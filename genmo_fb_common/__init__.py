"""
GenMo Family Banking - Common Library

Shared utilities, base classes, and helpers for all GenMo services.
"""

__version__ = "1.2.1"

from .clients import ServiceClient
from .events import EventPayload, publish_event
from .exceptions import (
    GenMoError,
    NotFoundError,
    PermissionDeniedError,
    ServiceUnavailableError,
    ValidationError,
)
from .managers import ActiveManager
from .middleware import IdentitySessionMiddleware
from .models import BaseModel
from .ownership import (
    FamilyContext,
    FamilyPermissionMissing,
    FamilyUnavailable,
    NotYourChild,
    ensure_parent_permission,
    ensure_self_or_own_child,
    get_family_context,
)

__all__ = [
    "BaseModel",
    "ActiveManager",
    "ServiceClient",
    "publish_event",
    "EventPayload",
    "GenMoError",
    "ServiceUnavailableError",
    "ValidationError",
    "NotFoundError",
    "PermissionDeniedError",
    "IdentitySessionMiddleware",
    "FamilyContext",
    "NotYourChild",
    "FamilyPermissionMissing",
    "FamilyUnavailable",
    "get_family_context",
    "ensure_self_or_own_child",
    "ensure_parent_permission",
]
