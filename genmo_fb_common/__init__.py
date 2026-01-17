"""
GenMo Family Banking - Common Library

Shared utilities, base classes, and helpers for all GenMo services.
"""

__version__ = "1.0.0"

from .models import BaseModel
from .managers import ActiveManager
from .clients import ServiceClient
from .events import publish_event, EventPayload
from .exceptions import (
    GenMoException,
    ServiceUnavailableError,
    ValidationError,
    NotFoundError,
    PermissionDeniedError,
)

__all__ = [
    "BaseModel",
    "ActiveManager",
    "ServiceClient",
    "publish_event",
    "EventPayload",
    "GenMoException",
    "ServiceUnavailableError",
    "ValidationError",
    "NotFoundError",
    "PermissionDeniedError",
]