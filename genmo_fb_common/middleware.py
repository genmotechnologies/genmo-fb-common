"""
Django middleware for GenMo services.
"""

import threading
import uuid
from typing import Callable

import structlog
from django.http import HttpRequest, HttpResponse

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
