"""
GenMo Family Banking - Common Library

Shared utilities, base classes, and helpers for all GenMo services.
"""

__version__ = "1.0.0"

from genmo_fb_common.models import BaseModel
from genmo_fb_common.managers import ActiveManager
from genmo_fb_common.clients import ServiceClient
from genmo_fb_common.events import publish_event, EventPayload
from genmo_fb_common.exceptions import (
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