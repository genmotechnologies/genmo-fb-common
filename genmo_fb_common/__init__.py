"""
GenMo Family Banking - Common Library

Shared utilities, base classes, and helpers for all GenMo services.
"""

__version__ = "1.3.2"

# Exports are loaded on first use, not at import: "genmo_fb_common.outbox" is a Django app,
# so Django imports this package while it is still loading apps, and importing a model
# module (BaseModel) at that point fails.
_EXPORTS = {
    "ServiceClient": ".clients",
    "EventPayload": ".events",
    "publish_event": ".events",
    "enqueue_event": ".events",
    "GenMoError": ".exceptions",
    "NotFoundError": ".exceptions",
    "PermissionDeniedError": ".exceptions",
    "ServiceUnavailableError": ".exceptions",
    "ValidationError": ".exceptions",
    "ActiveManager": ".managers",
    "IdentitySessionMiddleware": ".middleware",
    "BaseModel": ".models",
    "FamilyContext": ".ownership",
    "FamilyPermissionMissing": ".ownership",
    "FamilyUnavailable": ".ownership",
    "NotYourChild": ".ownership",
    "ensure_parent_permission": ".ownership",
    "ensure_self_or_own_child": ".ownership",
    "get_family_context": ".ownership",
}

__all__ = sorted(_EXPORTS)


def __getattr__(name):
    if name in _EXPORTS:
        from importlib import import_module

        return getattr(import_module(_EXPORTS[name], __name__), name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
