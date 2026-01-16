"""
HTTP client for service-to-service communication.
"""

import time
from typing import Any, Optional
from urllib.parse import urljoin

import httpx
import structlog
from django.conf import settings

from genmo_fb_common.exceptions import ServiceUnavailableError


logger = structlog.get_logger(__name__)


class ServiceClient:
    """
    Base HTTP client for calling other GenMo services.
    
    Features:
    - Automatic retries with exponential backoff
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
        
        # Forward request ID for tracing
        request_id = getattr(settings, 'CURRENT_REQUEST_ID', None)
        if request_id:
            headers["X-Request-ID"] = request_id
        
        return headers
    
    def _make_request(
        self,
        method: str,
        path: str,
        data: Optional[dict] = None,
        params: Optional[dict] = None
    ) -> dict:
        """
        Make HTTP request with retry logic.
        
        Args:
            method: HTTP method (GET, POST, PUT, DELETE)
            path: API endpoint path
            data: Request body (for POST/PUT)
            params: Query parameters (for GET)
            
        Returns:
            Response JSON as dict
            
        Raises:
            ServiceUnavailableError: If service is unreachable after retries
        """
        url = urljoin(self.base_url, path)
        headers = self._get_headers()
        
        last_exception = None
        
        for attempt in range(self.max_retries):
            try:
                logger.debug(
                    "service_request",
                    method=method,
                    url=url,
                    attempt=attempt + 1
                )
                
                response = self._client.request(
                    method=method,
                    url=url,
                    headers=headers,
                    json=data,
                    params=params
                )
                
                response.raise_for_status()
                
                logger.debug(
                    "service_response",
                    method=method,
                    url=url,
                    status=response.status_code
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
                        response=e.response.text
                    )
                    raise
                last_exception = e
                
            except (httpx.ConnectError, httpx.TimeoutException) as e:
                last_exception = e
                logger.warning(
                    "service_connection_error",
                    method=method,
                    url=url,
                    error=str(e),
                    attempt=attempt + 1
                )
            
            # Exponential backoff
            if attempt < self.max_retries - 1:
                sleep_time = self.retry_delay * (2 ** attempt)
                time.sleep(sleep_time)
        
        # All retries exhausted
        logger.error(
            "service_unavailable",
            method=method,
            url=url,
            error=str(last_exception)
        )
        raise ServiceUnavailableError(
            f"Service unavailable: {self.base_url}"
        )
    
    def get(self, path: str, params: Optional[dict] = None) -> dict:
        """Make GET request."""
        return self._make_request("GET", path, params=params)
    
    def post(self, path: str, data: Optional[dict] = None) -> dict:
        """Make POST request."""
        return self._make_request("POST", path, data=data)
    
    def put(self, path: str, data: Optional[dict] = None) -> dict:
        """Make PUT request."""
        return self._make_request("PUT", path, data=data)
    
    def patch(self, path: str, data: Optional[dict] = None) -> dict:
        """Make PATCH request."""
        return self._make_request("PATCH", path, data=data)
    
    def delete(self, path: str) -> dict:
        """Make DELETE request."""
        return self._make_request("DELETE", path)
    
    def __del__(self):
        """Clean up HTTP client."""
        if hasattr(self, '_client'):
            self._client.close()