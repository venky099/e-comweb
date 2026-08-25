"""Discount coupons and their redemption log.

Validity and discount amount are computed here and nowhere else. The client
may post a coupon *code*; it may never post a discount.
"""
from decimal import ROUND_HALF_UP, Decimal

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.core.models import TimeStampedModel

ZERO = Decimal("0.00")


def money(value):
    return Decimal(value).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


class CouponQuerySet(models.QuerySet):
    def live(self):
        """Active coupons inside their validity window."""
        now = timezone.now()
        return self.filter(is_active=True, valid_from__lte=now).filter(
            models.Q(valid_to__isnull=True) | models.Q(valid_to__gte=now)
        )

    def public(self):
        return self.live().filter(is_public=True)


class Coupon(TimeStampedModel):
    class DiscountType(models.TextChoices):
        PERCENTAGE = "percentage", _("Percentage off")
        FIXED = "fixed", _("Fixed amount off")
        FREE_SHIPPING = "free_shipping", _("Free shipping")

    code = models.CharField(max_length=32, unique=True, db_index=True)
    description = models.CharField(max_length=255, blank=True)
    discount_type = models.CharField(
        max_length=20, choices=DiscountType.choices, default=DiscountType.PERCENTAGE
    )
    value = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=ZERO,
        validators=[MinValueValidator(ZERO)],
        help_text=_("Percent (e.g. 10 = 10%) or a flat amount, per discount type."),
    )
    max_discount_amount = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        validators=[MinValueValidator(ZERO)],
        help_text=_("Cap for percentage coupons. Blank means uncapped."),
    )
    min_order_value = models.DecimalField(
        max_digits=10, decimal_places=2, default=ZERO, validators=[MinValueValidator(ZERO)]
    )

    valid_from = models.DateTimeField(default=timezone.now, db_index=True)
    valid_to = models.DateTimeField(null=True, blank=True, db_index=True)

    usage_limit = models.PositiveIntegerField(
        null=True, blank=True, help_text=_("Total redemptions allowed. Blank means unlimited.")
    )
    usage_limit_per_user = models.PositiveIntegerField(default=1)
    used_count = models.PositiveIntegerField(default=0, editable=False)

    is_active = models.BooleanField(default=True, db_index=True)
    is_public = models.BooleanField(
        default=True, help_text=_("Show on the storefront offers strip.")
    )
    first_order_only = models.BooleanField(default=False)

    applicable_categories = models.ManyToManyField(
        "catalog.Category",
        blank=True,
        related_name="coupons",
        help_text=_("Leave empty to apply to every category."),
    )
    applicable_products = models.ManyToManyField(
        "catalog.Product",
        blank=True,
        related_name="coupons",
        help_text=_("Leave empty to apply to every product."),
    )

    objects = CouponQuerySet.as_manager()

    class Meta:
        ordering = ("-created_at",)
        indexes = [
            models.Index(fields=["is_active", "valid_from", "valid_to"], name="coupon_window_idx"),
        ]

    def __str__(self):
        return f"{self.code} ({self.discount_label})"

    def save(self, *args, **kwargs):
        self.code = self.code.strip().upper()
        super().save(*args, **kwargs)

    # ---- presentation --------------------------------------------------
    @property
    def discount_label(self):
        if self.discount_type == self.DiscountType.PERCENTAGE:
            label = f"{self.value.normalize():f}% OFF"
            if self.max_discount_amount:
                label += f" up to {settings.CURRENCY_SYMBOL}{self.max_discount_amount:.0f}"
            return label
        if self.discount_type == self.DiscountType.FIXED:
            return f"{settings.CURRENCY_SYMBOL}{self.value:.0f} OFF"
        return str(_("Free shipping"))

    @property
    def is_expired(self):
        return bool(self.valid_to and self.valid_to < timezone.now())

    @property
    def is_exhausted(self):
        return bool(self.usage_limit and self.used_count >= self.usage_limit)

    @property
    def remaining_uses(self):
        if self.usage_limit is None:
            return None
        return max(self.usage_limit - self.used_count, 0)

    # ---- validation ----------------------------------------------------
    def check_validity(self, user=None, cart_total=ZERO, cart_items=None):
        """Return ``(is_valid, reason)``.

        Called on apply, on every cart render, and again at order placement --
        a coupon that lapses between those points cannot slip through.
        """
        now = timezone.now()
        cart_total = Decimal(cart_total or ZERO)

        if not self.is_active:
            return False, _("This coupon is no longer active.")
        if self.valid_from and self.valid_from > now:
            return False, _("This coupon is not active yet.")
        if self.is_expired:
            return False, _("This coupon has expired.")
        if self.is_exhausted:
            return False, _("This coupon has reached its usage limit.")
        if cart_total < self.min_order_value:
            return False, _("Add %(symbol)s%(amount)s more to use this coupon.") % {
                "symbol": settings.CURRENCY_SYMBOL,
                "amount": f"{(self.min_order_value - cart_total):.2f}",
            }

        if user is not None and getattr(user, "is_authenticated", False):
            used_by_user = self.usages.filter(user=user).count()
            if self.usage_limit_per_user and used_by_user >= self.usage_limit_per_user:
                return False, _("You have already used this coupon.")
            if self.first_order_only:
                from apps.orders.models import Order

                has_orders = Order.objects.filter(user=user).exclude(
                    status=Order.Status.CANCELLED
                ).exists()
                if has_orders:
                    return False, _("This coupon is valid on your first order only.")
        elif self.first_order_only or self.usage_limit_per_user:
            # Per-user limits cannot be enforced for guests.
            return False, _("Please sign in to use this coupon.")

        if cart_items is not None and (
            self.applicable_products.exists() or self.applicable_categories.exists()
        ):
            if not self.eligible_subtotal(cart_items):
                return False, _("This coupon does not apply to the items in your cart.")

        return True, ""

    def eligible_subtotal(self, cart_items):
        """Subtotal restricted to the products/categories this coupon covers."""
        product_ids = set(self.applicable_products.values_list("id", flat=True))
        category_ids = set()
        for category in self.applicable_categories.all():
            category_ids.update(category.descendant_ids())

        if not product_ids and not category_ids:
            return money(sum((item.line_total for item in cart_items), ZERO))

        total = ZERO
        for item in cart_items:
            product = item.variant.product
            if product.id in product_ids or product.category_id in category_ids:
                total += item.line_total
        return money(total)

    def discount_for(self, amount, cart_items=None):
        """Discount value for a given subtotal. Never exceeds the subtotal."""
        base = Decimal(amount or ZERO)
        if cart_items is not None and (
            self.applicable_products.exists() or self.applicable_categories.exists()
        ):
            base = self.eligible_subtotal(cart_items)

        if self.discount_type == self.DiscountType.FREE_SHIPPING:
            return ZERO  # applied against delivery, handled by the cart/order

        if self.discount_type == self.DiscountType.PERCENTAGE:
            discount = base * self.value / Decimal("100")
            if self.max_discount_amount is not None:
                discount = min(discount, self.max_discount_amount)
        else:
            discount = self.value

        return money(min(discount, base))

    def gives_free_shipping(self):
        return self.discount_type == self.DiscountType.FREE_SHIPPING


class CouponUsage(TimeStampedModel):
    """One redemption. Rows here are the only proof a coupon was consumed."""

    coupon = models.ForeignKey(
        Coupon, on_delete=models.CASCADE, related_name="usages", db_index=True
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="coupon_usages",
        db_index=True,
    )
    order = models.ForeignKey(
        "orders.Order",
        on_delete=models.CASCADE,
        related_name="coupon_usages",
        null=True,
        blank=True,
    )
    discount_amount = models.DecimalField(max_digits=10, decimal_places=2, default=ZERO)

    class Meta:
        ordering = ("-created_at",)
        constraints = [
            models.UniqueConstraint(
                fields=["coupon", "order"],
                condition=models.Q(order__isnull=False),
                name="unique_coupon_usage_per_order",
            )
        ]
        indexes = [models.Index(fields=["coupon", "user"], name="usage_coupon_user_idx")]

    def __str__(self):
        return f"{self.coupon.code} by {self.user_id}"
