"""Orders, order lines, status history and return requests.

An order is an immutable financial record: prices, names and addresses are
snapshotted onto it at placement time so later catalog edits never rewrite
what a customer was charged.
"""
from datetime import timedelta
from decimal import ROUND_HALF_UP, Decimal

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models
from django.urls import reverse
from django.utils import timezone
from django.utils.crypto import get_random_string
from django.utils.translation import gettext_lazy as _

from apps.core.models import TimeStampedModel

ZERO = Decimal("0.00")


def money(value):
    return Decimal(value or 0).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def generate_order_number():
    """Human-readable, non-sequential order number: ``LS-20260825-7QF3KD``."""
    stamp = timezone.localtime().strftime("%Y%m%d")
    for _attempt in range(10):
        candidate = f"LS-{stamp}-{get_random_string(6, 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789')}"
        if not Order.objects.filter(order_number=candidate).exists():
            return candidate
    raise RuntimeError("Could not allocate a unique order number.")


class OrderQuerySet(models.QuerySet):
    def for_user(self, user):
        return self.filter(user=user)

    def paid(self):
        return self.filter(payment_status=Order.PaymentStatus.PAID)

    def revenue_generating(self):
        """Orders that count towards sales figures."""
        return self.exclude(
            status__in=[Order.Status.CANCELLED, Order.Status.RETURNED, Order.Status.REFUNDED]
        )

    def with_details(self):
        return self.select_related("user", "coupon").prefetch_related(
            "items__variant__product", "status_history"
        )


