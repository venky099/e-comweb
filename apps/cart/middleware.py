"""Cart middleware."""
import logging

from .models import Cart
from .services import GUEST_CART_SESSION_KEY, merge_carts

logger = logging.getLogger("ecommerce")


class CartMergeMiddleware:
    """Folds a guest cart into the user's cart after they sign in.

    ``django.contrib.auth.login`` cycles the session *key* but keeps the
    session *data*, so the guest cart id stashed by ``services.get_cart``
    survives the login and can be picked up on the next request.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        self._merge_if_pending(request)
        return self.get_response(request)

    def _merge_if_pending(self, request):
        user = getattr(request, "user", None)
        if user is None or not user.is_authenticated:
            return

        session = getattr(request, "session", None)
        if session is None:
            return

        guest_cart_id = session.pop(GUEST_CART_SESSION_KEY, None)
        if not guest_cart_id:
            return

        try:
            guest_cart = Cart.objects.filter(pk=guest_cart_id, user__isnull=True).first()
            if guest_cart is None:
                return
            user_cart, _created = Cart.objects.get_or_create(user=user)
            merge_carts(guest_cart, user_cart)
        except Exception:  # never break a login over a cart merge
            logger.exception("Cart merge failed for user %s", user.pk)
