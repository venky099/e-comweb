"""Bulk product upload (MST spec section 38).

The properties that matter for an import are not really about parsing: a file
with one bad row must change nothing, and the dry run must predict the real
run exactly, or the confirmation screen is a lie.
"""
import io

from django.test import TestCase

from apps.catalog import importer
from apps.catalog.models import (
    Attribute,
    AttributeValue,
    Product,
    ProductAttribute,
    ProductVariant,
)
from apps.inventory.models import Inventory

HEADER = ",".join(importer.ALL_COLUMNS)


def csv_file(*rows):
    """A CSV upload with the standard header and the given data rows."""
    body = "\n".join([HEADER, *rows])
    return io.BytesIO(body.encode("utf-8"))


def row(
    sku="MS-1001",
    name="Silk Saree",
    category="Sarees",
    price="5000.00",
    size="M",
    color="Red",
    stock="10",
    attributes="",
    **overrides,
):
    values = {
        "sku": sku, "name": name, "category": category, "price": price,
        "brand": overrides.get("brand", "Meridian"),
        "short_description": "", "description": "",
        "compare_at_price": overrides.get("compare_at_price", ""),
        "size": size, "color": color, "color_hex": "",
        "stock": stock, "weight_grams": overrides.get("weight_grams", "400"),
        "barcode": "", "tags": "", "is_active": overrides.get("is_active", "true"),
        "attributes": attributes,
    }
    return ",".join(values[column] for column in importer.ALL_COLUMNS)


class ValidationTests(TestCase):
    def test_a_missing_required_column_is_reported_once(self):
        upload = io.BytesIO(b"sku,name\nMS-1,Saree\n")
        report = importer.preview(upload)
        self.assertFalse(report.ok)
        self.assertIn("category", report.errors[0][1])

    def test_an_empty_file_is_reported(self):
        report = importer.preview(csv_file())
        self.assertFalse(report.ok)
        self.assertIn("no rows", report.errors[0][1])

    def test_a_bad_price_names_the_row(self):
        report = importer.preview(csv_file(row(), row(sku="MS-2", price="abc")))
        self.assertFalse(report.ok)
        self.assertEqual(report.errors[0][0], 3)
        self.assertIn("price", report.errors[0][1])

    def test_a_compare_price_below_the_price_is_refused(self):
        report = importer.preview(csv_file(row(price="500", compare_at_price="100")))
        self.assertFalse(report.ok)
        self.assertIn("discount", report.errors[0][1])

    def test_the_same_variant_twice_in_one_file_is_refused(self):
        report = importer.preview(csv_file(row(), row()))
        self.assertFalse(report.ok)
        self.assertIn("already has", report.errors[0][1])

    def test_the_same_product_in_two_sizes_is_fine(self):
        """One row per variant is the normal shape of a clothing catalogue."""
        report = importer.preview(csv_file(row(size="M"), row(size="L")))
        self.assertTrue(report.ok, report.errors)
        self.assertEqual(report.created_products, 1)
        self.assertEqual(report.created_variants, 2)

    def test_every_bad_row_is_reported_not_just_the_first(self):
        report = importer.preview(
            csv_file(row(sku="A", price="x"), row(sku="B", price="y"))
        )
        self.assertEqual(len(report.errors), 2)


class AttributeTests(TestCase):
    def setUp(self):
        self.fabric = Attribute.objects.create(name="Fabric", code="fabric")
        AttributeValue.objects.create(attribute=self.fabric, value="Silk", slug="silk")

    def test_a_known_attribute_is_applied(self):
        report = importer.commit(csv_file(row(attributes="fabric=Silk")))
        self.assertTrue(report.ok, report.errors)
        product = Product.objects.get(sku="MS-1001")
        self.assertEqual(
            list(ProductAttribute.objects.filter(product=product).values_list(
                "value__value", flat=True
            )),
            ["Silk"],
        )

    def test_an_unknown_attribute_fails_the_row(self):
        report = importer.preview(csv_file(row(attributes="neckline=Round")))
        self.assertFalse(report.ok)
        self.assertIn("Unknown attribute", report.errors[0][1])

    def test_an_unknown_value_fails_the_row_rather_than_dropping_the_tag(self):
        report = importer.preview(csv_file(row(attributes="fabric=Kryptonite")))
        self.assertFalse(report.ok)
        self.assertIn("not a value of Fabric", report.errors[0][1])

    def test_malformed_attribute_text_is_explained(self):
        report = importer.preview(csv_file(row(attributes="silk")))
        self.assertFalse(report.ok)
        self.assertIn("fabric=Silk", report.errors[0][1])


