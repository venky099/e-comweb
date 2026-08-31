"""Shipping quotes.

Checked against the spec's own worked example (section 29): in India, Rs.80
below Rs.1,000 and free at or above it.
"""
from decimal import Decimal

from django.test import TestCase

from apps.core.tests.factories import create_category, create_product, create_variant
from apps.geo.models import Country, Currency
from apps.shipping import services
from apps.shipping.models import ShippingMethod, ShippingRate, ShippingZone


class Item:
    """The shape quote() needs: a variant and a quantity."""

    def __init__(self, variant, quantity=1):
        self.variant = variant
        self.quantity = quantity


class ShippingTestCase(TestCase):
    def setUp(self):
        self.inr = Currency.objects.create(
            code="INR", name="Rupee", symbol="₹", is_base=True
        )
        self.india = Country.objects.create(iso2="IN", name="India", currency=self.inr)
        self.usa = Country.objects.create(
            iso2="US", name="United States", currency=self.inr
        )

        self.zone = ShippingZone.objects.create(name="Zone 1", sort_order=1)
        self.zone.countries.add(self.india)

        self.standard = ShippingMethod.objects.create(
            name="Standard", code="standard", min_days=7, max_days=12, sort_order=1
        )
        self.express = ShippingMethod.objects.create(
            name="Express", code="express", min_days=3, max_days=5, sort_order=2
        )

        category = create_category(name="Sarees")
        product = create_product(
            category=category, name="Silk Saree", price=Decimal("5000.00")
        )
        self.variant = create_variant(product)
        self.variant.weight_grams = 400
        self.variant.save(update_fields=["weight_grams"])

    def rate(self, method=None, price="80.00", **kwargs):
        return ShippingRate.objects.create(
            zone=kwargs.pop("zone", self.zone),
            method=method or self.standard,
            price=Decimal(price),
            **kwargs,
        )


class QuoteTests(ShippingTestCase):
    def test_the_specs_india_example(self):
        """Rs.80 below Rs.999, free at or above it."""
        self.rate(price="80.00", free_over=Decimal("999.00"))

        under = services.quote([Item(self.variant)], self.india, Decimal("500.00"))
        self.assertEqual(under[0].price, Decimal("80.00"))
        self.assertFalse(under[0].is_free)

        over = services.quote([Item(self.variant)], self.india, Decimal("999.00"))
        self.assertEqual(over[0].price, Decimal("0.00"))
        self.assertTrue(over[0].is_free)

    def test_options_carry_a_delivery_estimate(self):
        self.rate()
        option = services.quote([Item(self.variant)], self.india, Decimal("500"))[0]
        self.assertEqual(option.estimate, "7-12 days")

    def test_a_country_in_no_zone_gets_no_options(self):
        self.rate()
        self.assertEqual(
            services.quote([Item(self.variant)], self.usa, Decimal("500")), []
        )

    def test_a_country_that_does_not_accept_delivery_gets_no_options(self):
        self.rate()
        self.india.shipping_enabled = False
        self.india.save(update_fields=["shipping_enabled"])
        self.assertEqual(
            services.quote([Item(self.variant)], self.india, Decimal("500")), []
        )

    def test_an_empty_cart_gets_no_options(self):
        self.rate()
        self.assertEqual(services.quote([], self.india, Decimal("0")), [])

    def test_options_are_cheapest_first(self):
        self.rate(method=self.standard, price="80.00")
        self.rate(method=self.express, price="180.00")
        options = services.quote([Item(self.variant)], self.india, Decimal("500"))
        self.assertEqual([o.code for o in options], ["standard", "express"])
        self.assertEqual(services.default_option(options).code, "standard")

    def test_an_inactive_method_is_not_offered(self):
        self.rate(method=self.express, price="180.00")
        self.express.is_active = False
        self.express.save(update_fields=["is_active"])
        self.assertEqual(
            services.quote([Item(self.variant)], self.india, Decimal("500")), []
        )


class WeightBandTests(ShippingTestCase):
    def test_the_matching_weight_band_is_used(self):
        self.rate(min_weight_grams=0, max_weight_grams=1000, price="80.00")
        self.rate(min_weight_grams=1000, max_weight_grams=None, price="140.00")

        light = services.quote([Item(self.variant, 2)], self.india, Decimal("500"))
        self.assertEqual(light[0].price, Decimal("80.00"))  # 800g

        heavy = services.quote([Item(self.variant, 3)], self.india, Decimal("500"))
        self.assertEqual(heavy[0].price, Decimal("140.00"))  # 1200g

    def test_bands_are_half_open_so_a_boundary_matches_exactly_one(self):
        low = self.rate(min_weight_grams=0, max_weight_grams=1000, price="80.00")
        high = self.rate(min_weight_grams=1000, max_weight_grams=None, price="140.00")
        self.assertFalse(low.covers(1000, Decimal("500")))
        self.assertTrue(high.covers(1000, Decimal("500")))

    def test_weight_falls_back_to_the_product_when_the_variant_has_none(self):
        self.variant.weight_grams = None
        self.variant.save(update_fields=["weight_grams"])
        self.variant.product.weight_grams = 250
        self.variant.product.save(update_fields=["weight_grams"])
        self.assertEqual(services.cart_weight_grams([Item(self.variant, 2)]), 500)

    def test_an_unweighed_parcel_is_treated_as_weightless_not_an_error(self):
        self.variant.weight_grams = None
        self.variant.save(update_fields=["weight_grams"])
        # Product.weight_grams is not nullable: zero is its unweighed state.
        self.variant.product.weight_grams = 0
        self.variant.product.save(update_fields=["weight_grams"])
        self.rate(min_weight_grams=0, max_weight_grams=1000, price="80.00")
        options = services.quote([Item(self.variant)], self.india, Decimal("500"))
        self.assertEqual(options[0].price, Decimal("80.00"))


class OrderValueBandTests(ShippingTestCase):
    def test_a_rate_can_be_limited_by_order_value(self):
        rate = self.rate(min_order_value=Decimal("1000"), price="50.00")
        self.assertFalse(rate.covers(400, Decimal("999")))
        self.assertTrue(rate.covers(400, Decimal("1000")))

    def test_free_over_wins_over_the_listed_price(self):
        rate = self.rate(price="80.00", free_over=Decimal("999"))
        self.assertEqual(rate.charge_for(Decimal("1200")), Decimal("0.00"))
        self.assertEqual(rate.charge_for(Decimal("998")), Decimal("80.00"))


class LegacyFallbackTests(ShippingTestCase):
    def test_flat_charge_matches_the_old_settings_behaviour(self):
        with self.settings(DELIVERY_CHARGE="49.00", FREE_DELIVERY_THRESHOLD="999.00"):
            self.assertEqual(
                services.legacy_flat_charge(Decimal("500")), Decimal("49.00")
            )
            self.assertEqual(
                services.legacy_flat_charge(Decimal("999")), Decimal("0.00")
            )
