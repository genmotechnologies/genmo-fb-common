"""
DRF permission classes for GenMo services.

The caller comes from IdentitySessionMiddleware (`request.bank_customer_id`); their role and
family come from family-service through `genmo_fb_common.ownership` (one call per request at
most, none when a caller only reaches their own data). FC-06: the old classes read
`request.user.role` / `request.user.family_ids`, which nothing sets, and IsOwnerOrParent let
any parent through.
"""

from rest_framework import permissions

from genmo_fb_common.ownership import ensure_self_or_own_child, get_family_context


def _signed_in(request) -> bool:
    return bool(getattr(request, "bank_customer_id", None))


class IsParent(permissions.BasePermission):
    """
    Permission check for parent users.

    Usage in ViewSet:
        permission_classes = [IsParent]
    """

    message = "Only parents can perform this action."

    def has_permission(self, request, view):
        return _signed_in(request) and get_family_context(request).is_parent


class IsChild(permissions.BasePermission):
    """Permission check for child users."""

    message = "Only children can perform this action."

    def has_permission(self, request, view):
        return _signed_in(request) and get_family_context(request).is_child


class IsParentOrReadOnly(permissions.BasePermission):
    """
    Parents can do anything, children can only read.
    """

    message = "Only parents can perform this action."

    def has_permission(self, request, view):
        if not _signed_in(request):
            return False
        if request.method in permissions.SAFE_METHODS:
            return True
        return get_family_context(request).is_parent


class IsFamilyMember(permissions.BasePermission):
    """
    Check if user is a member of the family being accessed.

    Requires the view to have a `get_family_id()` method or
    a `family_id` in URL kwargs.
    """

    message = "You must be a member of this family."

    def has_permission(self, request, view):
        if not _signed_in(request):
            return False

        family_id = view.kwargs.get("family_id")
        if not family_id and hasattr(view, "get_family_id"):
            family_id = view.get_family_id()
        if not family_id:
            return False

        context = get_family_context(request)
        return context.family_id is not None and str(context.family_id) == str(
            family_id
        )


class IsSelfOrOwnChild(permissions.BasePermission):
    """
    Object-level (FC-06): the object belongs to the caller, or to a child of the calling
    parent's own family; anybody else gets 403 NOT_YOUR_CHILD.

    The owner is `view.get_owner_bank_customer_id(obj)` when the view has it, otherwise
    `obj.bank_customer_id`.

    Usage:
        class GoalViewSet(viewsets.ModelViewSet):
            permission_classes = [IsSelfOrOwnChild]
    """

    message = "Authentication credentials were not provided or are invalid."

    def has_permission(self, request, view):
        return _signed_in(request)

    def owner_id(self, view, obj):
        if hasattr(view, "get_owner_bank_customer_id"):
            return view.get_owner_bank_customer_id(obj)
        return getattr(obj, "bank_customer_id", None)

    def has_object_permission(self, request, view, obj):
        owner = self.owner_id(view, obj)
        if not owner:
            return False
        ensure_self_or_own_child(
            request, bank_customer_id=owner
        )  # raises 403 otherwise
        return True


class IsOwnerOrParent(IsSelfOrOwnChild):
    """
    Object-level permission: the owner, or a parent of the owner's own family (FC-06: it
    used to let any parent through). The owner is `obj.bank_customer_id`, or
    `obj.created_by_id` for objects without one.
    """

    def owner_id(self, view, obj):
        if hasattr(view, "get_owner_bank_customer_id"):
            return view.get_owner_bank_customer_id(obj)
        return getattr(obj, "bank_customer_id", None) or getattr(
            obj, "created_by_id", None
        )
