"""Stock tracking: one inventory row per variant plus an audit trail."""
from django.conf import settings
from django.db import models
from django.db.models import F
from django.utils.translation import gettext_lazy as _

from apps.core.models import TimeStampedModel


class Warehouse(TimeStampedModel):
    """A place stock physically sits (MST spec section 32)."""

    code = models.SlugField(max_length=16, unique=True)
    name = models.CharField(max_length=100)
    address = models.TextField(blank=True)
    city = models.CharField(max_length=100, blank=True)
    country = models.ForeignKey(
        "geo.Country",
        on_delete=models.PROTECT,
        related_name="warehouses",
        blank=True,
        null=True,
    )
    is_active = models.BooleanField(default=True)
    is_default = models.BooleanField(
        default=False, help_text=_("Where new stock lands unless told otherwise.")
    )
    priority = models.PositiveSmallIntegerField(
        default=0, help_text=_("Lower numbers are picked from first.")
    )

    class Meta:
        ordering = ["priority", "name"]

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        if self.is_default:
            Warehouse.objects.exclude(pk=self.pk).filter(is_default=True).update(
                is_default=False
            )

    @classmethod
    def default(cls):
        return (
            cls.objects.filter(is_default=True, is_active=True).first()
            or cls.objects.filter(is_active=True).order_by("priority", "pk").first()
        )


class InventoryQuerySet(models.QuerySet):
    def low_stock(self):
        return self.filter(
            quantity_available__gt=F("quantity_reserved"),
            quantity_available__lte=F("low_stock_threshold") + F("quantity_reserved"),
        )

    def out_of_stock(self):
        return self.filter(quantity_available__lte=F("quantity_reserved"))

    def with_variant(self):
        return self.select_related("variant__product", "variant__product__category")


class Inventory(TimeStampedModel):
    """Stock levels for a single variant.

    ``quantity_available`` is physical stock on hand. ``quantity_reserved`` is
    the slice already promised to unpaid orders. What a shopper may buy is
    ``sellable_quantity`` = available - reserved.
    """

    variant = models.OneToOneField(
        "catalog.ProductVariant",
        on_delete=models.CASCADE,
        related_name="inventory",
    )
    warehouse = models.ForeignKey(
        Warehouse,
        on_delete=models.PROTECT,
        related_name="stock",
        blank=True,
        null=True,
        help_text=_("Where this variant's stock is held."),
    )

    # The five buckets section 32 asks for. Available is what is on the shelf;
    # reserved is the slice already promised to unpaid orders.
    quantity_available = models.PositiveIntegerField(default=0, db_index=True)
    quantity_reserved = models.PositiveIntegerField(default=0)
    quantity_sold = models.PositiveIntegerField(default=0)
    quantity_returned = models.PositiveIntegerField(
        default=0, help_text=_("Units that came back and were put away again.")
    )
    quantity_damaged = models.PositiveIntegerField(
        default=0, help_text=_("Written off -- counted out of available stock.")
    )

    # Section 32 calls this the reorder level; it is the same number that
    # drives the low-stock alert in section 33, so it stays one field.
    low_stock_threshold = models.PositiveIntegerField(default=settings.LOW_STOCK_THRESHOLD)
    allow_backorder = models.BooleanField(default=False)
    warehouse_location = models.CharField(
        max_length=120, blank=True, help_text=_("Aisle or bin within the warehouse.")
    )
    restocked_at = models.DateTimeField(null=True, blank=True)

    objects = InventoryQuerySet.as_manager()

    class Meta:
        verbose_name_plural = _("inventory")
        ordering = ("variant__product__name",)
        indexes = [
            models.Index(fields=["quantity_available"], name="inventory_available_idx"),
        ]

    def __str__(self):
        return f"{self.variant} ({self.sellable_quantity} sellable)"

    @property
    def sellable_quantity(self):
        return max(self.quantity_available - self.quantity_reserved, 0)

    @property
    def reorder_level(self):
        """Section 32's name for the low-stock threshold."""
        return self.low_stock_threshold

    @property
    def needs_reorder(self):
        return self.sellable_quantity <= self.low_stock_threshold

    @property
    def is_out_of_stock(self):
        return self.sellable_quantity <= 0 and not self.allow_backorder

    @property
    def is_low_stock(self):
        return 0 < self.sellable_quantity <= self.low_stock_threshold

    @property
    def stock_status(self):
        if self.is_out_of_stock:
            return "out_of_stock"
        if self.is_low_stock:
            return "low_stock"
        return "in_stock"

    @property
    def stock_label(self):
        return {
            "out_of_stock": _("Out of stock"),
            "low_stock": _("Only a few left"),
            "in_stock": _("In stock"),
        }[self.stock_status]


class StockMovement(TimeStampedModel):
    """Append-only log of every stock change, for the inventory report."""

    class Reason(models.TextChoices):
        PURCHASE = "purchase", _("Purchase / restock")
        SALE = "sale", _("Sale")
        RESERVATION = "reservation", _("Reserved for order")
        RELEASE = "release", _("Reservation released")
        CANCELLATION = "cancellation", _("Order cancelled")
        RETURN = "return", _("Customer return")
        ADJUSTMENT = "adjustment", _("Manual adjustment")
        DAMAGE = "damage", _("Damaged / written off")

    variant = models.ForeignKey(
        "catalog.ProductVariant",
        on_delete=models.CASCADE,
        related_name="stock_movements",
        db_index=True,
    )
    reason = models.CharField(max_length=20, choices=Reason.choices, db_index=True)
    quantity = models.IntegerField(help_text=_("Signed: positive adds stock, negative removes it."))
    quantity_after = models.IntegerField(
        default=0, help_text=_("Available quantity recorded right after this movement.")
    )
    reference = models.CharField(
        max_length=64, blank=True, db_index=True, help_text=_("Order number or PO reference.")
    )
    note = models.CharField(max_length=255, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="stock_movements",
    )

    class Meta:
        ordering = ("-created_at",)
        indexes = [
            models.Index(fields=["variant", "-created_at"], name="movement_variant_time_idx"),
            models.Index(fields=["reason", "-created_at"], name="movement_reason_time_idx"),
        ]

    def __str__(self):
        return f"{self.get_reason_display()} {self.quantity:+d} on {self.variant_id}"
