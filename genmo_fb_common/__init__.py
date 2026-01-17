"""
GenMo Family Banking - Common Library

Shared utilities, base classes, and helpers for all GenMo services.
"""

__version__ = "1.0.0"

from .clients import ServiceClient
from .events import EventPayload, publish_event
from .exceptions import GenMoError  # Changed from GenMoException
from .exceptions import (
    NotFoundError,
    PermissionDeniedError,
    ServiceUnavailableError,
    ValidationError,
)
from .managers import ActiveManager
from .models import BaseModel

__all__ = [
    "BaseModel",
    "ActiveManager",
    "ServiceClient",
    "publish_event",
    "EventPayload",
    "GenMoError",  # Changed from GenMoException
    "ServiceUnavailableError",
    "ValidationError",
    "NotFoundError",
    "PermissionDeniedError",
]
