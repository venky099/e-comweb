"""Currency, tax and shipping as they land on a placed order.

Spec section 60: an order must permanently store the currency and the rate it
was charged at, so "future exchange-rate changes cannot change historical
invoices". These tests are that sentence, made executable.
"""
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

from apps.cart.models import Cart, CartItem
from apps.core.tests.factories import (
    create_address,
    create_category,
    create_product,
    create_user,
    create_variant,
)
from apps.geo import services as geo_services
from apps.geo.models import Country, Currency, ExchangeRate, State
from apps.orders import services
from apps.orders.models import Order
from apps.shipping.models import ShippingMethod, ShippingRate, ShippingZone
from apps.tax.models import TaxRule


class OrderMoneyTestCase(TestCase):
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
        self.india = Country.objects.create(
            iso2="IN", name="India", currency=self.inr
        )
        self.karnataka = State.objects.create(country=self.india, name="Karnataka")

        self.user = create_user(email="buyer@example.test")
        self.address = create_address(
            self.user, country="India", state="Karnataka", city="Bengaluru"
        )

        category = create_category(name="Sarees")
        product = create_product(
            category=category, name="Silk Saree", price=Decimal("5000.00")
        )
        self.variant = create_variant(product, stock=10)
        self.variant.weight_grams = 400
        self.variant.save(update_fields=["weight_grams"])

        self.cart = Cart.objects.create(user=self.user)
        CartItem.objects.create(cart=self.cart, variant=self.variant, quantity=1)

    def add_shipping(self, price="80.00", free_over=None, code="standard", **kwargs):
        zone, _ = ShippingZone.objects.get_or_create(name="Zone 1")
        zone.countries.add(self.india)
        method, _ = ShippingMethod.objects.get_or_create(
            code=code, defaults={"name": code.title(), "min_days": 5, "max_days": 9}
        )
        ShippingRate.objects.create(
            zone=zone,
            method=method,
            price=Decimal(price),
            free_over=Decimal(free_over) if free_over else None,
            **kwargs,
        )
        return method

    def place(self, **kwargs):
        return services.place_order(
            self.user, self.cart, self.address, Order.PaymentMethod.COD, **kwargs
        )


class FrozenRateTests(OrderMoneyTestCase):
    def test_an_order_records_the_currency_and_the_rate_it_used(self):
        order = self.place(currency=self.usd)
        self.assertEqual(order.currency, "USD")
        self.assertEqual(order.base_currency, "INR")
        self.assertEqual(order.exchange_rate, Decimal("0.01160000"))
        self.assertEqual(order.total_amount, Decimal("5000.00"))
        self.assertEqual(order.charged_total, Decimal("58.00"))

    def test_a_later_rate_change_does_not_move_a_placed_order(self):
        """The sentence section 60 exists for."""
        order = self.place(currency=self.usd)
        before = order.charged_total

        geo_services.set_rate(self.usd, Decimal("0.05000"))
        order.refresh_from_db()

        self.assertEqual(order.charged_total, before)
        self.assertEqual(order.exchange_rate, Decimal("0.01160000"))

    def test_the_base_currency_is_charged_at_one(self):
        order = self.place(currency=self.inr)
        self.assertEqual(order.exchange_rate, Decimal("1"))
        self.assertEqual(order.charged_total, order.total_amount)

    def test_omitting_a_currency_charges_in_the_base_one(self):
        order = self.place()
        self.assertEqual(order.currency, "INR")
        self.assertEqual(order.charged_total, order.total_amount)

    def test_every_charged_column_is_populated(self):
        self.add_shipping(price="80.00")
        order = self.place(currency=self.usd)
        self.assertEqual(order.charged_subtotal, Decimal("58.00"))
        self.assertEqual(order.charged_delivery_charge, Decimal("0.93"))
        self.assertGreater(order.charged_total, Decimal("58.00"))


class OrderTaxTests(OrderMoneyTestCase):
    def test_tax_rules_reach_the_order_and_are_stored_as_lines(self):
        TaxRule.objects.create(
            name="CGST",
            country=self.india,
            percent=Decimal("9.000"),
            applies_when=TaxRule.AppliesWhen.ANY,
            effective_from=timezone.now().date(),
        )
        order = self.place()
        self.assertEqual(order.tax_amount, Decimal("450.00"))
        self.assertEqual(order.total_amount, Decimal("5450.00"))
        self.assertEqual([line.name for line in order.tax_lines.all()], ["CGST"])

    def test_an_unrecognised_country_falls_back_to_the_legacy_flat_rate(self):
        self.address.country = "Atlantis"
        self.address.save(update_fields=["country"])
        with self.settings(TAX_RATE_PERCENT="10.00"):
            order = self.place()
        self.assertEqual(order.tax_amount, Decimal("500.00"))

    def test_the_destination_country_is_recorded(self):
        order = self.place()
        self.assertEqual(order.destination_country, self.india)


class OrderShippingTests(OrderMoneyTestCase):
    def test_the_quoted_charge_lands_on_the_order(self):
        self.add_shipping(price="80.00")
        order = self.place()
        self.assertEqual(order.delivery_charge, Decimal("80.00"))
        self.assertEqual(order.shipping_method_name, "Standard")

    def test_a_chosen_method_is_honoured(self):
        self.add_shipping(price="80.00", code="standard")
        self.add_shipping(price="180.00", code="express")
        order = self.place(shipping_method_code="express")
        self.assertEqual(order.delivery_charge, Decimal("180.00"))

    def test_a_method_that_is_no_longer_offered_is_refused(self):
        """Never quietly substitute a different price than the one shown."""
        self.add_shipping(price="80.00", code="standard")
        with self.assertRaises(services.OrderError):
            self.place(shipping_method_code="priority")

    def test_free_shipping_over_a_threshold(self):
        self.add_shipping(price="80.00", free_over="999.00")
        order = self.place()
        self.assertEqual(order.delivery_charge, Decimal("0.00"))

    def test_a_country_we_do_not_deliver_to_is_refused(self):
        self.india.shipping_enabled = False
        self.india.save(update_fields=["shipping_enabled"])
        with self.assertRaises(services.OrderError):
            self.place()

    def test_the_delivery_estimate_follows_the_chosen_method(self):
        self.add_shipping(price="80.00")
        order = self.place()
        expected = timezone.localdate() + timezone.timedelta(days=9)
        self.assertEqual(order.estimated_delivery, expected)

    def test_no_rate_table_keeps_the_previous_flat_behaviour(self):
        with self.settings(DELIVERY_CHARGE="49.00", FREE_DELIVERY_THRESHOLD="999999.00"):
            order = self.place()
        self.assertEqual(order.delivery_charge, Decimal("49.00"))
