"""Invoices and the document number series.

Sections 26 and 61. The properties that matter are that a number is never
reused, and that an issued invoice is a record rather than a live view of the
order it came from.
"""
from decimal import Decimal

from django.core import mail
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.cart.models import Cart, CartItem
from apps.core.tests.factories import (
    create_address,
    create_category,
    create_product,
    create_staff,
    create_user,
    create_variant,
)
from apps.geo.models import Country, Currency, ExchangeRate
from apps.invoices import services as invoice_services
from apps.invoices.models import Invoice, NumberSeries
from apps.orders import services as order_services
from apps.orders.models import Order


class SeriesTests(TestCase):
    def test_numbers_follow_the_specs_format(self):
        number = NumberSeries.allocate(NumberSeries.Kind.INVOICE)
        prefix, kind, year, sequence = number.split("-")
        self.assertEqual(prefix, "MST")
        self.assertEqual(kind, "INV")
        self.assertEqual(int(year), timezone.now().year)
        self.assertEqual(sequence, "000001")

    def test_each_kind_counts_separately(self):
        self.assertTrue(
            NumberSeries.allocate(NumberSeries.Kind.INVOICE).endswith("000001")
        )
        self.assertTrue(
            NumberSeries.allocate(NumberSeries.Kind.PAYMENT).endswith("000001")
        )
        self.assertTrue(
            NumberSeries.allocate(NumberSeries.Kind.INVOICE).endswith("000002")
        )

    def test_numbers_never_repeat(self):
        issued = {NumberSeries.allocate(NumberSeries.Kind.SHIPMENT) for _ in range(25)}
        self.assertEqual(len(issued), 25)

    def test_the_series_restarts_each_year(self):
        last_year = timezone.now().replace(year=timezone.now().year - 1)
        first = NumberSeries.allocate(NumberSeries.Kind.RETURN, when=last_year)
        second = NumberSeries.allocate(NumberSeries.Kind.RETURN)
        self.assertTrue(first.endswith("000001"))
        self.assertTrue(second.endswith("000001"))
        self.assertNotEqual(first, second)


class InvoiceTestCase(TestCase):
    def setUp(self):
        self.inr = Currency.objects.create(
            code="INR", name="Rupee", symbol="₹", is_base=True
        )
        self.usd = Currency.objects.create(code="USD", name="Dollar", symbol="$")
        ExchangeRate.objects.create(
            base=self.inr,
            quote=self.usd,
            rate=Decimal("0.01160"),
            effective_from=timezone.now(),
        )
        Country.objects.create(iso2="IN", name="India", currency=self.inr)

        self.user = create_user(email="buyer@example.test")
        self.address = create_address(self.user, country="India", state="Karnataka")
        category = create_category(name="Sarees")
        product = create_product(
            category=category, name="Silk Saree", price=Decimal("5000.00")
        )
        self.variant = create_variant(product, stock=10)
        cart = Cart.objects.create(user=self.user)
        CartItem.objects.create(cart=cart, variant=self.variant, quantity=2)

        self.order = order_services.place_order(
            self.user, cart, self.address, Order.PaymentMethod.COD, currency=self.usd
        )


class IssueTests(InvoiceTestCase):
    def test_issuing_copies_the_orders_charged_figures(self):
        invoice = invoice_services.issue_for(self.order)
        self.assertEqual(invoice.currency, "USD")
        self.assertEqual(invoice.exchange_rate, Decimal("0.01160000"))
        self.assertEqual(invoice.grand_total, self.order.charged_total)
        self.assertEqual(invoice.lines.count(), 1)

    def test_issuing_twice_returns_the_same_document(self):
        """Payment webhooks retry; a second invoice number would be wrong."""
        first = invoice_services.issue_for(self.order)
        second = invoice_services.issue_for(self.order)
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(Invoice.objects.count(), 1)

    def test_an_invoice_does_not_follow_the_order_afterwards(self):
        invoice = invoice_services.issue_for(self.order)
        original = invoice.grand_total

        self.order.total_amount = Decimal("1.00")
        self.order.charged_total = Decimal("1.00")
        self.order.save(update_fields=["total_amount", "charged_total"])

        invoice.refresh_from_db()
        self.assertEqual(invoice.grand_total, original)

    def test_the_company_details_are_snapshotted(self):
        with self.settings(SITE_NAME="Lumen Store", COMPANY_TAX_NUMBER="29ABCDE1234F1Z5"):
            invoice = invoice_services.issue_for(self.order)
        self.assertEqual(invoice.company_name, "Lumen Store")
        self.assertEqual(invoice.company_tax_number, "29ABCDE1234F1Z5")

    def test_confirming_an_order_issues_its_invoice(self):
        order_services.confirm_cod(self.order)
        self.assertTrue(Invoice.objects.filter(order=self.order).exists())


class EmailTests(InvoiceTestCase):
    def test_the_invoice_is_emailed_to_the_customer(self):
        invoice = invoice_services.issue_for(self.order)
        mail.outbox.clear()

        self.assertTrue(invoice_services.email_to_customer(invoice))
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn(invoice.number, mail.outbox[0].subject)
        invoice.refresh_from_db()
        self.assertIsNotNone(invoice.emailed_at)

    def test_it_is_not_sent_twice(self):
        invoice = invoice_services.issue_for(self.order)
        invoice_services.email_to_customer(invoice)
        mail.outbox.clear()

        self.assertFalse(invoice_services.email_to_customer(invoice))
        self.assertEqual(len(mail.outbox), 0)


class RenderingTests(InvoiceTestCase):
    def test_the_document_renders_with_no_template_syntax_left(self):
        invoice = invoice_services.issue_for(self.order)
        html = invoice_services.render_html(invoice)
        self.assertIn(invoice.number, html)
        self.assertIn("Tax Invoice", html)
        self.assertNotIn("{{", html)
        self.assertNotIn("{%", html)

    def test_a_converted_invoice_states_the_rate_it_used(self):
        invoice = invoice_services.issue_for(self.order)
        self.assertIn("exchange rate", invoice_services.render_html(invoice))

    def test_amounts_print_in_the_currency_charged(self):
        invoice = invoice_services.issue_for(self.order)
        html = invoice_services.render_html(invoice)
        self.assertIn("$", html)


class AccessTests(InvoiceTestCase):
    def setUp(self):
        super().setUp()
        self.invoice = invoice_services.issue_for(self.order)
        self.url = reverse("invoices:detail", args=[self.invoice.number])

    def test_the_buyer_can_read_their_invoice(self):
        self.client.force_login(self.user)
        self.assertEqual(self.client.get(self.url).status_code, 200)

    def test_another_customer_cannot(self):
        self.client.force_login(create_user(email="nosy@example.test"))
        self.assertEqual(self.client.get(self.url).status_code, 404)

    def test_staff_can(self):
        self.client.force_login(create_staff(email="staff@example.test"))
        self.assertEqual(self.client.get(self.url).status_code, 200)

    def test_signing_in_is_required(self):
        self.assertEqual(self.client.get(self.url).status_code, 302)

    def test_the_download_falls_back_to_html_when_no_pdf_exists(self):
        self.client.force_login(self.user)
        response = self.client.get(
            reverse("invoices:download", args=[self.invoice.number])
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["X-Invoice-Format"], "html-fallback")