class Order(TimeStampedModel):
    class Status(models.TextChoices):
        PENDING = "pending", _("Pending")
        CONFIRMED = "confirmed", _("Confirmed")
        PROCESSING = "processing", _("Processing")
        SHIPPED = "shipped", _("Shipped")
        OUT_FOR_DELIVERY = "out_for_delivery", _("Out for delivery")
        DELIVERED = "delivered", _("Delivered")
        CANCELLED = "cancelled", _("Cancelled")
        RETURN_REQUESTED = "return_requested", _("Return requested")
        RETURNED = "returned", _("Returned")
        REFUND_INITIATED = "refund_initiated", _("Refund initiated")
        REFUNDED = "refunded", _("Refunded")

    class PaymentStatus(models.TextChoices):
        PENDING = "pending", _("Pending")
        PAID = "paid", _("Paid")
        FAILED = "failed", _("Failed")
        REFUND_PENDING = "refund_pending", _("Refund pending")
        REFUNDED = "refunded", _("Refunded")
        PARTIALLY_REFUNDED = "partially_refunded", _("Partially refunded")

    class PaymentMethod(models.TextChoices):
        CARD = "card", _("Credit / Debit card")
        UPI = "upi", _("UPI")
        NETBANKING = "netbanking", _("Net banking")
        WALLET = "wallet", _("Wallet")
        COD = "cod", _("Cash on delivery")

    # Which statuses may follow a given status. The single source of truth for
    # every transition, used by both the storefront and staff tooling.
    TRANSITIONS = {
        Status.PENDING: {Status.CONFIRMED, Status.CANCELLED},
        Status.CONFIRMED: {Status.PROCESSING, Status.CANCELLED},
        Status.PROCESSING: {Status.SHIPPED, Status.CANCELLED},
        Status.SHIPPED: {Status.OUT_FOR_DELIVERY, Status.DELIVERED, Status.RETURN_REQUESTED},
        Status.OUT_FOR_DELIVERY: {Status.DELIVERED, Status.RETURN_REQUESTED},
        Status.DELIVERED: {Status.RETURN_REQUESTED},
        Status.RETURN_REQUESTED: {Status.RETURNED, Status.DELIVERED},
        Status.RETURNED: {Status.REFUND_INITIATED},
        Status.REFUND_INITIATED: {Status.REFUNDED},
        Status.CANCELLED: {Status.REFUND_INITIATED},
        Status.REFUNDED: set(),
    }

    # Statuses whose stock must be returned to inventory.
    STOCK_RESTORING_STATUSES = {Status.CANCELLED, Status.RETURNED}

    order_number = models.CharField(
        max_length=32, unique=True, default=generate_order_number, editable=False, db_index=True
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="orders",
        db_index=True,
    )
    email = models.EmailField(help_text=_("Snapshot of the buyer email at placement time."))
    phone = models.CharField(max_length=20, blank=True)

    status = models.CharField(
        max_length=24, choices=Status.choices, default=Status.PENDING, db_index=True
    )
    payment_status = models.CharField(
        max_length=24, choices=PaymentStatus.choices, default=PaymentStatus.PENDING, db_index=True
    )
    payment_method = models.CharField(
        max_length=20, choices=PaymentMethod.choices, default=PaymentMethod.COD
    )

    # ---- money (all computed server-side at placement) ----
    subtotal = models.DecimalField(max_digits=12, decimal_places=2, default=ZERO)
    product_discount = models.DecimalField(max_digits=12, decimal_places=2, default=ZERO)
    coupon = models.ForeignKey(
        "coupons.Coupon", on_delete=models.SET_NULL, null=True, blank=True, related_name="orders"
    )
    coupon_code = models.CharField(max_length=32, blank=True)
    coupon_discount = models.DecimalField(max_digits=12, decimal_places=2, default=ZERO)
    delivery_charge = models.DecimalField(max_digits=12, decimal_places=2, default=ZERO)
    tax_amount = models.DecimalField(max_digits=12, decimal_places=2, default=ZERO)
    total_amount = models.DecimalField(
        max_digits=12, decimal_places=2, default=ZERO, validators=[MinValueValidator(ZERO)],
        db_index=True,
    )
    refunded_amount = models.DecimalField(max_digits=12, decimal_places=2, default=ZERO)
    # ---- currency (MST spec section 60) ---------------------------------
    # The figures above are in the base currency, which is what the catalogue
    # is priced in. The ones below are what the customer was actually charged,
    # frozen with the rate used. Storing both is the whole point: "this
    # prevents future exchange-rate changes from changing historical
    # invoices". Nothing ever re-derives these from today's rate.
    currency = models.CharField(
        max_length=8, default="INR", help_text=_("What the customer was charged in.")
    )
    base_currency = models.CharField(
        max_length=8, default="INR", help_text=_("What the amounts above are in.")
    )
    exchange_rate = models.DecimalField(
        max_digits=18,
        decimal_places=8,
        default=Decimal("1"),
        help_text=_("Units of the charged currency per unit of the base currency."),
    )
    charged_subtotal = models.DecimalField(max_digits=12, decimal_places=2, default=ZERO)
    charged_discount = models.DecimalField(max_digits=12, decimal_places=2, default=ZERO)
    charged_delivery_charge = models.DecimalField(
        max_digits=12, decimal_places=2, default=ZERO
    )
    charged_tax_amount = models.DecimalField(
        max_digits=12, decimal_places=2, default=ZERO
    )
    charged_total = models.DecimalField(max_digits=12, decimal_places=2, default=ZERO)

    # ---- delivery -------------------------------------------------------
    destination_country = models.ForeignKey(
        "geo.Country",
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="orders",
        help_text=_("Resolved from the shipping address, for tax and reporting."),
    )
    shipping_method = models.ForeignKey(
        "shipping.ShippingMethod",
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="orders",
    )
    shipping_method_name = models.CharField(
        max_length=64,
        blank=True,
        help_text=_("Copied at checkout, so renaming a method later cannot rewrite it."),
    )

    #: True once the reservation taken at placement has been converted into a
    #: sale. It decides whether closing the order *releases* a reservation or
    #: *restores* sold units, so it is maintained only by the service layer.
    stock_committed = models.BooleanField(default=False, editable=False)

    # ---- shipping address snapshot ----
    shipping_full_name = models.CharField(max_length=150)
    shipping_phone = models.CharField(max_length=20)
    shipping_line1 = models.CharField(max_length=255)
    shipping_line2 = models.CharField(max_length=255, blank=True)
    shipping_landmark = models.CharField(max_length=255, blank=True)
    shipping_city = models.CharField(max_length=100)
    shipping_state = models.CharField(max_length=100)
    shipping_country = models.CharField(max_length=100, default="India")
    shipping_postal_code = models.CharField(max_length=16)

    # ---- fulfilment ----
    tracking_number = models.CharField(max_length=64, blank=True, db_index=True)
    courier_name = models.CharField(max_length=100, blank=True)
    estimated_delivery = models.DateField(null=True, blank=True)
    customer_note = models.TextField(blank=True)
    staff_note = models.TextField(blank=True)
    cancel_reason = models.CharField(max_length=255, blank=True)

    placed_at = models.DateTimeField(default=timezone.now, db_index=True)
    confirmed_at = models.DateTimeField(null=True, blank=True)
    shipped_at = models.DateTimeField(null=True, blank=True)
    delivered_at = models.DateTimeField(null=True, blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)
    returned_at = models.DateTimeField(null=True, blank=True)

    objects = OrderQuerySet.as_manager()

    class Meta:
        ordering = ("-placed_at",)
        indexes = [
            models.Index(fields=["user", "-placed_at"], name="order_user_time_idx"),
            models.Index(fields=["status", "-placed_at"], name="order_status_time_idx"),
            models.Index(fields=["payment_status"], name="order_payment_status_idx"),
            models.Index(fields=["-placed_at"], name="order_placed_idx"),
        ]

    def __str__(self):
        return self.order_number

    def get_absolute_url(self):
        return reverse("orders:detail", kwargs={"order_number": self.order_number})

    # ---- derived ------------------------------------------------------
    @property
    def item_count(self):
        return sum(item.quantity for item in self.items.all())

    @property
    def total_savings(self):
        return money(self.product_discount + self.coupon_discount)

    @property
    def is_paid(self):
        return self.payment_status == self.PaymentStatus.PAID

    @property
    def is_cod(self):
        return self.payment_method == self.PaymentMethod.COD

    @property
    def is_open(self):
        """Still moving through fulfilment."""
        return self.status not in {
            self.Status.DELIVERED,
            self.Status.CANCELLED,
            self.Status.RETURNED,
            self.Status.REFUNDED,
        }

    @property
    def shipping_address_lines(self):
        parts = [
            self.shipping_line1,
            self.shipping_line2,
            self.shipping_landmark,
            f"{self.shipping_city}, {self.shipping_state} {self.shipping_postal_code}",
            self.shipping_country,
        ]
        return [p for p in parts if p]

    @property
    def status_index(self):
        """Position along the happy path, for the tracking progress bar."""
        flow = [
            self.Status.PENDING,
            self.Status.CONFIRMED,
            self.Status.PROCESSING,
            self.Status.SHIPPED,
            self.Status.OUT_FOR_DELIVERY,
            self.Status.DELIVERED,
        ]
        try:
            return flow.index(self.status)
        except ValueError:
            return -1

    @property
    def status_badge(self):
        return {
            self.Status.PENDING: "secondary",
            self.Status.CONFIRMED: "info",
            self.Status.PROCESSING: "info",
            self.Status.SHIPPED: "primary",
            self.Status.OUT_FOR_DELIVERY: "primary",
            self.Status.DELIVERED: "success",
            self.Status.CANCELLED: "danger",
            self.Status.RETURN_REQUESTED: "warning",
            self.Status.RETURNED: "warning",
            self.Status.REFUND_INITIATED: "warning",
            self.Status.REFUNDED: "dark",
        }.get(self.status, "secondary")

    # ---- policy -------------------------------------------------------
    def can_transition_to(self, new_status):
        return new_status in self.TRANSITIONS.get(self.status, set())

    @property
    def can_be_cancelled(self):
        """Cancellable while unshipped and inside the cancellation window."""
        if self.status not in {self.Status.PENDING, self.Status.CONFIRMED, self.Status.PROCESSING}:
            return False
        window = timedelta(hours=settings.ORDER_CANCEL_WINDOW_HOURS)
        return timezone.now() - self.placed_at <= window

    @property
    def return_deadline(self):
        if not self.delivered_at:
            return None
        return self.delivered_at + timedelta(days=settings.RETURN_WINDOW_DAYS)

    @property
    def can_be_returned(self):
        if self.status != self.Status.DELIVERED or not self.delivered_at:
            return False
        if not any(item.is_returnable for item in self.items.all()):
            return False
        return timezone.now() <= self.return_deadline

    @property
    def can_be_reviewed(self):
        return self.status == self.Status.DELIVERED

    def recalculate_totals(self, save=True):
        """Recompute money from the order lines. Server-side, always."""
        items = list(self.items.all())
        self.subtotal = money(sum((i.line_total for i in items), ZERO))
        self.product_discount = money(sum((i.line_savings for i in items), ZERO))
        self.total_amount = money(
            max(self.subtotal - self.coupon_discount, ZERO)
            + self.delivery_charge
            + self.tax_amount
        )
        if save:
            self.save(
                update_fields=[
                    "subtotal",
                    "product_discount",
                    "total_amount",
                    "updated_at",
                ]
            )
        return self.total_amount


