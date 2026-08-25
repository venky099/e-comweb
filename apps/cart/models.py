"""Shopping cart.

Every monetary figure a customer sees comes from these model methods. Nothing
about a total is ever accepted from the client -- templates and the API both
read the same server-computed values.
"""
from decimal import ROUND_HALF_UP, Decimal

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.core.models import TimeStampedModel

ZERO = Decimal("0.00")


def money(value):
    """Round to two places the way an invoice does."""
    return Decimal(value).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


class Cart(TimeStampedModel):
    """A cart belonging to a logged-in user or an anonymous session.

    Guests get a session-keyed cart; on login it is merged into the user's
    database cart (see ``apps.cart.services.merge_carts``).
    """

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="cart",
    )
    session_key = models.CharField(max_length=64, blank=True, db_index=True)
    coupon = models.ForeignKey(
        "coupons.Coupon",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="carts",
    )
    is_active = models.BooleanField(default=True, db_index=True)

    class Meta:
        ordering = ("-updated_at",)
        constraints = [
            models.UniqueConstraint(
                fields=["session_key"],
                condition=models.Q(user__isnull=True) & ~models.Q(session_key=""),
                name="unique_guest_cart_per_session",
            )
        ]

    def __str__(self):
        owner = self.user.get_display_name() if self.user else f"guest:{self.session_key[:8]}"
        return f"Cart<{owner}> {self.item_count} item(s)"

    # ---- contents ------------------------------------------------------
    def live_items(self):
        """Cart rows joined to everything the templates and totals need."""
        return self.items.select_related(
            "variant__product__category", "variant__product__brand", "variant__inventory"
        ).prefetch_related("variant__product__images")

    @property
    def item_count(self):
        return sum(item.quantity for item in self.items.all())

    @property
    def distinct_item_count(self):
        return self.items.count()

    @property
    def is_empty(self):
        return not self.items.exists()

    # ---- money ---------------------------------------------------------
    @property
    def subtotal(self):
        """Sum of line totals at current selling prices."""
        return money(sum((item.line_total for item in self.live_items()), ZERO))

    @property
    def mrp_total(self):
        """What the same basket would cost at full MRP."""
        return money(sum((item.line_mrp_total for item in self.live_items()), ZERO))

    @property
    def product_discount(self):
        """Savings already baked into the product prices."""
        return money(max(self.mrp_total - self.subtotal, ZERO))

    @property
    def coupon_discount(self):
        """Coupon savings, revalidated on every read.

        A coupon that expired or stopped qualifying while the cart sat idle
        contributes nothing -- it is never trusted from a stored value.
        """
        if not self.coupon:
            return ZERO
        is_valid, _reason = self.coupon.check_validity(user=self.user, cart_total=self.subtotal)
        if not is_valid:
            return ZERO
        return self.coupon.discount_for(self.subtotal)

    @property
    def discounted_subtotal(self):
        return money(max(self.subtotal - self.coupon_discount, ZERO))

    @property
    def delivery_charge(self):
        if self.is_empty:
            return ZERO
        threshold = Decimal(settings.FREE_DELIVERY_THRESHOLD)
        if self.discounted_subtotal >= threshold:
            return ZERO
        return money(Decimal(settings.DELIVERY_CHARGE))

    @property
    def free_delivery_shortfall(self):
        """How much more to spend to unlock free delivery (0 when unlocked)."""
        threshold = Decimal(settings.FREE_DELIVERY_THRESHOLD)
        return money(max(threshold - self.discounted_subtotal, ZERO))

    @property
    def tax_amount(self):
        rate = Decimal(settings.TAX_RATE_PERCENT)
        if rate <= ZERO:
            return ZERO
        return money(self.discounted_subtotal * rate / Decimal("100"))

    @property
    def total(self):
        return money(self.discounted_subtotal + self.delivery_charge + self.tax_amount)

    @property
    def total_savings(self):
        return money(self.product_discount + self.coupon_discount)

    def as_summary(self):
        """Flat totals dict reused by templates, the API and checkout."""
        return {
            "item_count": self.item_count,
            "subtotal": self.subtotal,
            "mrp_total": self.mrp_total,
            "product_discount": self.product_discount,
            "coupon_code": self.coupon.code if self.coupon else "",
            "coupon_discount": self.coupon_discount,
            "delivery_charge": self.delivery_charge,
            "tax_amount": self.tax_amount,
            "total": self.total,
            "total_savings": self.total_savings,
            "free_delivery_shortfall": self.free_delivery_shortfall,
        }

    # ---- validation ----------------------------------------------------
    def stock_problems(self):
        """Rows that can no longer be fulfilled at their current quantity."""
        problems = []
        for item in self.live_items():
            available = item.variant.available_quantity
            if not item.variant.is_active or not item.variant.product.is_active:
                problems.append((item, _("No longer available")))
            elif available <= 0:
                problems.append((item, _("Out of stock")))
            elif item.quantity > available:
                problems.append((item, _("Only %(n)d left") % {"n": available}))
        return problems

    def clear(self):
        self.items.all().delete()
        if self.coupon_id:
            self.coupon = None
            self.save(update_fields=["coupon", "updated_at"])


class CartItem(TimeStampedModel):
    """One variant + quantity line in a cart."""

    cart = models.ForeignKey(Cart, on_delete=models.CASCADE, related_name="items", db_index=True)
    variant = models.ForeignKey(
        "catalog.ProductVariant", on_delete=models.CASCADE, related_name="cart_items"
    )
    quantity = models.PositiveIntegerField(default=1, validators=[MinValueValidator(1)])

    class Meta:
        ordering = ("-created_at",)
        constraints = [
            models.UniqueConstraint(fields=["cart", "variant"], name="unique_variant_per_cart")
        ]
        indexes = [models.Index(fields=["cart", "variant"], name="cartitem_cart_variant_idx")]

    def __str__(self):
        return f"{self.quantity} x {self.variant}"

    # ---- money (always derived from the variant, never stored) ---------
    @property
    def unit_price(self):
        return money(self.variant.price)

    @property
    def unit_mrp(self):
        return money(self.variant.compare_at_price or self.variant.price)

    @property
    def line_total(self):
        return money(self.unit_price * self.quantity)

    @property
    def line_mrp_total(self):
        return money(self.unit_mrp * self.quantity)

    @property
    def line_savings(self):
        return money(max(self.line_mrp_total - self.line_total, ZERO))

    # ---- stock ---------------------------------------------------------
    @property
    def available_quantity(self):
        return self.variant.available_quantity

    @property
    def max_selectable(self):
        """Upper bound offered in the quantity dropdown."""
        return max(min(self.available_quantity, settings.MAX_CART_QUANTITY_PER_ITEM), 0)

    @property
    def has_stock_issue(self):
        return self.quantity > self.available_quantity or not self.variant.is_active

    def clamp_quantity(self):
        """Trim the row to what is actually purchasable. Returns True if changed."""
        ceiling = self.max_selectable
        if self.quantity > ceiling:
            self.quantity = ceiling
            if self.quantity <= 0:
                self.delete()
            else:
                self.save(update_fields=["quantity", "updated_at"])
            return True
        return False
