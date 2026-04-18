"""
Django middleware for GenMo services.
"""

import hashlib
import threading
import uuid
from typing import Callable

import jwt
import structlog
from django.conf import settings
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.utils import timezone

logger = structlog.get_logger(__name__)


# Thread-local storage for request context
_request_context = threading.local()


def get_current_request_id() -> str:
    """Get the current request ID from thread-local storage."""
    return getattr(_request_context, "request_id", None)


def get_current_user_id() -> str:
    """Get the current user ID from thread-local storage."""
    return getattr(_request_context, "user_id", None)


class RequestIDMiddleware:
    """
    Middleware that adds a unique request ID to each request.

    - Checks for X-Request-ID header (forwarded from API gateway)
    - Generates new ID if not present
    - Adds ID to response headers
    - Stores in thread-local for logging/tracing

    Add to MIDDLEWARE in settings:
        'genmo_fb_common.middleware.RequestIDMiddleware',
    """

    def __init__(self, get_response: Callable):
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        # Get or generate request ID
        request_id = request.headers.get("X-Request-ID")
        if not request_id:
            request_id = str(uuid.uuid4())

        # Store in thread-local
        _request_context.request_id = request_id

        # Add to request for easy access
        request.request_id = request_id

        # Bind to structlog
        structlog.contextvars.bind_contextvars(request_id=request_id)

        # Process request
        response = self.get_response(request)

        # Add to response headers
        response["X-Request-ID"] = request_id

        # Clean up
        _request_context.request_id = None
        structlog.contextvars.unbind_contextvars("request_id")

        return response


class RequestLoggingMiddleware:
    """
    Middleware that logs all requests and responses.

    Add to MIDDLEWARE in settings (after RequestIDMiddleware):
        'genmo_fb_common.middleware.RequestLoggingMiddleware',
    """

    def __init__(self, get_response: Callable):
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        # Log request
        logger.info(
            "request_started",
            method=request.method,
            path=request.path,
            user_id=(
                getattr(request.user, "id", None) if hasattr(request, "user") else None
            ),
        )

        # Process request
        response = self.get_response(request)

        # Log response
        logger.info(
            "request_completed",
            method=request.method,
            path=request.path,
            status=response.status_code,
        )

        return response


class BaseSessionAuthMiddleware:
    """
    Common Middleware to authenticate sessions using JWT and Database.
    Inherit this in your service and provide the Session model.
    """

    def __init__(self, get_response, session_model):
        self.get_response = get_response
        self.session_model = session_model

    def __call__(self, request):
        auth_header = request.headers.get("Authorization")
        if not auth_header or not auth_header.startswith("Bearer "):
            return self.get_response(request)

        token = auth_header.split(" ")[1]
        try:
            # 1. Decode JWT
            jwt.decode(token, settings.SECRET_KEY, algorithms=["HS256"])
            token_hash = hashlib.sha256(token.encode()).hexdigest()

            # 2. Check Database Session

            session = self.session_model.objects.filter(token_hash=token_hash).first()

            if not session or not session.is_valid:
                return JsonResponse({"error": "Session expired or invalid"}, status=401)

            # 3. Attach User Context
            request.bank_customer_id = session.bank_customer_id
            request.bank_id = session.bank_id
            request.session_id = str(session.id)
            request.current_session = session

            # Update activity
            session.last_activity_at = timezone.now()
            session.save(update_fields=["last_activity_at"])

        except jwt.ExpiredSignatureError:
            return JsonResponse({"error": "Token has expired"}, status=401)
        except Exception:
            return JsonResponse({"error": "Invalid token"}, status=401)

        return self.get_response(request)


class IdentitySessionMiddleware:
    """
    Session validation middleware for services that don't own sessions.

    Validates tokens by calling identity-service's /api/v1/sessions/validate/ endpoint.
    Caches valid sessions in Redis to minimize HTTP calls.

    Settings required:
        IDENTITY_SERVICE_URL: URL of identity-service (default: http://identity-service:8001)
        CACHES: Django cache config with Redis backend

    On success, attaches to request:
        - bank_customer_id
        - bank_id
        - session_id
    """

    CACHE_TTL = 60  # seconds

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        auth_header = request.headers.get("Authorization", "")

        if auth_header.startswith("Bearer "):
            token = auth_header[7:]
            session_data = self._validate_session(token)

            if session_data:
                request.bank_customer_id = session_data["bank_customer_id"]
                request.bank_id = session_data["bank_id"]
                request.session_id = session_data["session_id"]
            else:
                request.bank_customer_id = None
                request.bank_id = None
                request.session_id = None
        else:
            request.bank_customer_id = None
            request.bank_id = None
            request.session_id = None

        return self.get_response(request)

    def _validate_session(self, token):
        import hashlib

        import httpx
        import structlog
        from django.conf import settings
        from django.core.cache import cache

        logger = structlog.get_logger(__name__)

        token_hash = hashlib.sha256(token.encode()).hexdigest()[:16]
        cache_key = f"session:{token_hash}"

        # Check cache first
        cached = cache.get(cache_key)
        if cached is not None:
            return cached if cached else None

        # Call identity-service
        identity_url = getattr(
            settings, "IDENTITY_SERVICE_URL", "http://identity-service:8001"
        )

        try:
            response = httpx.get(
                f"{identity_url}/api/v1/sessions/validate/",
                headers={"Authorization": f"Bearer {token}"},
                timeout=5.0,
            )

            if response.status_code == 200:
                data = response.json()
                cache.set(cache_key, data, self.CACHE_TTL)
                return data
            else:
                cache.set(cache_key, False, self.CACHE_TTL)
                return None

        except Exception as e:
            logger.warning("identity_service_unavailable", error=str(e))
            return None
