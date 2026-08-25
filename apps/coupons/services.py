"""Coupon application and redemption.

The browser only ever sends a *code*. Validity, eligibility and the discount
value are decided here, both when the code is applied and again when the order
is placed.
"""
import logging

from django.db import transaction
from django.db.models import F
from django.utils.translation import gettext_lazy as _

from .models import Coupon, CouponUsage

logger = logging.getLogger("ecommerce")


class CouponError(Exception):
    """Raised when a coupon cannot be applied."""


def find_coupon(code):
    code = (code or "").strip().upper()
    if not code:
        raise CouponError(_("Enter a coupon code."))
    coupon = Coupon.objects.filter(code=code).first()
    if coupon is None:
        raise CouponError(_("That coupon code is not valid."))
    return coupon


def validate_for_cart(coupon, cart, user=None):
    """Raise ``CouponError`` unless the coupon applies to this cart right now."""
    if cart.is_empty:
        raise CouponError(_("Add something to your cart before applying a coupon."))

    items = list(cart.live_items())
    is_valid, reason = coupon.check_validity(
        user=user if user is not None else cart.user,
        cart_total=cart.subtotal,
        cart_items=items,
    )
    if not is_valid:
        raise CouponError(reason)

    discount = coupon.discount_for(cart.subtotal, cart_items=items)
    if discount <= 0 and not coupon.gives_free_shipping():
        raise CouponError(_("This coupon gives no discount on your current cart."))
    return discount


def apply_to_cart(cart, code, user=None):
    """Attach a validated coupon to the cart. Returns the discount value."""
    coupon = find_coupon(code)
    discount = validate_for_cart(coupon, cart, user=user)
    cart.coupon = coupon
    cart.save(update_fields=["coupon", "updated_at"])
    return coupon, discount


def remove_from_cart(cart):
    cart.coupon = None
    cart.save(update_fields=["coupon", "updated_at"])


@transaction.atomic
def redeem(coupon, user, order, discount_amount):
    """Record a redemption and bump the usage counter.

    Called inside the order-placement transaction, so a failed order never
    burns a coupon use.
    """
    if coupon is None:
        return None

    locked = Coupon.objects.select_for_update().get(pk=coupon.pk)
    if locked.usage_limit is not None and locked.used_count >= locked.usage_limit:
        raise CouponError(_("This coupon has just reached its usage limit."))

    usage = CouponUsage.objects.create(
        coupon=locked,
        user=user,
        order=order,
        discount_amount=discount_amount,
    )
    Coupon.objects.filter(pk=locked.pk).update(used_count=F("used_count") + 1)
    logger.info("Coupon %s redeemed by user %s on order %s", locked.code, user.pk, order.pk)
    return usage


@transaction.atomic
def revoke(order):
    """Give a coupon use back when an order is cancelled before fulfilment."""
    usages = list(CouponUsage.objects.select_related("coupon").filter(order=order))
    for usage in usages:
        Coupon.objects.filter(pk=usage.coupon_id, used_count__gt=0).update(
            used_count=F("used_count") - 1
        )
    CouponUsage.objects.filter(order=order).delete()
    return len(usages)


def available_for_user(user, cart_total=0, limit=10):
    """Public coupons this user could actually use, for the 'offers' drawer."""
    coupons = Coupon.objects.public().order_by("min_order_value")[: limit * 2]
    usable = []
    for coupon in coupons:
        is_valid, reason = coupon.check_validity(user=user, cart_total=cart_total)
        usable.append({"coupon": coupon, "is_valid": is_valid, "reason": reason})
        if len(usable) >= limit:
            break
    return usable
