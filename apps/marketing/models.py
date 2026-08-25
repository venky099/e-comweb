"""Banners, offers and flash sales shown on the storefront."""
from decimal import Decimal

from django.core.validators import MinValueValidator
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.core.models import TimeStampedModel

ZERO = Decimal("0.00")


class ScheduledQuerySet(models.QuerySet):
    """Shared 'currently running' filter for time-boxed marketing records."""

    def live(self):
        now = timezone.now()
        return (
            self.filter(is_active=True)
            .filter(models.Q(start_at__isnull=True) | models.Q(start_at__lte=now))
            .filter(models.Q(end_at__isnull=True) | models.Q(end_at__gte=now))
        )


class ScheduledModel(TimeStampedModel):
    is_active = models.BooleanField(default=True, db_index=True)
    start_at = models.DateTimeField(null=True, blank=True, db_index=True)
    end_at = models.DateTimeField(null=True, blank=True, db_index=True)

    objects = ScheduledQuerySet.as_manager()

    class Meta:
        abstract = True

    @property
    def is_live(self):
        now = timezone.now()
        if not self.is_active:
            return False
        if self.start_at and self.start_at > now:
            return False
        if self.end_at and self.end_at < now:
            return False
        return True


class Banner(ScheduledModel):
    class Position(models.TextChoices):
        HERO = "hero", _("Homepage hero carousel")
        STRIP = "strip", _("Promotional strip")
        SIDEBAR = "sidebar", _("Category sidebar")
        FOOTER = "footer", _("Above footer")

    title = models.CharField(max_length=150)
    subtitle = models.CharField(max_length=255, blank=True)
    image = models.ImageField(upload_to="banners/")
    mobile_image = models.ImageField(
        upload_to="banners/mobile/", blank=True, null=True, help_text=_("Optional portrait crop.")
    )
    link_url = models.CharField(max_length=500, blank=True)
    cta_label = models.CharField(max_length=60, blank=True, default="Shop now")
    position = models.CharField(
        max_length=16, choices=Position.choices, default=Position.HERO, db_index=True
    )
    background_color = models.CharField(max_length=7, blank=True, help_text=_("e.g. #0f172a"))
    sort_order = models.PositiveIntegerField(default=0, db_index=True)
    click_count = models.PositiveIntegerField(default=0, editable=False)

    class Meta:
        ordering = ("sort_order", "-created_at")
        indexes = [models.Index(fields=["position", "is_active", "sort_order"], name="banner_slot_idx")]

    def __str__(self):
        return self.title


class Offer(ScheduledModel):
    """A marketing tile -- optionally pointing at a coupon."""

    title = models.CharField(max_length=150)
    description = models.CharField(max_length=255, blank=True)
    badge_text = models.CharField(max_length=40, blank=True)
    image = models.ImageField(upload_to="offers/", blank=True, null=True)
    link_url = models.CharField(max_length=500, blank=True)
    coupon = models.ForeignKey(
        "coupons.Coupon",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="offers",
        help_text=_("Optional -- shows the code and terms on the offer card."),
    )
    sort_order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ("sort_order", "-created_at")

    def __str__(self):
        return self.title


class FlashSale(ScheduledModel):
    """A time-boxed sale. Prices come from the linked items, not the product."""

    name = models.CharField(max_length=150)
    description = models.CharField(max_length=255, blank=True)
    banner_image = models.ImageField(upload_to="flash-sales/", blank=True, null=True)

    class Meta:
        ordering = ("-start_at",)

    def __str__(self):
        return self.name

    @property
    def seconds_remaining(self):
        if not self.end_at:
            return None
        delta = (self.end_at - timezone.now()).total_seconds()
        return max(int(delta), 0)

    def live_items(self):
        return self.items.select_related("variant__product").prefetch_related(
            "variant__product__images"
        )


class FlashSaleItem(TimeStampedModel):
    flash_sale = models.ForeignKey(
        FlashSale, on_delete=models.CASCADE, related_name="items", db_index=True
    )
    variant = models.ForeignKey(
        "catalog.ProductVariant", on_delete=models.CASCADE, related_name="flash_sale_items"
    )
    sale_price = models.DecimalField(
        max_digits=12, decimal_places=2, validators=[MinValueValidator(ZERO)]
    )
    quantity_limit = models.PositiveIntegerField(
        default=0, help_text=_("0 means no cap beyond available stock.")
    )
    sold_count = models.PositiveIntegerField(default=0, editable=False)
    sort_order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ("sort_order", "id")
        constraints = [
            models.UniqueConstraint(
                fields=["flash_sale", "variant"], name="unique_variant_per_flash_sale"
            )
        ]

    def __str__(self):
        return f"{self.variant} @ {self.sale_price}"

    @property
    def discount_percent(self):
        base = self.variant.price
        if not base or base <= self.sale_price:
            return 0
        return int(round(((base - self.sale_price) / base) * 100))

    @property
    def is_sold_out(self):
        if self.quantity_limit and self.sold_count >= self.quantity_limit:
            return True
        return self.variant.available_quantity <= 0

    @property
    def claimed_percent(self):
        """Progress-bar value for the 'x% claimed' meter."""
        if not self.quantity_limit:
            return 0
        return min(int((self.sold_count / self.quantity_limit) * 100), 100)
