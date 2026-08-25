"""Header cart/wishlist badge context."""
from .services import get_cart


def cart_summary(request):
    """Item counts for the header, without creating an empty guest cart.

    Passing ``create=False`` keeps anonymous browsing from writing a Cart row
    (and a session) for every visitor who never adds anything.
    """
    path = request.path
    if path.startswith("/api/"):
        return {}

    cart = get_cart(request, create=False)
    count = 0
    if cart is not None:
        count = sum(item.quantity for item in cart.items.all())

    wishlist_count = 0
    user = getattr(request, "user", None)
    if user is not None and user.is_authenticated:
        from apps.wishlist.models import WishlistItem

        wishlist_count = WishlistItem.objects.filter(wishlist__user=user).count()

    return {
        "header_cart": cart,
        "header_cart_count": count,
        "header_wishlist_count": wishlist_count,
    }
