"""Payment records, refunds and raw gateway webhook events."""
from decimal import Decimal

from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.core.models import TimeStampedModel

ZERO = Decimal("0.00")


class Payment(TimeStampedModel):
    """One payment attempt against an order.

    Nothing here is written from the browser: the gateway order id is created
    server-side and the success callback is only trusted after signature
    verification (see ``apps.payments.gateways``).
    """

    class Gateway(models.TextChoices):
        RAZORPAY = "razorpay", _("Razorpay")
        STRIPE = "stripe", _("Stripe")
        COD = "cod", _("Cash on delivery")
        MOCK = "mock", _("Mock (development)")

    class Status(models.TextChoices):
        CREATED = "created", _("Created")
        PENDING = "pending", _("Pending")
        AUTHORIZED = "authorized", _("Authorized")
        CAPTURED = "captured", _("Captured")
        FAILED = "failed", _("Failed")
        CANCELLED = "cancelled", _("Cancelled")
        REFUNDED = "refunded", _("Refunded")
        PARTIALLY_REFUNDED = "partially_refunded", _("Partially refunded")

    order = models.ForeignKey(
        "orders.Order", on_delete=models.CASCADE, related_name="payments", db_index=True
    )
    gateway = models.CharField(max_length=20, choices=Gateway.choices, db_index=True)
    method = models.CharField(max_length=32, blank=True)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    currency = models.CharField(max_length=8, default="INR")
    status = models.CharField(
        max_length=24, choices=Status.choices, default=Status.CREATED, db_index=True
    )

    gateway_order_id = models.CharField(max_length=128, blank=True, db_index=True)
    gateway_payment_id = models.CharField(max_length=128, blank=True, db_index=True)
    gateway_signature = models.CharField(max_length=256, blank=True)

    error_code = models.CharField(max_length=64, blank=True)
    error_message = models.CharField(max_length=500, blank=True)
    raw_response = models.JSONField(default=dict, blank=True)
    paid_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ("-created_at",)
        indexes = [
            models.Index(fields=["order", "status"], name="payment_order_status_idx"),
            models.Index(fields=["gateway", "-created_at"], name="payment_gateway_time_idx"),
        ]

    def __str__(self):
        return f"{self.get_gateway_display()} {self.amount} ({self.status})"

    @property
    def is_successful(self):
        return self.status in {self.Status.CAPTURED, self.Status.AUTHORIZED}

    @property
    def refundable_amount(self):
        refunded = sum(
            (r.amount for r in self.refunds.all() if r.status == Refund.Status.PROCESSED), ZERO
        )
        return max(self.amount - refunded, ZERO)


#: See the note in apps/orders/models.py about enum naming.
PAYMENT_STATUS_CHOICES = Payment.Status.choices


class Refund(TimeStampedModel):
    class Status(models.TextChoices):
        PENDING = "pending", _("Pending")
        PROCESSED = "processed", _("Processed")
        FAILED = "failed", _("Failed")

    payment = models.ForeignKey(
        Payment, on_delete=models.CASCADE, related_name="refunds", db_index=True
    )
    return_request = models.ForeignKey(
        "orders.ReturnRequest",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="refunds",
    )
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.PENDING, db_index=True
    )
    reason = models.CharField(max_length=255, blank=True)
    gateway_refund_id = models.CharField(max_length=128, blank=True)
    raw_response = models.JSONField(default=dict, blank=True)
    processed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ("-created_at",)

    def __str__(self):
        return f"Refund {self.amount} ({self.status})"


class WebhookEvent(TimeStampedModel):
    """Raw gateway callbacks, stored before processing.

    ``event_id`` is unique so a gateway retrying the same event cannot double
    apply it.
    """

    gateway = models.CharField(max_length=20, db_index=True)
    event_id = models.CharField(max_length=128, unique=True)
    event_type = models.CharField(max_length=64, blank=True, db_index=True)
    payload = models.JSONField(default=dict)
    signature_verified = models.BooleanField(default=False)
    processed = models.BooleanField(default=False, db_index=True)
    processing_error = models.CharField(max_length=500, blank=True)

    class Meta:
        ordering = ("-created_at",)

    def __str__(self):
        return f"{self.gateway}:{self.event_type}:{self.event_id}"
