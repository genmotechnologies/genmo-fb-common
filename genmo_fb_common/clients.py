"""
HTTP client for service-to-service communication.
"""

import time
from typing import Optional
from urllib.parse import urljoin

import httpx
import structlog

from genmo_fb_common.exceptions import ServiceUnavailableError
from genmo_fb_common.middleware import get_current_request_id
from genmo_fb_common.tracing import outgoing_traceparent

logger = structlog.get_logger(__name__)

# Reads change nothing, so asking again is always safe.
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
# Failures where the request never reached the other service, so asking again cannot do
# the work twice. A read timeout is not one of them: the work may have been done.
NEVER_SENT = (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout)


def _error_code(response: "httpx.Response") -> Optional[str]:
    """
    The machine-readable code of another service's error answer, for the log: the
    platform's `{"error": {"code": ...}}` body, or a flat `{"code": ...}`. Never the
    message and never the rest of the body. None when the answer has no such code.
    """
    try:
        body = response.json()
    except ValueError:
        return None
    if not isinstance(body, dict):
        return None
    error = body.get("error")
    code = error.get("code") if isinstance(error, dict) else body.get("code")
    # a code is a short token; anything else is not one and is not logged
    if isinstance(code, str) and len(code) <= 64 and code.replace("_", "").isalnum():
        return code
    return None


class ServiceClient:
    """
    Base HTTP client for calling other GenMo services.

    Features:
    - Automatic retries with exponential backoff, only when asking again is safe: a read
      (GET), a call carrying an `idempotency_key`, or a failure before the request left
      this service (the connection was refused). A write without a key is never repeated
      after a timeout or a 5xx answer, because the other service may already have done it.
    - Request/response logging
    - Timeout handling
    - JWT token forwarding

    Usage:
        class LimitsClient(ServiceClient):
            base_url = settings.LIMITS_SERVICE_URL

            def validate_transaction(self, child_id: str, amount: str) -> dict:
                return self.post('/api/v1/limits/validate/', {
                    'child_id': child_id,
                    'amount': amount
                })
    """

    base_url: str = ""
    timeout: float = 30.0
    max_retries: int = 3
    retry_delay: float = 1.0

    def __init__(self, auth_token: Optional[str] = None):
        """
        Initialize client.

        Args:
            auth_token: JWT token for authentication. If not provided,
                       will try to get from thread-local storage.
        """
        self.auth_token = auth_token
        self._client = httpx.Client(timeout=self.timeout)

    def _get_headers(self) -> dict:
        """Build request headers."""
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

        if self.auth_token:
            headers["Authorization"] = f"Bearer {self.auth_token}"

        # Forward the request ID RequestIDMiddleware set for this request, so one
        # call can be followed across services
        request_id = get_current_request_id()
        if request_id:
            headers["X-Request-ID"] = request_id

        # Forward the trace (FC-08): the same trace ID, a new span for this call
        traceparent = outgoing_traceparent()
        if traceparent:
            headers["traceparent"] = traceparent

        return headers

    def _make_request(
        self,
        method: str,
        path: str,
        data: Optional[dict] = None,
        params: Optional[dict] = None,
        idempotency_key: Optional[str] = None,
    ) -> dict:
        """
        Make HTTP request with retry logic.

        Args:
            method: HTTP method (GET, POST, PUT, DELETE)
            path: API endpoint path
            data: Request body (for POST/PUT)
            params: Query parameters (for GET)
            idempotency_key: sent as the Idempotency-Key header. The other service
                answers a repeat of the same key with the first answer, so the call
                may be retried like a read.

        Returns:
            Response JSON as dict

        Raises:
            ServiceUnavailableError: If service is unreachable after retries
        """
        url = urljoin(self.base_url, path)
        headers = self._get_headers()
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        repeatable = method.upper() in SAFE_METHODS or bool(idempotency_key)

        last_exception = None

        for attempt in range(self.max_retries):
            try:
                logger.debug(
                    "service_request", method=method, url=url, attempt=attempt + 1
                )

                response = self._client.request(
                    method=method, url=url, headers=headers, json=data, params=params
                )

                response.raise_for_status()

                logger.debug(
                    "service_response",
                    method=method,
                    url=url,
                    status=response.status_code,
                )

                return response.json()

            except httpx.HTTPStatusError as e:
                # Don't retry client errors (4xx)
                if 400 <= e.response.status_code < 500:
                    logger.warning(
                        "service_client_error",
                        method=method,
                        url=url,
                        status=e.response.status_code,
                        # the error code only: the body can carry what the caller
                        # sent (a card number, a phone number), and logs never do
                        error_code=_error_code(e.response),
                    )
                    raise
                last_exception = e
                if not repeatable:
                    logger.warning(
                        "service_error_not_retried",
                        method=method,
                        url=url,
                        status=e.response.status_code,
                    )
                    break

            except (httpx.ConnectError, httpx.TimeoutException) as e:
                last_exception = e
                logger.warning(
                    "service_connection_error",
                    method=method,
                    url=url,
                    error=str(e),
                    attempt=attempt + 1,
                )
                if not repeatable and not isinstance(e, NEVER_SENT):
                    # it may have been done: the caller decides, not this client
                    break

            # Exponential backoff
            if attempt < self.max_retries - 1:
                sleep_time = self.retry_delay * (2**attempt)
                time.sleep(sleep_time)

        # All retries exhausted
        logger.error(
            "service_unavailable", method=method, url=url, error=str(last_exception)
        )
        raise ServiceUnavailableError(f"Service unavailable: {self.base_url}")

    def get(self, path: str, params: Optional[dict] = None) -> dict:
        """Make GET request."""
        return self._make_request("GET", path, params=params)

    def post(
        self,
        path: str,
        data: Optional[dict] = None,
        *,
        idempotency_key: Optional[str] = None,
    ) -> dict:
        """Make POST request."""
        return self._make_request(
            "POST", path, data=data, idempotency_key=idempotency_key
        )

    def put(
        self,
        path: str,
        data: Optional[dict] = None,
        *,
        idempotency_key: Optional[str] = None,
    ) -> dict:
        """Make PUT request."""
        return self._make_request(
            "PUT", path, data=data, idempotency_key=idempotency_key
        )

    def patch(
        self,
        path: str,
        data: Optional[dict] = None,
        *,
        idempotency_key: Optional[str] = None,
    ) -> dict:
        """Make PATCH request."""
        return self._make_request(
            "PATCH", path, data=data, idempotency_key=idempotency_key
        )

    def delete(self, path: str, *, idempotency_key: Optional[str] = None) -> dict:
        """Make DELETE request."""
        return self._make_request("DELETE", path, idempotency_key=idempotency_key)

    def __del__(self):
        """Clean up HTTP client."""
        if hasattr(self, "_client"):
            self._client.close()
