"""Cart views.

Each mutating view answers twice: a full redirect for plain form posts, and an
HTMX partial when the request comes from the inline cart controls.
"""
from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_POST
from django.views.generic import TemplateView

from apps.catalog.models import ProductVariant

from .services import CartError, add_to_cart, clamp_cart_to_stock, get_cart, remove_item, update_quantity


def _is_htmx(request):
    return request.headers.get("HX-Request") == "true"


def _cart_response(request, redirect_to="cart:detail"):
    """Render the cart partial for HTMX, otherwise redirect."""
    if _is_htmx(request):
        cart = get_cart(request)
        return render(
            request,
            "cart/partials/cart_body.html",
            {"cart": cart, "items": cart.live_items(), "summary": cart.as_summary()},
        )
    return redirect(redirect_to)


class CartDetailView(TemplateView):
    """The cart page."""

    template_name = "cart/detail.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        cart = get_cart(self.request)

        # Quietly trim rows that outran their stock while the cart sat idle.
        adjusted = clamp_cart_to_stock(cart)
        for item, before in adjusted:
            messages.warning(
                self.request,
                _("%(name)s was reduced from %(before)d to what we have in stock.")
                % {"name": item.variant.product.name, "before": before},
            )

        context["cart"] = cart
        context["items"] = cart.live_items()
        context["summary"] = cart.as_summary()
        context["stock_problems"] = cart.stock_problems()
        return context


@require_POST
def add(request):
    """Add a variant to the cart."""
    variant_id = request.POST.get("variant_id")
    quantity = request.POST.get("quantity", 1)

    variant = get_object_or_404(
        ProductVariant.objects.select_related("product", "inventory"), pk=variant_id
    )

    try:
        _item, message = add_to_cart(request, variant, quantity)
        messages.success(request, message)
    except CartError as exc:
        messages.error(request, str(exc))
        if _is_htmx(request):
            return _cart_response(request)
        return redirect(request.META.get("HTTP_REFERER", reverse("cart:detail")))

    if request.POST.get("buy_now"):
        return redirect("orders:checkout_address")

    if _is_htmx(request):
        return render(
            request,
            "cart/partials/cart_badge.html",
            {"cart": get_cart(request), "added": True},
        )
    return redirect(request.META.get("HTTP_REFERER", reverse("cart:detail")))


@require_POST
def update(request, item_id):
    """Set an exact quantity for one row."""
    try:
        _item, message = update_quantity(request, item_id, request.POST.get("quantity", 1))
        messages.success(request, message)
    except CartError as exc:
        messages.error(request, str(exc))
    return _cart_response(request)


@require_POST
def increment(request, item_id):
    cart = get_cart(request)
    item = cart.items.filter(pk=item_id).first()
    current = item.quantity if item else 0
    try:
        _item, message = update_quantity(request, item_id, current + 1)
        messages.success(request, message)
    except CartError as exc:
        messages.error(request, str(exc))
    return _cart_response(request)


@require_POST
def decrement(request, item_id):
    cart = get_cart(request)
    item = cart.items.filter(pk=item_id).first()
    current = item.quantity if item else 0
    try:
        _item, message = update_quantity(request, item_id, current - 1)
        messages.success(request, message)
    except CartError as exc:
        messages.error(request, str(exc))
    return _cart_response(request)


@require_POST
def remove(request, item_id):
    try:
        messages.success(request, remove_item(request, item_id))
    except CartError as exc:
        messages.error(request, str(exc))
    return _cart_response(request)


@require_POST
def clear(request):
    cart = get_cart(request)
    cart.clear()
    messages.success(request, _("Your cart is now empty."))
    return _cart_response(request)


def mini_cart(request):
    """Header dropdown contents (HTMX)."""
    cart = get_cart(request, create=False)
    items = cart.live_items()[:5] if cart else []
    return render(
        request,
        "cart/partials/mini_cart.html",
        {
            "cart": cart,
            "items": items,
            "summary": cart.as_summary() if cart else None,
        },
    )
