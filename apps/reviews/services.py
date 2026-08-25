"""Review eligibility rules.

A review is only accepted from someone who actually received the product, so
``verified_purchase`` is derived here rather than being a field a client can
set.
"""
from django.utils.translation import gettext_lazy as _

from apps.orders.models import Order, OrderItem

from .models import Review


def delivered_order_item(user, product):
    """The delivered line entitling ``user`` to review ``product``, if any."""
    return (
        OrderItem.objects.filter(
            order__user=user,
            order__status__in=[Order.Status.DELIVERED, Order.Status.RETURN_REQUESTED],
            product=product,
        )
        .exclude(status=OrderItem.ItemStatus.CANCELLED)
        .select_related("order")
        .order_by("-order__delivered_at")
        .first()
    )


def can_review_product(user, product):
    """Return ``(allowed, reason)``.

    Enforced identically by the storefront form and the API serializer.
    """
    if not user.is_authenticated:
        return False, _("Please sign in to write a review.")

    if Review.objects.filter(product=product, user=user).exists():
        return False, _("You have already reviewed this product.")

    if delivered_order_item(user, product) is None:
        return False, _("Only customers who received this product can review it.")

    return True, ""


def create_review(user, product, rating, title="", comment="", images=None):
    """Create a verified review after re-checking eligibility."""
    allowed, reason = can_review_product(user, product)
    if not allowed:
        raise PermissionError(str(reason))

    order_item = delivered_order_item(user, product)
    review = Review.objects.create(
        product=product,
        user=user,
        order_item=order_item,
        rating=rating,
        title=title or "",
        comment=comment or "",
        verified_purchase=order_item is not None,
    )

    if order_item is not None:
        order_item.is_reviewed = True
        order_item.save(update_fields=["is_reviewed", "updated_at"])

    if images:
        from .models import ReviewImage

        for image in images[:5]:
            ReviewImage.objects.create(review=review, image=image)

    return review


def pending_reviews_for(user, limit=10):
    """Delivered items this customer has not reviewed yet."""
    return (
        OrderItem.objects.filter(
            order__user=user,
            order__status=Order.Status.DELIVERED,
            is_reviewed=False,
            product__isnull=False,
        )
        .select_related("product", "order")
        .prefetch_related("product__images")
        .order_by("-order__delivered_at")[:limit]
    )
