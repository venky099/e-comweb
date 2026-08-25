"""Coupon apply/remove endpoints used by the cart and checkout pages."""
from django.contrib import messages
from django.shortcuts import redirect, render
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_GET, require_POST

from apps.cart.services import get_cart

from . import services


def _is_htmx(request):
    return request.headers.get("HX-Request") == "true"


def _summary_response(request, redirect_to):
    if _is_htmx(request):
        cart = get_cart(request)
        return render(
            request,
            "cart/partials/order_summary.html",
            {"cart": cart, "summary": cart.as_summary()},
        )
    return redirect(redirect_to)


@require_POST
def apply_coupon(request):
    """Validate a posted code server-side and attach it to the cart."""
    cart = get_cart(request)
    redirect_to = request.POST.get("next") or "cart:detail"

    try:
        coupon, discount = services.apply_to_cart(
            cart, request.POST.get("code"), user=request.user
        )
        messages.success(
            request,
            _("Coupon %(code)s applied - you saved %(amount)s.")
            % {"code": coupon.code, "amount": f"{discount:.2f}"},
        )
    except services.CouponError as exc:
        messages.error(request, str(exc))

    return _summary_response(request, redirect_to)


@require_POST
def remove_coupon(request):
    cart = get_cart(request)
    services.remove_from_cart(cart)
    messages.info(request, _("Coupon removed."))
    return _summary_response(request, request.POST.get("next") or "cart:detail")


@require_GET
def available_coupons(request):
    """Offers drawer -- shows which codes this cart currently qualifies for."""
    cart = get_cart(request, create=False)
    total = cart.subtotal if cart else 0
    return render(
        request,
        "coupons/partials/available.html",
        {"coupons": services.available_for_user(request.user, cart_total=total)},
    )
