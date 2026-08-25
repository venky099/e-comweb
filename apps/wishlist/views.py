"""Wishlist views. All require a signed-in customer."""
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_POST
from django.views.generic import ListView

from apps.cart.services import CartError, add_to_cart
from apps.catalog.models import Product, ProductVariant

from .models import Wishlist, WishlistItem


def _is_htmx(request):
    return request.headers.get("HX-Request") == "true"


class WishlistView(LoginRequiredMixin, ListView):
    template_name = "wishlist/detail.html"
    context_object_name = "items"
    paginate_by = 24

    def get_queryset(self):
        return Wishlist.for_user(self.request.user).live_items()


@login_required
@require_POST
def toggle(request):
    """Add or remove a product -- the heart button on every product card."""
    product_id = request.POST.get("product_id")
    variant_id = request.POST.get("variant_id") or None

    product = get_object_or_404(Product.objects.published(), pk=product_id)
    wishlist = Wishlist.for_user(request.user)

    existing = wishlist.items.filter(product=product).first()
    if existing:
        existing.delete()
        added = False
        messages.info(request, _("Removed from your wishlist."))
    else:
        variant = None
        if variant_id:
            variant = ProductVariant.objects.filter(pk=variant_id, product=product).first()
        WishlistItem.objects.create(wishlist=wishlist, product=product, variant=variant)
        added = True
        messages.success(request, _("Saved to your wishlist."))

    if _is_htmx(request):
        return render(
            request,
            "wishlist/partials/toggle_button.html",
            {"product": product, "in_wishlist": added},
        )
    return redirect(request.META.get("HTTP_REFERER", reverse("wishlist:detail")))


@login_required
@require_POST
def remove(request, item_id):
    item = get_object_or_404(WishlistItem, pk=item_id, wishlist__user=request.user)
    item.delete()
    messages.success(request, _("Removed from your wishlist."))

    if _is_htmx(request):
        wishlist = Wishlist.for_user(request.user)
        return render(
            request,
            "wishlist/partials/wishlist_body.html",
            {"items": wishlist.live_items()},
        )
    return redirect("wishlist:detail")


@login_required
@require_POST
def move_to_cart(request, item_id):
    """Move one saved item into the cart, stock permitting."""
    item = get_object_or_404(
        WishlistItem.objects.select_related("product", "variant"),
        pk=item_id,
        wishlist__user=request.user,
    )
    variant = item.movable_variant
    if variant is None:
        messages.error(request, _("This product is not available right now."))
        return redirect("wishlist:detail")

    try:
        add_to_cart(request, variant, 1)
    except CartError as exc:
        messages.error(request, str(exc))
        return redirect("wishlist:detail")

    item.delete()
    messages.success(request, _("Moved to your cart."))
    return redirect("cart:detail")


@login_required
@require_POST
def move_all_to_cart(request):
    """Bulk move -- skips anything out of stock and reports the count."""
    wishlist = Wishlist.for_user(request.user)
    moved, skipped = 0, 0

    for item in list(wishlist.live_items()):
        variant = item.movable_variant
        if variant is None:
            skipped += 1
            continue
        try:
            add_to_cart(request, variant, 1)
        except CartError:
            skipped += 1
            continue
        item.delete()
        moved += 1

    if moved:
        messages.success(request, _("Moved %(n)d item(s) to your cart.") % {"n": moved})
    if skipped:
        messages.warning(
            request, _("%(n)d item(s) could not be moved (out of stock).") % {"n": skipped}
        )
    if not moved and not skipped:
        messages.info(request, _("Your wishlist is empty."))
    return redirect("cart:detail" if moved else "wishlist:detail")


@login_required
@require_POST
def clear(request):
    Wishlist.for_user(request.user).items.all().delete()
    messages.success(request, _("Wishlist cleared."))
    return redirect("wishlist:detail")
