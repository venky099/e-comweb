"""Catalog browsing, search, filtering and staff CRUD permission tests."""
from decimal import Decimal
from pathlib import Path
from unittest import mock

from django.conf import settings
from django.test import TestCase
from django.urls import reverse

from apps.catalog import search
from apps.catalog.models import Product
from apps.core.tests.factories import (
    create_brand,
    create_category,
    create_product,
    create_staff,
    create_user,
    create_variant,
)


class ProductListingTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.electronics = create_category(name="Electronics")
        cls.laptops = create_category(name="Laptops", parent=cls.electronics)
        cls.brand = create_brand(name="Acme")

        cls.cheap = create_product(
            category=cls.laptops, price="500.00", stock=5, name="Budget Laptop", brand=cls.brand
        )
        cls.pricey = create_product(
            category=cls.laptops, price="2500.00", stock=3, name="Premium Laptop"
        )
        cls.sold_out = create_product(
            category=cls.laptops, price="900.00", stock=0, name="Sold Out Laptop"
        )
        cls.draft = create_product(
            category=cls.laptops, price="700.00", name="Hidden Draft", status=Product.Status.DRAFT
        )

    def test_listing_shows_only_published_products(self):
        response = self.client.get(reverse("catalog:product_list"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Budget Laptop")
        self.assertNotContains(response, "Hidden Draft")

    def test_price_filter(self):
        response = self.client.get(reverse("catalog:product_list"), {"max_price": "1000"})
        names = [p.name for p in response.context["products"]]
        self.assertIn("Budget Laptop", names)
        self.assertNotIn("Premium Laptop", names)

    def test_reversed_price_range_is_corrected_not_rejected(self):
        response = self.client.get(
            reverse("catalog:product_list"), {"min_price": "1000", "max_price": "100"}
        )
        self.assertEqual(response.status_code, 200)
        names = [p.name for p in response.context["products"]]
        self.assertIn("Budget Laptop", names)

    def test_in_stock_filter_excludes_sold_out(self):
        response = self.client.get(
            reverse("catalog:product_list"), {"availability": "in_stock"}
        )
        names = [p.name for p in response.context["products"]]
        self.assertIn("Budget Laptop", names)
        self.assertNotIn("Sold Out Laptop", names)

    def test_sorting_by_price_ascending(self):
        response = self.client.get(reverse("catalog:product_list"), {"sort": "price_asc"})
        prices = [p.price for p in response.context["products"]]
        self.assertEqual(prices, sorted(prices))

    def test_sorting_by_price_descending(self):
        response = self.client.get(reverse("catalog:product_list"), {"sort": "price_desc"})
        prices = [p.price for p in response.context["products"]]
        self.assertEqual(prices, sorted(prices, reverse=True))

    def test_brand_filter(self):
        response = self.client.get(
            reverse("catalog:product_list"), {"brand": self.brand.slug}
        )
        names = [p.name for p in response.context["products"]]
        self.assertEqual(names, ["Budget Laptop"])

    def test_category_page_includes_subcategory_products(self):
        response = self.client.get(
            reverse("catalog:category", args=[self.electronics.slug])
        )
        self.assertEqual(response.status_code, 200)
        names = [p.name for p in response.context["products"]]
        self.assertIn("Budget Laptop", names)

    def test_pagination_is_applied(self):
        for index in range(30):
            create_product(category=self.laptops, name=f"Bulk Product {index}")
        response = self.client.get(reverse("catalog:product_list"))
        self.assertTrue(response.context["is_paginated"])
        self.assertEqual(len(response.context["products"]), 24)

    def test_first_page_renders_pagination_without_error(self):
        """previous_page_number raises EmptyPage on page 1 if evaluated."""
        for index in range(30):
            create_product(category=self.laptops, name=f"Bulk Product {index}")
        response = self.client.get(reverse("catalog:product_list"))
        self.assertEqual(response.status_code, 200)


class SearchTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        category = create_category(name="Audio")
        cls.headphones = create_product(
            category=category, name="Noise Cancelling Headphones", stock=4
        )
        cls.speaker = create_product(category=category, name="Bluetooth Speaker", stock=4)

    def test_search_finds_by_name(self):
        response = self.client.get(reverse("catalog:search"), {"q": "Headphones"})
        names = [p.name for p in response.context["products"]]
        self.assertIn("Noise Cancelling Headphones", names)
        self.assertNotIn("Bluetooth Speaker", names)

    def test_search_with_no_results_renders_empty_state(self):
        response = self.client.get(reverse("catalog:search"), {"q": "zzzznothing"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context["products"]), 0)
        self.assertContains(response, "No products match your filters")

    def test_autocomplete_endpoint(self):
        response = self.client.get(
            reverse("catalog:search_suggestions"), {"q": "head"}
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Noise Cancelling Headphones")

    def test_autocomplete_ignores_single_character(self):
        response = self.client.get(reverse("catalog:search_suggestions"), {"q": "h"})
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Noise Cancelling Headphones")


class ProductDetailTests(TestCase):
    def setUp(self):
        self.product = create_product(price="1200.00", stock=7, name="Detail Product")

    def test_detail_page_renders(self):
        response = self.client.get(self.product.get_absolute_url())
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Detail Product")

    def test_view_count_increments(self):
        before = self.product.view_count
        self.client.get(self.product.get_absolute_url())
        self.product.refresh_from_db()
        self.assertEqual(self.product.view_count, before + 1)

    def test_draft_product_is_not_publicly_visible(self):
        self.product.status = Product.Status.DRAFT
        self.product.save(update_fields=["status"])
        response = self.client.get(self.product.get_absolute_url())
        self.assertEqual(response.status_code, 404)

    def test_variant_stock_endpoint_returns_server_computed_values(self):
        variant = self.product.variants.first()
        response = self.client.get(reverse("catalog:variant_stock", args=[variant.pk]))
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["available_quantity"], 7)
        self.assertEqual(Decimal(payload["price"]), self.product.price)
        self.assertTrue(payload["in_stock"])


class PricingTests(TestCase):
    """Discount maths lives on the model, not in a template."""

    def test_discount_percent(self):
        product = create_product(price="750.00", compare_at_price=Decimal("1000.00"))
        self.assertTrue(product.has_discount)
        self.assertEqual(product.discount_percent, 25)
        self.assertEqual(product.discount_amount, Decimal("250.00"))

    def test_no_discount_when_compare_price_missing(self):
        product = create_product(price="500.00", compare_at_price=None)
        self.assertFalse(product.has_discount)
        self.assertEqual(product.discount_percent, 0)

    def test_variant_inherits_product_price(self):
        product = create_product(price="400.00")
        variant = product.variants.first()
        self.assertEqual(variant.price, Decimal("400.00"))

    def test_variant_override_wins(self):
        product = create_product(price="400.00")
        variant = create_variant(product, price_override=Decimal("350.00"), sku="OVR-1")
        self.assertEqual(variant.price, Decimal("350.00"))

    def test_total_stock_sums_active_variants(self):
        product = create_product(stock=5)
        create_variant(product, stock=3, sku="EXTRA-1")
        product.refresh_from_db()
        self.assertEqual(product.total_stock, 8)


class CategoryTreeTests(TestCase):
    def test_descendant_ids_include_whole_subtree(self):
        root = create_category(name="Root")
        child = create_category(name="Child", parent=root)
        grandchild = create_category(name="Grandchild", parent=child)

        ids = root.descendant_ids()
        self.assertIn(root.pk, ids)
        self.assertIn(child.pk, ids)
        self.assertIn(grandchild.pk, ids)

    def test_full_path(self):
        root = create_category(name="Root")
        child = create_category(name="Child", parent=root)
        self.assertEqual(child.full_path, "Root > Child")


class StaffCrudPermissionTests(TestCase):
    """Catalog CRUD is delivered through the Django admin -- staff only."""

    def setUp(self):
        self.customer = create_user()
        self.staff = create_staff(superuser=True)
        self.product = create_product(name="Admin Target")

    def test_customer_cannot_reach_product_admin(self):
        self.client.force_login(self.customer)
        response = self.client.get("/admin/catalog/product/")
        self.assertEqual(response.status_code, 302)

    def test_staff_can_list_products_in_admin(self):
        self.client.force_login(self.staff)
        response = self.client.get("/admin/catalog/product/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Admin Target")

    def test_staff_can_create_a_product_through_admin(self):
        self.client.force_login(self.staff)
        category = create_category(name="Admin Category")
        response = self.client.post(
            "/admin/catalog/product/add/",
            {
                "name": "Created In Admin",
                "slug": "created-in-admin",
                "sku": "ADMIN-SKU-1",
                "category": category.pk,
                "status": Product.Status.PUBLISHED,
                "is_active": "on",
                "price": "999.00",
                "compare_at_price": "1299.00",
                "tax_rate_percent": "0.00",
                "specifications": "{}",
                "weight_grams": 0,
                # inline management forms
                "images-TOTAL_FORMS": "0",
                "images-INITIAL_FORMS": "0",
                "images-MIN_NUM_FORMS": "0",
                "images-MAX_NUM_FORMS": "1000",
                "variants-TOTAL_FORMS": "0",
                "variants-INITIAL_FORMS": "0",
                "variants-MIN_NUM_FORMS": "0",
                "variants-MAX_NUM_FORMS": "1000",
            },
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(Product.objects.filter(sku="ADMIN-SKU-1").exists())

    def test_staff_can_delete_a_product_through_admin(self):
        self.client.force_login(self.staff)
        target = create_product(name="Delete Me")
        response = self.client.post(
            f"/admin/catalog/product/{target.pk}/delete/",
            {"post": "yes"},
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Product.objects.filter(pk=target.pk).exists())


class TrigramGuardTests(TestCase):
    """Search must not emit SQL the database cannot run.

    TrigramSimilarity compiles to SIMILARITY(), which exists only where the
    pg_trgm extension is installed. A Postgres database without it answered
    every search with a 500 until migration 0002 and this guard landed.
    """

    def test_trigram_is_never_claimed_on_a_non_postgres_backend(self):
        self.assertFalse(search._has_trigram())

    def test_rank_drops_the_trigram_term_when_the_extension_is_absent(self):
        with mock.patch.object(search, "_is_postgres", return_value=True), \
                mock.patch.object(search, "_has_trigram", return_value=False):
            sql = str(search._postgres_search(Product.objects.all(), "shoes").query)
        self.assertNotIn("SIMILARITY", sql.upper())

    def test_the_extension_migration_exists(self):
        migration = (
            Path(settings.BASE_DIR)
            / "apps" / "catalog" / "migrations" / "0002_pg_trgm_extension.py"
        )
        self.assertTrue(migration.exists())
        self.assertIn("TrigramExtension", migration.read_text(encoding="utf-8"))