class OrderItem(TimeStampedModel):
    """A purchased line. Product details are snapshotted, not looked up."""

    class ItemStatus(models.TextChoices):
        ACTIVE = "active", _("Active")
        CANCELLED = "cancelled", _("Cancelled")
        RETURN_REQUESTED = "return_requested", _("Return requested")
        RETURNED = "returned", _("Returned")
        REFUNDED = "refunded", _("Refunded")

    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="items", db_index=True)
    variant = models.ForeignKey(
        "catalog.ProductVariant",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="order_items",
    )
    product = models.ForeignKey(
        "catalog.Product",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="order_items",
        db_index=True,
    )

    # Snapshots -- what the customer actually bought.
    product_name = models.CharField(max_length=255)
    variant_label = models.CharField(max_length=120, blank=True)
    sku = models.CharField(max_length=64, blank=True)
    image_url = models.CharField(max_length=500, blank=True)

    unit_price = models.DecimalField(max_digits=12, decimal_places=2)
    unit_mrp = models.DecimalField(max_digits=12, decimal_places=2, default=ZERO)
    quantity = models.PositiveIntegerField(default=1, validators=[MinValueValidator(1)])
    line_total = models.DecimalField(max_digits=12, decimal_places=2, default=ZERO)

    status = models.CharField(
        max_length=20, choices=ItemStatus.choices, default=ItemStatus.ACTIVE, db_index=True
    )
    is_returnable = models.BooleanField(default=True)
    is_reviewed = models.BooleanField(default=False)

    class Meta:
        ordering = ("id",)
        indexes = [
            models.Index(fields=["order", "status"], name="orderitem_order_status_idx"),
            models.Index(fields=["product"], name="orderitem_product_idx"),
        ]

    def __str__(self):
        return f"{self.quantity} x {self.product_name}"

    def save(self, *args, **kwargs):
        self.line_total = money(self.unit_price * self.quantity)
        super().save(*args, **kwargs)

    @property
    def line_mrp_total(self):
        return money((self.unit_mrp or self.unit_price) * self.quantity)

    @property
    def line_savings(self):
        return money(max(self.line_mrp_total - self.line_total, ZERO))

    @property
    def display_title(self):
        return f"{self.product_name} ({self.variant_label})" if self.variant_label else self.product_name


