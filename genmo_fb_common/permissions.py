"""
DRF permission classes for GenMo services.
"""

from rest_framework import permissions


class IsParent(permissions.BasePermission):
    """
    Permission check for parent users.

    Usage in ViewSet:
        permission_classes = [IsAuthenticated, IsParent]
    """

    message = "Only parents can perform this action."

    def has_permission(self, request, view):
        return (
            request.user
            and request.user.is_authenticated
            and getattr(request.user, "role", None) == "parent"
        )


class IsChild(permissions.BasePermission):
    """Permission check for child users."""

    message = "Only children can perform this action."

    def has_permission(self, request, view):
        return (
            request.user
            and request.user.is_authenticated
            and getattr(request.user, "role", None) == "child"
        )


class IsParentOrReadOnly(permissions.BasePermission):
    """
    Parents can do anything, children can only read.
    """

    def has_permission(self, request, view):
        if request.method in permissions.SAFE_METHODS:
            return request.user and request.user.is_authenticated
        return (
            request.user
            and request.user.is_authenticated
            and getattr(request.user, "role", None) == "parent"
        )


class IsFamilyMember(permissions.BasePermission):
    """
    Check if user is a member of the family being accessed.

    Requires the view to have a `get_family_id()` method or
    a `family_id` in URL kwargs.
    """

    message = "You must be a member of this family."

    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False

        # Get family_id from view
        family_id = view.kwargs.get("family_id")
        if not family_id and hasattr(view, "get_family_id"):
            family_id = view.get_family_id()

        if not family_id:
            return False

        # Check if user is member of this family
        # This assumes user has family_ids attribute
        user_family_ids = getattr(request.user, "family_ids", [])
        return str(family_id) in [str(fid) for fid in user_family_ids]


class IsOwnerOrParent(permissions.BasePermission):
    """
    Object-level permission: Owner or parent can access.

    Usage:
        class GoalViewSet(viewsets.ModelViewSet):
            permission_classes = [IsAuthenticated, IsOwnerOrParent]

            # Must implement get_owner_id on the model or serializer
    """

    def has_object_permission(self, request, view, obj):
        # Owner can always access
        owner_id = getattr(obj, "bank_customer_id", None) or getattr(
            obj, "created_by_id", None
        )
        if str(owner_id) == str(request.user.bank_customer_id):
            return True

        # Parents can access children's resources
        if getattr(request.user, "role", None) == "parent":
            # Check if owner is child in same family
            # This requires additional logic based on your user model
            return True

        return False
