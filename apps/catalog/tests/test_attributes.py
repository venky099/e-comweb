"""Admin-defined attributes and faceted filtering (MST sections 5, 12, 14).

The filter semantics are the part worth pinning down, because getting them
backwards is invisible until a shopper complains that picking two fabrics
showed fewer results instead of more.
"""
from django.http import QueryDict
from django.test import TestCase
from django.urls import reverse

from apps.catalog.filters import ProductFilterForm, apply_filters, facet_options
from apps.catalog.models import (
    Attribute,
    AttributeValue,
    Product,
    ProductAttribute,
    SizeGuide,
)
from apps.core.tests.factories import create_category, create_product


class AttributeTestCase(TestCase):
    def setUp(self):
        self.category = create_category(name="Sarees")

        self.fabric = Attribute.objects.create(name="Fabric", code="fabric")
        self.silk = AttributeValue.objects.create(
            attribute=self.fabric, value="Silk", slug="silk"
        )
        self.cotton = AttributeValue.objects.create(
            attribute=self.fabric, value="Cotton", slug="cotton"
        )

        self.occasion = Attribute.objects.create(name="Occasion", code="occasion")
        self.festive = AttributeValue.objects.create(
            attribute=self.occasion, value="Festive", slug="festive"
        )
        self.casual = AttributeValue.objects.create(
            attribute=self.occasion, value="Casual", slug="casual"
        )

        self.silk_festive = self.make("Silk Festive Saree", self.silk, self.festive)
        self.silk_casual = self.make("Silk Casual Saree", self.silk, self.casual)
        self.cotton_casual = self.make("Cotton Casual Saree", self.cotton, self.casual)

    def make(self, name, *values):
        product = create_product(category=self.category, name=name)
        for value in values:
            ProductAttribute.objects.create(
                product=product, attribute=value.attribute, value=value
            )
        return product

    def filtered(self, query):
        form = ProductFilterForm(QueryDict(query))
        form.is_valid()
        return apply_filters(Product.objects.filter(is_active=True), form.cleaned_data)


class FilterSemanticsTests(AttributeTestCase):
    def test_one_value_narrows_to_products_carrying_it(self):
        self.assertEqual(
            set(self.filtered("fabric=silk")), {self.silk_festive, self.silk_casual}
        )

    def test_two_values_of_one_attribute_widen_the_results(self):
        """Silk OR cotton -- picking a second fabric shows more, not fewer."""
        self.assertEqual(self.filtered("fabric=silk&fabric=cotton").count(), 3)

    def test_a_comma_separated_value_works_the_same_as_repeated_ones(self):
        self.assertEqual(self.filtered("fabric=silk,cotton").count(), 3)

    def test_two_different_attributes_narrow_the_results(self):
        """Silk AND festive."""
        self.assertEqual(
            list(self.filtered("fabric=silk&occasion=festive")), [self.silk_festive]
        )

    def test_an_unknown_value_matches_nothing(self):
        self.assertEqual(self.filtered("fabric=tweed").count(), 0)

    def test_an_unknown_attribute_is_ignored_rather_than_erroring(self):
        """A stale bookmark should still load the page."""
        self.assertEqual(self.filtered("neckline=round").count(), 3)

    def test_no_filter_returns_everything(self):
        self.assertEqual(self.filtered("").count(), 3)

    def test_results_are_not_duplicated_by_the_join(self):
        self.assertEqual(self.filtered("fabric=silk").count(), 2)


class FacetTests(AttributeTestCase):
    def test_facets_list_the_attributes_products_actually_use(self):
        facets = facet_options(Product.objects.filter(is_active=True))
        names = [f["name"] for f in facets["attributes"]]
        self.assertEqual(names, ["Fabric", "Occasion"])

    def test_facet_values_carry_counts(self):
        facets = facet_options(Product.objects.filter(is_active=True))
        fabric = next(f for f in facets["attributes"] if f["code"] == "fabric")
        counts = {v["slug"]: v["count"] for v in fabric["values"]}
        self.assertEqual(counts, {"silk": 2, "cotton": 1})

    def test_a_value_no_product_uses_is_not_offered(self):
        """Offering it would be a dead end -- pick it, get an empty page."""
        AttributeValue.objects.create(
            attribute=self.fabric, value="Tweed", slug="tweed"
        )
        facets = facet_options(Product.objects.filter(is_active=True))
        fabric = next(f for f in facets["attributes"] if f["code"] == "fabric")
        self.assertNotIn("tweed", [v["slug"] for v in fabric["values"]])

    def test_an_attribute_marked_unfilterable_is_not_offered(self):
        self.fabric.is_filterable = False
        self.fabric.save(update_fields=["is_filterable"])
        facets = facet_options(Product.objects.filter(is_active=True))
        self.assertEqual([f["code"] for f in facets["attributes"]], ["occasion"])

    def test_the_sidebar_renders_the_facets(self):
        response = self.client.get(reverse("catalog:product_list"))
        self.assertContains(response, "Fabric")
        self.assertContains(response, "Occasion")

    def test_a_selected_value_comes_back_ticked(self):
        response = self.client.get(reverse("catalog:product_list"), {"fabric": "silk"})
        self.assertIn("silk", response.context["selected_attributes"])

    def test_active_filters_show_a_readable_chip(self):
        form = ProductFilterForm(QueryDict("fabric=silk"))
        form.is_valid()
        chips = dict((code, value) for code, _label, value in form.active_filters)
        self.assertEqual(chips.get("fabric"), "Silk")


class SizeGuideTests(TestCase):
    def test_a_guide_with_no_rows_is_not_usable(self):
        """An empty table on the page looks broken; better to show nothing."""
        guide = SizeGuide.objects.create(name="Empty", columns=["Size"], rows=[])
        self.assertFalse(guide.is_usable)

    def test_a_populated_guide_is_usable(self):
        guide = SizeGuide.objects.create(
            name="Women's",
            columns=["Size", "Bust"],
            rows=[["S", "86"], ["M", "91"]],
        )
        self.assertTrue(guide.is_usable)


class AttributeModelTests(TestCase):
    def test_a_code_is_derived_from_the_name_when_left_blank(self):
        attribute = Attribute.objects.create(name="Sleeve Length")
        self.assertEqual(attribute.code, "sleeve-length")

    def test_a_value_slug_is_derived_from_the_value(self):
        attribute = Attribute.objects.create(name="Fabric", code="fabric")
        value = AttributeValue.objects.create(attribute=attribute, value="Raw Silk")
        self.assertEqual(value.slug, "raw-silk")

    def test_a_product_shows_its_value(self):
        attribute = Attribute.objects.create(name="Fabric", code="fabric")
        value = AttributeValue.objects.create(attribute=attribute, value="Silk")
        product = create_product(name="Saree")
        row = ProductAttribute.objects.create(
            product=product, attribute=attribute, value=value
        )
        self.assertEqual(row.display, "Silk")
