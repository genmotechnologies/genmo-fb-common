"""
Django middleware for GenMo services.
"""

import threading
import uuid
from typing import Callable

import structlog
from django.http import HttpRequest, HttpResponse
import jwt
import hashlib
import structlog
from django.conf import settings
from django.http import JsonResponse
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
        auth_header = request.headers.get('Authorization')
        if not auth_header or not auth_header.startswith('Bearer '):
            return self.get_response(request)

        token = auth_header.split(' ')[1]
        try:
            # 1. Decode JWT
            payload = jwt.decode(token, settings.SECRET_KEY, algorithms=['HS256'])
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
            session.save(update_fields=['last_activity_at'])

        except jwt.ExpiredSignatureError:
            return JsonResponse({"error": "Token has expired"}, status=401)
        except Exception as e:
            return JsonResponse({"error": "Invalid token"}, status=401)

        return self.get_response(request)

