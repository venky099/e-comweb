"""Parcels, tracking and customs (MST spec section 31).

A shipment is not an order. One order can leave in two boxes, on different
days, with different tracking numbers -- and a partial delivery has to be
representable or the tracking page starts lying. So the parcel is its own
record, holding the items that are actually in it.

Tracking events are append-only, like stock movements: carriers resend and
reorder their updates, and a history you can replay is what makes a "where is
my parcel" conversation answerable.
"""
from decimal import Decimal

from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.core.models import TimeStampedModel

ZERO = Decimal("0.00")


class Shipment(TimeStampedModel):
    """One parcel, on its way."""

    class Status(models.TextChoices):
        PENDING = "pending", _("Awaiting dispatch")
        DISPATCHED = "dispatched", _("Dispatched")
        IN_TRANSIT = "in_transit", _("In transit")
        OUT_FOR_DELIVERY = "out_for_delivery", _("Out for delivery")
        DELIVERED = "delivered", _("Delivered")
        FAILED = "failed", _("Delivery failed")
        RETURNED = "returned", _("Returned to sender")

    order = models.ForeignKey(
        "orders.Order", on_delete=models.PROTECT, related_name="shipments"
    )
    number = models.CharField(max_length=32, unique=True, db_index=True)
    method = models.ForeignKey(
        "shipping.ShippingMethod",
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="shipments",
    )
    carrier = models.CharField(max_length=64, blank=True)
    tracking_number = models.CharField(max_length=64, blank=True, db_index=True)
    tracking_url = models.URLField(blank=True)
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.PENDING, db_index=True
    )

    # ---- the parcel itself ----
    weight_grams = models.PositiveIntegerField(default=0)
    length_mm = models.PositiveIntegerField(blank=True, null=True)
    width_mm = models.PositiveIntegerField(blank=True, null=True)
    height_mm = models.PositiveIntegerField(blank=True, null=True)

    # ---- customs, for international parcels (section 31) ----
    contents_description = models.CharField(max_length=255, blank=True)
    declared_value = models.DecimalField(
        max_digits=12, decimal_places=2, default=ZERO,
        help_text=_("In the base currency, for the commercial invoice."),
    )
    hs_code = models.CharField(
        max_length=16, blank=True, help_text=_("Harmonised System tariff code.")
    )

    dispatched_at = models.DateTimeField(blank=True, null=True)
    delivered_at = models.DateTimeField(blank=True, null=True)
    note = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [models.Index(fields=["order", "status"])]

    def __str__(self):
        return self.number

    @property
    def is_international(self):
        country = getattr(self.order, "destination_country", None)
        return bool(country) and not country.iso2 == "IN"

    @property
    def latest_event(self):
        return self.events.first()


class ShipmentItem(TimeStampedModel):
    """How much of an order line is in this parcel."""

    shipment = models.ForeignKey(
        Shipment, on_delete=models.CASCADE, related_name="items"
    )
    order_item = models.ForeignKey(
        "orders.OrderItem", on_delete=models.PROTECT, related_name="shipment_items"
    )
    quantity = models.PositiveIntegerField(default=1)

    class Meta:
        ordering = ["id"]
        constraints = [
            models.UniqueConstraint(
                fields=["shipment", "order_item"], name="shipping_item_once_per_parcel"
            )
        ]

    def __str__(self):
        return f"{self.order_item} x{self.quantity}"


class TrackingEvent(TimeStampedModel):
    """One carrier update. Append-only history."""

    shipment = models.ForeignKey(
        Shipment, on_delete=models.CASCADE, related_name="events"
    )
    status = models.CharField(max_length=20, choices=Shipment.Status.choices)
    description = models.CharField(max_length=255, blank=True)
    location = models.CharField(max_length=120, blank=True)
    occurred_at = models.DateTimeField(default=timezone.now, db_index=True)
    raw = models.JSONField(
        default=dict, blank=True, help_text=_("The carrier's payload, as received.")
    )

    class Meta:
        ordering = ["-occurred_at", "-id"]
        indexes = [models.Index(fields=["shipment", "-occurred_at"])]

    def __str__(self):
        return f"{self.get_status_display()} @ {self.occurred_at:%Y-%m-%d %H:%M}"