#: Module-level aliases so the OpenAPI generator can name these enums
#: (drf-spectacular resolves ENUM_NAME_OVERRIDES by import path).
ORDER_STATUS_CHOICES = Order.Status.choices
ORDER_PAYMENT_STATUS_CHOICES = Order.PaymentStatus.choices
ORDER_ITEM_STATUS_CHOICES = OrderItem.ItemStatus.choices


class OrderStatusHistory(TimeStampedModel):
    """Every status change, so tracking pages and audits agree."""

    order = models.ForeignKey(
        Order, on_delete=models.CASCADE, related_name="status_history", db_index=True
    )
    status = models.CharField(max_length=24, choices=Order.Status.choices)
    note = models.CharField(max_length=255, blank=True)
    changed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="order_status_changes",
    )

    class Meta:
        ordering = ("created_at", "id")
        verbose_name_plural = _("order status history")

    def __str__(self):
        return f"{self.order_id}: {self.status}"


class ReturnRequest(TimeStampedModel):
    """A customer-initiated return/refund against one order line."""

    class Status(models.TextChoices):
        REQUESTED = "requested", _("Requested")
        APPROVED = "approved", _("Approved")
        REJECTED = "rejected", _("Rejected")
        PICKED_UP = "picked_up", _("Picked up")
        COMPLETED = "completed", _("Completed")
        REFUNDED = "refunded", _("Refunded")

    class Reason(models.TextChoices):
        DAMAGED = "damaged", _("Item arrived damaged")
        WRONG_ITEM = "wrong_item", _("Wrong item delivered")
        SIZE_ISSUE = "size_issue", _("Size or fit issue")
        NOT_AS_DESCRIBED = "not_as_described", _("Not as described")
        QUALITY = "quality", _("Quality not as expected")
        NO_LONGER_NEEDED = "no_longer_needed", _("No longer needed")
        OTHER = "other", _("Other")

    order = models.ForeignKey(
        Order, on_delete=models.CASCADE, related_name="return_requests", db_index=True
    )
    order_item = models.ForeignKey(
        OrderItem, on_delete=models.CASCADE, related_name="return_requests"
    )
    quantity = models.PositiveIntegerField(default=1, validators=[MinValueValidator(1)])
    reason = models.CharField(max_length=24, choices=Reason.choices)
    comment = models.TextField(blank=True)
    status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.REQUESTED, db_index=True
    )
    refund_amount = models.DecimalField(max_digits=12, decimal_places=2, default=ZERO)
    staff_note = models.CharField(max_length=255, blank=True)
    processed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="processed_returns",
    )
    processed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ("-created_at",)
        indexes = [models.Index(fields=["status", "-created_at"], name="return_status_time_idx")]

    def __str__(self):
        return f"Return #{self.pk} for {self.order.order_number}"

    @property
    def expected_refund(self):
        """Refund value derived from the line, never from client input."""
        return money(self.order_item.unit_price * self.quantity)

    @property
    def status_badge(self):
        return {
            self.Status.REQUESTED: "warning",
            self.Status.APPROVED: "info",
            self.Status.REJECTED: "danger",
            self.Status.PICKED_UP: "primary",
            self.Status.COMPLETED: "success",
            self.Status.REFUNDED: "success",
        }.get(self.status, "secondary")


RETURN_STATUS_CHOICES = ReturnRequest.Status.choices
