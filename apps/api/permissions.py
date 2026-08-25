"""DRF permission classes.

Authorisation for the API mirrors the storefront exactly: customers can only
reach their own rows, and anything that changes the catalog, coupons or order
status is staff-only.
"""
from rest_framework import permissions

SAFE_METHODS = permissions.SAFE_METHODS


class IsStaffOrReadOnly(permissions.BasePermission):
    """Anyone may read; only staff may write.

    Used for the catalog: products and categories are public data, but
    editing them is an operations task.
    """

    message = "Only staff accounts can modify this resource."

    def has_permission(self, request, view):
        if request.method in SAFE_METHODS:
            return True
        return bool(request.user and request.user.is_authenticated and request.user.is_staff)


class IsStaff(permissions.BasePermission):
    """Staff-only, read included (customer lists, coupon management)."""

    message = "This endpoint is restricted to staff accounts."

    def has_permission(self, request, view):
        return bool(request.user and request.user.is_authenticated and request.user.is_staff)


class IsOwner(permissions.BasePermission):
    """Object-level ownership check.

    Works for models exposing ``user``, ``order.user``, ``cart.user`` or
    ``wishlist.user``. Staff bypass it so support can inspect a record.
    """

    message = "You do not have access to this record."

    def has_object_permission(self, request, view, obj):
        user = request.user
        if not (user and user.is_authenticated):
            return False
        if user.is_staff:
            return True
        return _owner_of(obj) == user


class IsOwnerOrReadOnly(IsOwner):
    """Public reads, owner-only writes -- used for reviews.

    ``has_permission`` gates creation as well: without it an anonymous POST
    would fall through to serializer validation and come back as a 400, when
    the honest answer is 401.
    """

    def has_permission(self, request, view):
        if request.method in SAFE_METHODS:
            return True
        return bool(request.user and request.user.is_authenticated)

    def has_object_permission(self, request, view, obj):
        if request.method in SAFE_METHODS:
            return True
        return super().has_object_permission(request, view, obj)


def _owner_of(obj):
    """Walk the common owner relationships and return the owning user."""
    for path in ("user", "order.user", "cart.user", "wishlist.user"):
        node = obj
        for attribute in path.split("."):
            node = getattr(node, attribute, None)
            if node is None:
                break
        else:
            return node
    return None
