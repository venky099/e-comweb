"""Issuing invoices.

``issue_for(order)`` is idempotent: an order has at most one invoice, and
calling it twice returns the same document rather than allocating a second
number. Payment webhooks retry, and a duplicate invoice number is exactly the
kind of thing an auditor notices.

Amounts are written in the currency the customer was charged, taken from the
order's frozen figures. Nothing here converts anything -- the conversion
already happened once, at checkout, at a rate the order recorded.
"""
import logging
from decimal import Decimal

from django.conf import settings
from django.core.mail import EmailMessage
from django.db import transaction
from django.template.loader import render_to_string
from django.utils import timezone

from apps.invoices.models import Invoice, InvoiceLine, InvoiceTaxLine, NumberSeries

logger = logging.getLogger("ecommerce")

ZERO = Decimal("0.00")


def company_details():
    """Seller details for the invoice header (spec section 59)."""
    return {
        "company_name": getattr(settings, "SITE_NAME", "") or "",
        "company_address": getattr(settings, "COMPANY_ADDRESS", "") or "",
        "company_email": getattr(settings, "SUPPORT_EMAIL", "") or "",
        "company_phone": getattr(settings, "SUPPORT_PHONE", "") or "",
        "company_tax_number": getattr(settings, "COMPANY_TAX_NUMBER", "") or "",
    }


def address_text(order):
    """The shipping address as it was snapshotted onto the order."""
    parts = [
        order.shipping_full_name,
        order.shipping_line1,
        order.shipping_line2,
        order.shipping_landmark,
        f"{order.shipping_city} {order.shipping_postal_code}".strip(),
        order.shipping_state,
        order.shipping_country,
    ]
    return "\n".join(p for p in parts if p)


@transaction.atomic
def issue_for(order, when=None):
    """Create the invoice for an order, or return the existing one."""
    existing = Invoice.objects.filter(order=order).first()
    if existing is not None:
        return existing

    issued_at = when or timezone.now()
    rate = order.exchange_rate or Decimal("1")

    def charged(base_amount, stored):
        """Prefer the frozen charged figure; fall back to the frozen rate.

        Orders placed before the charged columns existed were backfilled at
        rate 1, so both routes agree for them.
        """
        if stored:
            return stored
        return (Decimal(base_amount) * Decimal(rate)).quantize(Decimal("0.01"))

    invoice = Invoice.objects.create(
        order=order,
        number=NumberSeries.allocate(NumberSeries.Kind.INVOICE, when=issued_at),
        issued_at=issued_at,
        customer_name=order.shipping_full_name or order.email,
        customer_email=order.email,
        billing_address=address_text(order),
        shipping_address=address_text(order),
        country_name=order.shipping_country or "",
        currency=order.currency,
        base_currency=order.base_currency,
        exchange_rate=rate,
        subtotal=charged(order.subtotal, order.charged_subtotal),
        discount_total=charged(
            order.product_discount + order.coupon_discount, order.charged_discount
        ),
        shipping_total=charged(order.delivery_charge, order.charged_delivery_charge),
        tax_total=charged(order.tax_amount, order.charged_tax_amount),
        grand_total=charged(order.total_amount, order.charged_total),
        payment_method=order.get_payment_method_display(),
        **company_details(),
    )

    lines = []
    for index, item in enumerate(order.items.all()):
        unit = charged(item.unit_price, None)
        mrp = charged(item.unit_mrp or item.unit_price, None)
        lines.append(
            InvoiceLine(
                invoice=invoice,
                description=f"{item.product_name} {item.variant_label}".strip(),
                sku=item.sku,
                quantity=item.quantity,
                unit_price=unit,
                discount=(mrp - unit) * item.quantity,
                total=unit * item.quantity,
                sort_order=index,
            )
        )
    InvoiceLine.objects.bulk_create(lines)

    InvoiceTaxLine.objects.bulk_create(
        [
            InvoiceTaxLine(
                invoice=invoice,
                name=line.name,
                percent=line.percent,
                amount=charged(line.amount, None),
            )
            for line in order.tax_lines.all()
        ]
    )

    logger.info("Invoice %s issued for order %s", invoice.number, order.order_number)
    return invoice


def render_html(invoice):
    """The invoice as HTML -- what both the download and the email show."""
    return render_to_string(
        "invoices/invoice.html",
        {
            "invoice": invoice,
            "lines": invoice.lines.all(),
            "tax_lines": invoice.tax_lines.all(),
            "order": invoice.order,
        },
    )


def email_to_customer(invoice, force=False):
    """Send the invoice to the customer (spec section 26).

    Does nothing if it has already been sent, unless forced -- payment
    webhooks retry, and a customer should not receive the same invoice five
    times because a gateway was unsure.
    """
    if invoice.emailed_at and not force:
        return False
    if not invoice.customer_email:
        return False

    message = EmailMessage(
        subject=f"Invoice {invoice.number}",
        body=render_html(invoice),
        from_email=getattr(settings, "DEFAULT_FROM_EMAIL", None),
        to=[invoice.customer_email],
    )
    message.content_subtype = "html"

    if invoice.pdf:
        try:
            message.attach(
                f"{invoice.number}.pdf", invoice.pdf.read(), "application/pdf"
            )
        except Exception as exc:  # a missing file must not block the email
            logger.warning("Invoice %s PDF unreadable: %s", invoice.number, exc)

    message.send(fail_silently=False)
    invoice.emailed_at = timezone.now()
    invoice.save(update_fields=["emailed_at", "updated_at"])
    return True
