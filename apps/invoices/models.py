"""Invoices and the document number series.

MST section 61 asks for separate identifier series per record type:

    MST-ORD-2026-000001    MST-INV-2026-000001    MST-PAY-2026-000001
    MST-SHP-2026-000001    MST-RET-2026-000001

Section 26 asks the invoice itself to look like a real one: company details,
customer details, a product table, and totals -- downloadable as a PDF and
emailed automatically.

The invoice snapshots everything it prints. Company address, tax number,
customer address, currency, rate and totals are all copied at issue time, so
changing a setting next year cannot rewrite a document a customer already
has.
"""
from decimal import Decimal

from django.db import models, transaction
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.core.models import TimeStampedModel

ZERO = Decimal("0.00")


class NumberSeries(TimeStampedModel):
    """A per-year counter for one kind of document.

    Allocation takes a row lock, so two checkouts in the same instant cannot
    be handed the same invoice number. A duplicate invoice number is the kind
    of thing an auditor notices.
    """

    class Kind(models.TextChoices):
        ORDER = "ORD", _("Order")
        INVOICE = "INV", _("Invoice")
        PAYMENT = "PAY", _("Payment")
        SHIPMENT = "SHP", _("Shipment")
        RETURN = "RET", _("Return")

    kind = models.CharField(max_length=3, choices=Kind.choices)
    year = models.PositiveSmallIntegerField()
    prefix = models.CharField(max_length=8, default="MST")
    last_number = models.PositiveIntegerField(default=0)
    padding = models.PositiveSmallIntegerField(default=6)

    class Meta:
        ordering = ["-year", "kind"]
        constraints = [
            models.UniqueConstraint(
                fields=["kind", "year"], name="invoices_series_unique_per_year"
            )
        ]
        verbose_name_plural = _("number series")

    def __str__(self):
        return f"{self.prefix}-{self.kind}-{self.year} (at {self.last_number})"

    def format(self, number):
        return f"{self.prefix}-{self.kind}-{self.year}-{number:0{self.padding}d}"

    @classmethod
    @transaction.atomic
    def allocate(cls, kind, prefix="MST", when=None):
        """Take the next number in a series and return it formatted.

        Numbers restart each year, which is what the spec's examples show.
        """
        year = (when or timezone.now()).year
        series, _created = cls.objects.select_for_update().get_or_create(
            kind=kind, year=year, defaults={"prefix": prefix}
        )
        series.last_number += 1
        series.save(update_fields=["last_number", "updated_at"])
        return series.format(series.last_number)


class Invoice(TimeStampedModel):
    """A tax invoice for one order.

    Every printed figure is stored here rather than read back through the
    order, so the document is a record rather than a rendering of current
    state.
    """

    order = models.OneToOneField(
        "orders.Order", on_delete=models.PROTECT, related_name="invoice"
    )
    number = models.CharField(max_length=32, unique=True, db_index=True)
    issued_at = models.DateTimeField(default=timezone.now, db_index=True)

    # ---- seller, copied at issue time ----
    company_name = models.CharField(max_length=150)
    company_address = models.TextField(blank=True)
    company_email = models.EmailField(blank=True)
    company_phone = models.CharField(max_length=32, blank=True)
    company_tax_number = models.CharField(
        max_length=32, blank=True, help_text=_("GSTIN or equivalent registration.")
    )

    # ---- buyer, copied at issue time ----
    customer_name = models.CharField(max_length=150)
    customer_email = models.EmailField(blank=True)
    billing_address = models.TextField(blank=True)
    shipping_address = models.TextField(blank=True)
    country_name = models.CharField(max_length=100, blank=True)

    # ---- money, in the currency the customer was charged ----
    currency = models.CharField(max_length=8, default="INR")
    base_currency = models.CharField(max_length=8, default="INR")
    exchange_rate = models.DecimalField(
        max_digits=18, decimal_places=8, default=Decimal("1")
    )
    subtotal = models.DecimalField(max_digits=12, decimal_places=2, default=ZERO)
    discount_total = models.DecimalField(max_digits=12, decimal_places=2, default=ZERO)
    shipping_total = models.DecimalField(max_digits=12, decimal_places=2, default=ZERO)
    tax_total = models.DecimalField(max_digits=12, decimal_places=2, default=ZERO)
    grand_total = models.DecimalField(max_digits=12, decimal_places=2, default=ZERO)

    payment_method = models.CharField(max_length=32, blank=True)
    pdf = models.FileField(upload_to="invoices/%Y/%m/", blank=True, null=True)
    emailed_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        ordering = ["-issued_at", "-id"]

    def __str__(self):
        return self.number

    @property
    def is_emailed(self):
        return self.emailed_at is not None


class InvoiceLine(TimeStampedModel):
    """One row of the invoice's product table (spec section 26)."""

    invoice = models.ForeignKey(
        Invoice, on_delete=models.CASCADE, related_name="lines"
    )
    description = models.CharField(max_length=255)
    sku = models.CharField(max_length=64, blank=True)
    quantity = models.PositiveIntegerField(default=1)
    unit_price = models.DecimalField(max_digits=12, decimal_places=2, default=ZERO)
    discount = models.DecimalField(max_digits=12, decimal_places=2, default=ZERO)
    tax = models.DecimalField(max_digits=12, decimal_places=2, default=ZERO)
    total = models.DecimalField(max_digits=12, decimal_places=2, default=ZERO)
    sort_order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["sort_order", "id"]

    def __str__(self):
        return f"{self.description} x{self.quantity}"


class InvoiceTaxLine(TimeStampedModel):
    """The named tax breakdown as it appeared on the document."""

    invoice = models.ForeignKey(
        Invoice, on_delete=models.CASCADE, related_name="tax_lines"
    )
    name = models.CharField(max_length=32)
    percent = models.DecimalField(max_digits=6, decimal_places=3, default=ZERO)
    amount = models.DecimalField(max_digits=12, decimal_places=2, default=ZERO)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return f"{self.name} {self.amount}"
