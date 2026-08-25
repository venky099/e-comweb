"""Customer wishlists."""
from django.conf import settings
from django.db import models

from apps.core.models import TimeStampedModel


class Wishlist(TimeStampedModel):
    """One wishlist per customer, created lazily on first save."""

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="wishlist"
    )

    class Meta:
        ordering = ("-updated_at",)

    def __str__(self):
        return f"Wishlist<{self.user_id}> {self.item_count} item(s)"

    @classmethod
    def for_user(cls, user):
        wishlist, _created = cls.objects.get_or_create(user=user)
        return wishlist

    def live_items(self):
        return self.items.select_related(
            "product__category", "product__brand", "variant__inventory"
        ).prefetch_related("product__images", "product__variants__inventory")

    @property
    def item_count(self):
        return self.items.count()

    def has_product(self, product_id):
        return self.items.filter(product_id=product_id).exists()


class WishlistItem(TimeStampedModel):
    wishlist = models.ForeignKey(
        Wishlist, on_delete=models.CASCADE, related_name="items", db_index=True
    )
    product = models.ForeignKey(
        "catalog.Product", on_delete=models.CASCADE, related_name="wishlist_items", db_index=True
    )
    variant = models.ForeignKey(
        "catalog.ProductVariant",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="wishlist_items",
    )
    note = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ("-created_at",)
        constraints = [
            models.UniqueConstraint(
                fields=["wishlist", "product"], name="unique_product_per_wishlist"
            )
        ]
        indexes = [
            models.Index(fields=["wishlist", "-created_at"], name="wishitem_list_time_idx")
        ]

    def __str__(self):
        return f"{self.product_id} in wishlist {self.wishlist_id}"

    @property
    def movable_variant(self):
        """Variant used by "move to cart" -- the saved one, else the default."""
        if self.variant and self.variant.is_active:
            return self.variant
        return self.product.default_variant

    @property
    def in_stock(self):
        variant = self.movable_variant
        return bool(variant and variant.available_quantity > 0)