class WriteTests(TestCase):
    def test_a_dry_run_writes_nothing(self):
        report = importer.preview(csv_file(row()))
        self.assertTrue(report.ok, report.errors)
        self.assertEqual(Product.objects.count(), 0)
        self.assertTrue(report.dry_run)

    def test_a_commit_creates_the_product_variant_and_stock(self):
        report = importer.commit(csv_file(row(stock="42")))
        self.assertTrue(report.ok, report.errors)

        product = Product.objects.get(sku="MS-1001")
        variant = ProductVariant.objects.get(product=product, size="M", color="Red")
        self.assertEqual(product.name, "Silk Saree")
        self.assertEqual(variant.weight_grams, 400)
        self.assertEqual(
            Inventory.objects.get(variant=variant).quantity_available, 42
        )

    def test_one_bad_row_leaves_the_catalogue_untouched(self):
        """Working out which of 400 rows landed is the thing to avoid."""
        report = importer.commit(
            csv_file(row(sku="GOOD-1"), row(sku="BAD-1", price="nope"))
        )
        self.assertFalse(report.ok)
        self.assertEqual(Product.objects.count(), 0)

    def test_re_importing_updates_rather_than_duplicating(self):
        importer.commit(csv_file(row(name="Silk Saree")))
        report = importer.commit(csv_file(row(name="Renamed Saree")))
        self.assertTrue(report.ok, report.errors)
        self.assertEqual(Product.objects.filter(sku="MS-1001").count(), 1)
        self.assertEqual(Product.objects.get(sku="MS-1001").name, "Renamed Saree")

    def test_the_dry_run_predicts_the_real_run_exactly(self):
        """Otherwise the confirmation screen is a lie."""
        rows = [row(sku="A", size="M"), row(sku="A", size="L"), row(sku="B")]
        preview = importer.preview(csv_file(*rows))
        actual = importer.commit(csv_file(*rows))

        self.assertEqual(preview.created_products, actual.created_products)
        self.assertEqual(preview.updated_products, actual.updated_products)
        self.assertEqual(preview.created_variants, actual.created_variants)
        self.assertEqual(preview.updated_variants, actual.updated_variants)

    def test_the_prediction_still_holds_on_a_second_import(self):
        rows = [row(sku="A", size="M"), row(sku="A", size="L")]
        importer.commit(csv_file(*rows))

        preview = importer.preview(csv_file(*rows))
        actual = importer.commit(csv_file(*rows))
        self.assertEqual(preview.updated_products, actual.updated_products)
        self.assertEqual(preview.updated_variants, actual.updated_variants)
        self.assertEqual(preview.created_variants, 0)


class EncodingTests(TestCase):
    def test_a_utf8_bom_does_not_break_the_first_column(self):
        """Excel writes one, and it would make 'sku' unmatchable."""
        body = ("﻿" + HEADER + "\n" + row()).encode("utf-8")
        report = importer.preview(io.BytesIO(body))
        self.assertTrue(report.ok, report.errors)

    def test_the_template_round_trips_through_the_importer(self):
        Attribute.objects.create(name="Fabric", code="fabric")
        AttributeValue.objects.create(
            attribute=Attribute.objects.get(code="fabric"), value="Silk", slug="silk"
        )
        for name, code in [("Occasion", "occasion"), ("Gender", "gender")]:
            attribute = Attribute.objects.create(name=name, code=code)
            AttributeValue.objects.create(
                attribute=attribute,
                value="Festive" if code == "occasion" else "Women",
                slug="festive" if code == "occasion" else "women",
            )

        report = importer.preview(io.BytesIO(importer.template_csv().encode("utf-8")))
        self.assertTrue(report.ok, report.errors)
