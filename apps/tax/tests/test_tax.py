"""Tax resolution and arithmetic.

Section 27 forbids hard-coding Indian GST, so the India cases here are worth
reading as proof of that: CGST/SGST/IGST behaviour comes entirely from three
rows differing in ``applies_when``, and swapping the destination state is the
only thing that changes the outcome.
"""
from decimal import Decimal

from django.test import TestCase, override_settings
from django.utils import timezone

from apps.core.tests.factories import create_category, create_product, create_variant
from apps.geo.models import Country, Currency, State
from apps.tax import services
from apps.tax.models import TaxRule


class Item:
    """The shape compute() needs: a line total and a product."""

    def __init__(self, variant, line_total):
        self.variant = variant
        self.line_total = Decimal(line_total)


class TaxTestCase(TestCase):
    def setUp(self):
        self.inr = Currency.objects.create(
            code="INR", name="Rupee", symbol="₹", is_base=True
        )
        self.india = Country.objects.create(
            iso2="IN", name="India", currency=self.inr
        )
        self.karnataka = State.objects.create(country=self.india, name="Karnataka")
        self.kerala = State.objects.create(country=self.india, name="Kerala")
        self.today = timezone.now().date()

        self.category = create_category(name="Sarees")
        self.product = create_product(
            category=self.category, name="Silk Saree", price=Decimal("5000.00")
        )
        self.variant = create_variant(self.product)

    def rule(self, name, percent, applies_when=TaxRule.AppliesWhen.ANY, **kwargs):
        return TaxRule.objects.create(
            name=name,
            country=kwargs.pop("country", self.india),
            percent=Decimal(percent),
            applies_when=applies_when,
            effective_from=kwargs.pop("effective_from", self.today),
            **kwargs,
        )

    def items(self, total="5000.00"):
        return [Item(self.variant, total)]


@override_settings(TAX_ORIGIN_STATE="Karnataka")
class IndiaGstTests(TaxTestCase):
    def setUp(self):
        super().setUp()
        self.rule("CGST", "9.000", TaxRule.AppliesWhen.INTRA_STATE)
        self.rule("SGST", "9.000", TaxRule.AppliesWhen.INTRA_STATE)
        self.rule("IGST", "18.000", TaxRule.AppliesWhen.INTER_STATE)

    def test_within_the_sellers_state_charges_cgst_and_sgst(self):
        result = services.compute(self.items(), self.india, self.karnataka)
        self.assertEqual(result.names, ["CGST", "SGST"])
        self.assertEqual(result.total, Decimal("900.00"))

    def test_across_state_lines_charges_igst(self):
        result = services.compute(self.items(), self.india, self.kerala)
        self.assertEqual(result.names, ["IGST"])
        self.assertEqual(result.total, Decimal("900.00"))

    def test_both_routes_cost_the_customer_the_same(self):
        intra = services.compute(self.items(), self.karnataka.country, self.karnataka)
        inter = services.compute(self.items(), self.india, self.kerala)
        self.assertEqual(intra.total, inter.total)

    def test_an_unknown_destination_state_charges_no_conditional_tax(self):
        """Guessing intra vs inter would mean charging the wrong tax."""
        result = services.compute(self.items(), self.india, None)
        self.assertEqual(result.total, Decimal("0.00"))


class RuleResolutionTests(TaxTestCase):
    def test_a_category_rule_replaces_the_country_default(self):
        self.rule("VAT", "20.000")
        self.rule("VAT", "5.000", category=self.category)
        result = services.compute(self.items(), self.india)
        self.assertEqual(result.total, Decimal("250.00"))

    def test_a_rule_for_another_category_does_not_apply(self):
        other = create_category(name="Electronics", slug="electronics-tax")
        self.rule("VAT", "20.000")
        self.rule("VAT", "5.000", category=other)
        result = services.compute(self.items(), self.india)
        self.assertEqual(result.total, Decimal("1000.00"))

    def test_a_state_rule_only_applies_to_that_state(self):
        self.rule("Local", "10.000", state=self.kerala)
        self.assertEqual(
            services.compute(self.items(), self.india, self.kerala).total,
            Decimal("500.00"),
        )
        self.assertEqual(
            services.compute(self.items(), self.india, self.karnataka).total,
            Decimal("0.00"),
        )

    def test_an_expired_rule_is_ignored(self):
        yesterday = self.today - timezone.timedelta(days=2)
        self.rule(
            "Old", "20.000", effective_from=yesterday, effective_to=self.today - timezone.timedelta(days=1)
        )
        self.assertEqual(services.compute(self.items(), self.india).total, Decimal("0.00"))

    def test_a_future_rule_is_ignored(self):
        self.rule("Future", "20.000", effective_from=self.today + timezone.timedelta(days=7))
        self.assertEqual(services.compute(self.items(), self.india).total, Decimal("0.00"))

    def test_an_inactive_rule_is_ignored(self):
        self.rule("Off", "20.000", is_active=False)
        self.assertEqual(services.compute(self.items(), self.india).total, Decimal("0.00"))

    def test_no_country_means_no_tax(self):
        self.rule("VAT", "20.000")
        self.assertEqual(services.compute(self.items(), None).total, Decimal("0.00"))

    def test_falls_back_to_the_products_own_rate_when_no_rule_matches(self):
        self.product.tax_rate_percent = Decimal("12.000")
        self.product.save(update_fields=["tax_rate_percent"])
        result = services.compute(self.items(), self.india)
        self.assertEqual(result.names, ["Tax"])
        self.assertEqual(result.total, Decimal("600.00"))


class RoundingTests(TaxTestCase):
    def test_tax_is_rounded_once_across_the_order_not_per_line(self):
        """Rounding each line then summing disagrees with the printed total."""
        self.rule("GST", "18.000")
        items = [Item(self.variant, "0.10") for _ in range(10)]
        result = services.compute(items, self.india)
        # 10 x 0.10 = 1.00 at 18% is 0.18. Rounding each line first gives
        # 10 x 0.02 = 0.20, which would be wrong by two paise.
        self.assertEqual(result.total, Decimal("0.18"))

    def test_a_line_total_of_zero_produces_no_tax_line(self):
        self.rule("GST", "18.000")
        result = services.compute([Item(self.variant, "0.00")], self.india)
        self.assertEqual(result.lines, [])
        self.assertFalse(result)

    def test_empty_items_produce_no_tax(self):
        self.rule("GST", "18.000")
        self.assertEqual(services.compute([], self.india).total, Decimal("0.00"))
