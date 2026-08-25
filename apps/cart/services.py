"""Cart service layer.

Views and API viewsets call these functions; none of them trust a price, a
total or a stock figure supplied by the caller.
"""
import logging

from django.conf import settings
from django.db import transaction
from django.utils.translation import gettext_lazy as _

from .models import Cart, CartItem

logger = logging.getLogger("ecommerce")

#: Session key holding the guest cart id, so the cart survives the session-key
#: rotation Django performs at login and can be merged afterwards.
GUEST_CART_SESSION_KEY = "guest_cart_id"


class CartError(Exception):
    """Raised when a cart operation cannot be satisfied (stock, limits...)."""


def _ensure_session(request):
    if not request.session.session_key:
        request.session.create()
    return request.session.session_key


def get_cart(request, create=True):
    """Return the cart for this request.

    Logged-in users get their database cart; guests get one keyed to the
    session. Returns ``None`` when ``create`` is False and none exists.
    """
    user = getattr(request, "user", None)

    if user is not None and user.is_authenticated:
        if create:
            cart, _created = Cart.objects.get_or_create(user=user, defaults={"is_active": True})
            return cart
        return Cart.objects.filter(user=user).first()

    session_key = request.session.session_key
    if not session_key:
        if not create:
            return None
        session_key = _ensure_session(request)

    if create:
        cart, _created = Cart.objects.get_or_create(
            session_key=session_key, user__isnull=True, defaults={"is_active": True}
        )
        # Remembered by id, not by session key: login rotates the key but
        # keeps the data, so this is what survives to the merge.
        request.session[GUEST_CART_SESSION_KEY] = cart.pk
        return cart
    return Cart.objects.filter(session_key=session_key, user__isnull=True).first()


@transaction.atomic
def add_to_cart(request, variant, quantity=1):
    """Add (or top up) a variant in the cart, clamped to real stock.

    Returns ``(cart_item, message)``. Raises ``CartError`` when nothing can be
    added at all.
    """
    quantity = max(int(quantity), 1)

    if not variant.is_active or not variant.product.is_active:
        raise CartError(_("This product is not available right now."))

    available = variant.available_quantity
    if available <= 0:
        raise CartError(_("This item is out of stock."))

    cart = get_cart(request)
    item = CartItem.objects.select_for_update().filter(cart=cart, variant=variant).first()

    ceiling = min(available, settings.MAX_CART_QUANTITY_PER_ITEM)
    current = item.quantity if item else 0
    requested = current + quantity
    final = min(requested, ceiling)

    if final <= current:
        if current >= settings.MAX_CART_QUANTITY_PER_ITEM:
            raise CartError(
                _("You can order at most %(n)d of this item.")
                % {"n": settings.MAX_CART_QUANTITY_PER_ITEM}
            )
        raise CartError(_("Only %(n)d left in stock.") % {"n": available})

    if item:
        item.quantity = final
        item.save(update_fields=["quantity", "updated_at"])
    else:
        item = CartItem.objects.create(cart=cart, variant=variant, quantity=final)

    message = _("Added to your cart.")
    if final < requested:
        message = _("Added %(n)d - that is all we have in stock.") % {"n": final - current}
    return item, message


@transaction.atomic
def update_quantity(request, item_id, quantity):
    """Set an exact quantity. Zero (or less) removes the row."""
    cart = get_cart(request)
    item = CartItem.objects.select_for_update().filter(cart=cart, pk=item_id).first()
    if item is None:
        raise CartError(_("That item is no longer in your cart."))

    quantity = int(quantity)
    if quantity <= 0:
        item.delete()
        return None, _("Item removed from your cart.")

    ceiling = min(item.variant.available_quantity, settings.MAX_CART_QUANTITY_PER_ITEM)
    if ceiling <= 0:
        item.delete()
        raise CartError(_("That item just went out of stock and was removed."))

    message = _("Cart updated.")
    if quantity > ceiling:
        quantity = ceiling
        message = _("Only %(n)d available - quantity adjusted.") % {"n": ceiling}

    item.quantity = quantity
    item.save(update_fields=["quantity", "updated_at"])
    return item, message


def remove_item(request, item_id):
    cart = get_cart(request)
    deleted, _details = CartItem.objects.filter(cart=cart, pk=item_id).delete()
    if not deleted:
        raise CartError(_("That item is no longer in your cart."))
    return _("Item removed from your cart.")


@transaction.atomic
def merge_carts(session_cart, user_cart):
    """Fold a guest cart into the user's cart at login.

    Quantities are summed and then clamped to stock; the guest cart is deleted
    afterwards so it cannot be merged twice.
    """
    if session_cart is None or user_cart is None or session_cart.pk == user_cart.pk:
        return user_cart

    existing = {item.variant_id: item for item in user_cart.items.select_for_update()}

    for guest_item in session_cart.items.select_related("variant__inventory"):
        ceiling = min(
            guest_item.variant.available_quantity, settings.MAX_CART_QUANTITY_PER_ITEM
        )
        if ceiling <= 0:
            continue
        target = existing.get(guest_item.variant_id)
        if target:
            target.quantity = min(target.quantity + guest_item.quantity, ceiling)
            target.save(update_fields=["quantity", "updated_at"])
        else:
            CartItem.objects.create(
                cart=user_cart,
                variant=guest_item.variant,
                quantity=min(guest_item.quantity, ceiling),
            )

    # Carry over a coupon the guest had applied, if the user has none.
    if session_cart.coupon_id and not user_cart.coupon_id:
        user_cart.coupon_id = session_cart.coupon_id
        user_cart.save(update_fields=["coupon", "updated_at"])

    session_cart.delete()
    logger.info("Merged guest cart into user cart %s", user_cart.pk)
    return user_cart


def clamp_cart_to_stock(cart):
    """Trim every row to what is purchasable. Returns the adjusted rows."""
    adjusted = []
    for item in list(cart.live_items()):
        before = item.quantity
        if item.clamp_quantity():
            adjusted.append((item, before))
    return adjusted
