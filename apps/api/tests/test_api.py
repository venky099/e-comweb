"""REST API: authentication, permissions and the checkout flow over JSON."""
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from apps.cart.models import Cart, CartItem
from apps.catalog.models import Product
from apps.core.tests.factories import (
    create_address,
    create_category,
    create_coupon,
    create_product,
    create_staff,
    create_user,
    variant_of,
)
from apps.orders.models import Order


class ApiTestCase(TestCase):
    """Shared helpers: JSON requests and bearer-token auth."""

    def json(self, method, url, data=None, token=None):
        headers = {"HTTP_AUTHORIZATION": f"Bearer {token}"} if token else {}
        kwargs = {"content_type": "application/json", **headers}
        if data is not None:
            return getattr(self.client, method)(url, data=data, **kwargs)
        return getattr(self.client, method)(url, **kwargs)

    def token_for(self, email, password):
        response = self.json(
            "post", "/api/v1/auth/login/", {"username": email, "password": password}
        )
        self.assertEqual(response.status_code, 200, response.content)
        return response.json()["access"]


class AuthApiTests(ApiTestCase):
    def test_register_returns_tokens_and_user(self):
        response = self.json(
            "post",
            "/api/v1/auth/register/",
            {
                "email": "api.user@example.test",
                "first_name": "Api",
                "last_name": "User",
                "phone": "9998887770",
                "password": "StrongPass!2345",
                "password_confirm": "StrongPass!2345",
            },
        )
        self.assertEqual(response.status_code, 201, response.content)
        payload = response.json()
        self.assertIn("access", payload)
        self.assertIn("refresh", payload)
        self.assertEqual(payload["user"]["email"], "api.user@example.test")

    def test_register_rejects_mismatched_passwords(self):
        response = self.json(
            "post",
            "/api/v1/auth/register/",
            {
                "email": "bad@example.test",
                "first_name": "Bad",
                "password": "StrongPass!2345",
                "password_confirm": "Different!2345",
            },
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("error", response.json())

    def test_register_rejects_duplicate_email(self):
        create_user(email="dupe@example.test")
        response = self.json(
            "post",
            "/api/v1/auth/register/",
            {
                "email": "dupe@example.test",
                "first_name": "Dupe",
                "password": "StrongPass!2345",
                "password_confirm": "StrongPass!2345",
            },
        )
        self.assertEqual(response.status_code, 400)

    def test_register_enforces_password_policy(self):
        response = self.json(
            "post",
            "/api/v1/auth/register/",
            {
                "email": "weak@example.test",
                "first_name": "Weak",
                "password": "12345678",
                "password_confirm": "12345678",
            },
        )
        self.assertEqual(response.status_code, 400)

    def test_login_with_email_and_access_protected_endpoint(self):
        password = "TestPass!2345"
        user = create_user(email="login@example.test", password=password)
        token = self.token_for("login@example.test", password)

        response = self.json("get", "/api/v1/auth/me/", token=token)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["email"], user.email)

    def test_login_with_bad_password_fails(self):
        create_user(email="login2@example.test", password="TestPass!2345")
        response = self.json(
            "post",
            "/api/v1/auth/login/",
            {"username": "login2@example.test", "password": "nope"},
        )
        self.assertEqual(response.status_code, 401)

    def test_protected_endpoint_requires_a_token(self):
        response = self.json("get", "/api/v1/auth/me/")
        self.assertEqual(response.status_code, 401)

    def test_refresh_issues_a_new_access_token(self):
        password = "TestPass!2345"
        create_user(email="refresh@example.test", password=password)
        login = self.json(
            "post",
            "/api/v1/auth/login/",
            {"username": "refresh@example.test", "password": password},
        )
        refresh = login.json()["refresh"]

        response = self.json("post", "/api/v1/auth/refresh/", {"refresh": refresh})
        self.assertEqual(response.status_code, 200)
        self.assertIn("access", response.json())

    def test_logout_blacklists_the_refresh_token(self):
        password = "TestPass!2345"
        create_user(email="logout@example.test", password=password)
        login = self.json(
            "post",
            "/api/v1/auth/login/",
            {"username": "logout@example.test", "password": password},
        )
        access, refresh = login.json()["access"], login.json()["refresh"]

        response = self.json("post", "/api/v1/auth/logout/", {"refresh": refresh}, token=access)
        self.assertEqual(response.status_code, 205)

        reuse = self.json("post", "/api/v1/auth/refresh/", {"refresh": refresh})
        self.assertEqual(reuse.status_code, 401)

    def test_change_password(self):
        password = "TestPass!2345"
        user = create_user(email="pwd@example.test", password=password)
        token = self.token_for("pwd@example.test", password)

        response = self.json(
            "post",
            "/api/v1/auth/change-password/",
            {"old_password": password, "new_password": "BrandNew!9876"},
            token=token,
        )
        self.assertEqual(response.status_code, 200)

        user.refresh_from_db()
        self.assertTrue(user.check_password("BrandNew!9876"))

    def test_change_password_requires_the_old_one(self):
        password = "TestPass!2345"
        create_user(email="pwd2@example.test", password=password)
        token = self.token_for("pwd2@example.test", password)

        response = self.json(
            "post",
            "/api/v1/auth/change-password/",
            {"old_password": "wrong", "new_password": "BrandNew!9876"},
            token=token,
        )
        self.assertEqual(response.status_code, 400)


class CatalogApiTests(ApiTestCase):
    @classmethod
    def setUpTestData(cls):
        cls.category = create_category(name="API Category")
        cls.cheap = create_product(
            category=cls.category, name="API Cheap", price="100.00", stock=5
        )
        cls.pricey = create_product(
            category=cls.category, name="API Pricey", price="900.00", stock=5
        )
        cls.draft = create_product(
            category=cls.category, name="API Draft", status=Product.Status.DRAFT
        )

    def test_product_list_is_public_and_paginated(self):
        response = self.client.get("/api/v1/products/")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertIn("results", payload)
        self.assertIn("total_pages", payload)
        names = [p["name"] for p in payload["results"]]
        self.assertIn("API Cheap", names)
        self.assertNotIn("API Draft", names)

    def test_product_detail_includes_variants(self):
        response = self.client.get(f"/api/v1/products/{self.cheap.slug}/")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["slug"], self.cheap.slug)
        self.assertEqual(len(payload["variants"]), 1)
        self.assertEqual(payload["variants"][0]["available_quantity"], 5)

    def test_draft_product_detail_is_hidden_from_the_public(self):
        response = self.client.get(f"/api/v1/products/{self.draft.slug}/")
        self.assertEqual(response.status_code, 404)

    def test_price_filter_and_sorting(self):
        response = self.client.get("/api/v1/products/?max_price=500")
        names = [p["name"] for p in response.json()["results"]]
        self.assertIn("API Cheap", names)
        self.assertNotIn("API Pricey", names)

        response = self.client.get("/api/v1/products/?sort=price_desc")
        prices = [Decimal(p["price"]) for p in response.json()["results"]]
        self.assertEqual(prices, sorted(prices, reverse=True))

    def test_search(self):
        response = self.client.get("/api/v1/products/?q=Pricey")
        names = [p["name"] for p in response.json()["results"]]
        self.assertEqual(names, ["API Pricey"])

    def test_categories_are_public(self):
        response = self.client.get("/api/v1/categories/")
        self.assertEqual(response.status_code, 200)

    def test_anonymous_cannot_create_a_product(self):
        response = self.json(
            "post",
            "/api/v1/products/",
            {"name": "Hacked", "sku": "HACK-1", "category": self.category.pk, "price": "1.00"},
        )
        self.assertIn(response.status_code, (401, 403))
        self.assertFalse(Product.objects.filter(sku="HACK-1").exists())

    def test_customer_cannot_create_a_product(self):
        password = "TestPass!2345"
        create_user(email="shopper@example.test", password=password)
        token = self.token_for("shopper@example.test", password)

        response = self.json(
            "post",
            "/api/v1/products/",
            {"name": "Hacked", "sku": "HACK-2", "category": self.category.pk, "price": "1.00"},
            token=token,
        )
        self.assertEqual(response.status_code, 403)
        self.assertFalse(Product.objects.filter(sku="HACK-2").exists())

    def test_staff_can_create_update_and_delete_a_product(self):
        password = "StaffPass!2345"
        create_staff(email="apistaff@example.test", password=password, superuser=True)
        token = self.token_for("apistaff@example.test", password)

        created = self.json(
            "post",
            "/api/v1/products/",
            {
                "name": "Staff Created",
                "sku": "STAFF-1",
                "category": self.category.pk,
                "price": "250.00",
                "status": Product.Status.PUBLISHED,
                "is_active": True,
            },
            token=token,
        )
        self.assertEqual(created.status_code, 201, created.content)
        slug = created.json()["slug"]

        updated = self.json(
            "patch", f"/api/v1/products/{slug}/", {"price": "199.00"}, token=token
        )
        self.assertEqual(updated.status_code, 200)
        self.assertEqual(Decimal(updated.json()["price"]), Decimal("199.00"))

        deleted = self.json("delete", f"/api/v1/products/{slug}/", token=token)
        self.assertEqual(deleted.status_code, 204)
        self.assertFalse(Product.objects.filter(sku="STAFF-1").exists())

    def test_compare_price_below_selling_price_is_rejected(self):
        password = "StaffPass!2345"
        create_staff(email="apistaff2@example.test", password=password, superuser=True)
        token = self.token_for("apistaff2@example.test", password)

        response = self.json(
            "post",
            "/api/v1/products/",
            {
                "name": "Bad Pricing",
                "sku": "BAD-1",
                "category": self.category.pk,
                "price": "500.00",
                "compare_at_price": "100.00",
            },
            token=token,
        )
        self.assertEqual(response.status_code, 400)


class CartApiTests(ApiTestCase):
    def setUp(self):
        self.password = "TestPass!2345"
        self.user = create_user(email="cartapi@example.test", password=self.password)
        self.token = self.token_for("cartapi@example.test", self.password)
        self.product = create_product(price="600.00", stock=4)
        self.variant = variant_of(self.product)

    def test_cart_requires_authentication(self):
        self.assertEqual(self.json("get", "/api/v1/cart/").status_code, 401)

    def test_add_item_and_read_totals(self):
        response = self.json(
            "post",
            "/api/v1/cart/items/",
            {"variant_id": self.variant.pk, "quantity": 2},
            token=self.token,
        )
        self.assertEqual(response.status_code, 201, response.content)
        summary = response.json()["summary"]
        self.assertEqual(Decimal(summary["subtotal"]), Decimal("1200.00"))
        self.assertEqual(summary["item_count"], 2)

    def test_quantity_is_clamped_to_stock(self):
        response = self.json(
            "post",
            "/api/v1/cart/items/",
            {"variant_id": self.variant.pk, "quantity": 99},
            token=self.token,
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["summary"]["item_count"], 4)

    def test_out_of_stock_variant_is_rejected(self):
        sold_out = create_product(price="100.00", stock=0)
        response = self.json(
            "post",
            "/api/v1/cart/items/",
            {"variant_id": variant_of(sold_out).pk, "quantity": 1},
            token=self.token,
        )
        self.assertEqual(response.status_code, 400)

    def test_unknown_variant_is_rejected(self):
        response = self.json(
            "post", "/api/v1/cart/items/", {"variant_id": 999999}, token=self.token
        )
        self.assertEqual(response.status_code, 400)

    def test_update_and_remove(self):
        self.json(
            "post",
            "/api/v1/cart/items/",
            {"variant_id": self.variant.pk, "quantity": 2},
            token=self.token,
        )
        item = CartItem.objects.get(cart__user=self.user)

        updated = self.json(
            "put", f"/api/v1/cart/items/{item.pk}/", {"quantity": 3}, token=self.token
        )
        self.assertEqual(updated.status_code, 200)
        item.refresh_from_db()
        self.assertEqual(item.quantity, 3)

        removed = self.json(
            "delete", f"/api/v1/cart/items/{item.pk}/remove/", token=self.token
        )
        self.assertEqual(removed.status_code, 200)
        self.assertFalse(CartItem.objects.filter(pk=item.pk).exists())

    def test_coupon_apply_and_remove(self):
        create_coupon(code="APICOUPON", value=Decimal("10"))
        self.json(
            "post",
            "/api/v1/cart/items/",
            {"variant_id": self.variant.pk, "quantity": 2},
            token=self.token,
        )
        applied = self.json(
            "post", "/api/v1/cart/apply-coupon/", {"code": "APICOUPON"}, token=self.token
        )
        self.assertEqual(applied.status_code, 200)
        self.assertEqual(
            Decimal(applied.json()["summary"]["coupon_discount"]), Decimal("120.00")
        )

        removed = self.json("delete", "/api/v1/cart/remove-coupon/", token=self.token)
        self.assertEqual(
            Decimal(removed.json()["summary"]["coupon_discount"]), Decimal("0.00")
        )

    def test_invalid_coupon_is_rejected(self):
        self.json(
            "post",
            "/api/v1/cart/items/",
            {"variant_id": self.variant.pk, "quantity": 1},
            token=self.token,
        )
        response = self.json(
            "post", "/api/v1/cart/apply-coupon/", {"code": "NOPE"}, token=self.token
        )
        self.assertEqual(response.status_code, 400)

    def test_one_customer_cannot_see_anothers_cart(self):
        self.json(
            "post",
            "/api/v1/cart/items/",
            {"variant_id": self.variant.pk, "quantity": 1},
            token=self.token,
        )
        other_password = "OtherPass!2345"
        create_user(email="other@example.test", password=other_password)
        other_token = self.token_for("other@example.test", other_password)

        response = self.json("get", "/api/v1/cart/", token=other_token)
        self.assertEqual(response.json()["summary"]["item_count"], 0)


class OrderApiTests(ApiTestCase):
    def setUp(self):
        self.password = "TestPass!2345"
        self.user = create_user(email="orderapi@example.test", password=self.password)
        self.address = create_address(self.user)
        self.token = self.token_for("orderapi@example.test", self.password)
        self.product = create_product(price="1200.00", stock=10)
        self.variant = variant_of(self.product)

        cart = Cart.objects.create(user=self.user)
        CartItem.objects.create(cart=cart, variant=self.variant, quantity=1)

    def test_place_an_order(self):
        response = self.json(
            "post",
            "/api/v1/orders/",
            {"address_id": self.address.pk, "payment_method": "cod"},
            token=self.token,
        )
        self.assertEqual(response.status_code, 201, response.content)
        payload = response.json()
        self.assertEqual(Decimal(payload["total_amount"]), Decimal("1200.00"))
        self.assertEqual(payload["status"], Order.Status.CONFIRMED)

    def test_cannot_order_to_someone_elses_address(self):
        stranger_address = create_address(create_user())
        response = self.json(
            "post",
            "/api/v1/orders/",
            {"address_id": stranger_address.pk, "payment_method": "cod"},
            token=self.token,
        )
        self.assertEqual(response.status_code, 400)
        self.assertFalse(Order.objects.filter(user=self.user).exists())

    def test_posted_total_is_ignored(self):
        response = self.json(
            "post",
            "/api/v1/orders/",
            {
                "address_id": self.address.pk,
                "payment_method": "cod",
                "total_amount": "1.00",
                "subtotal": "1.00",
            },
            token=self.token,
        )
        self.assertEqual(Decimal(response.json()["total_amount"]), Decimal("1200.00"))

    def test_customer_only_sees_their_own_orders(self):
        self.json(
            "post",
            "/api/v1/orders/",
            {"address_id": self.address.pk, "payment_method": "cod"},
            token=self.token,
        )
        other_password = "OtherPass!2345"
        create_user(email="nosy@example.test", password=other_password)
        other_token = self.token_for("nosy@example.test", other_password)

        response = self.json("get", "/api/v1/orders/", token=other_token)
        self.assertEqual(response.json()["count"], 0)

    def test_customer_cannot_read_another_order_by_number(self):
        created = self.json(
            "post",
            "/api/v1/orders/",
            {"address_id": self.address.pk, "payment_method": "cod"},
            token=self.token,
        )
        order_number = created.json()["order_number"]

        other_password = "OtherPass!2345"
        create_user(email="nosy2@example.test", password=other_password)
        other_token = self.token_for("nosy2@example.test", other_password)

        response = self.json("get", f"/api/v1/orders/{order_number}/", token=other_token)
        self.assertEqual(response.status_code, 404)

    def test_customer_cannot_change_order_status(self):
        created = self.json(
            "post",
            "/api/v1/orders/",
            {"address_id": self.address.pk, "payment_method": "cod"},
            token=self.token,
        )
        order_number = created.json()["order_number"]

        response = self.json(
            "put",
            f"/api/v1/orders/{order_number}/status/",
            {"status": "delivered"},
            token=self.token,
        )
        self.assertEqual(response.status_code, 403)

        order = Order.objects.get(order_number=order_number)
        self.assertNotEqual(order.status, Order.Status.DELIVERED)

    def test_staff_can_change_order_status(self):
        created = self.json(
            "post",
            "/api/v1/orders/",
            {"address_id": self.address.pk, "payment_method": "cod"},
            token=self.token,
        )
        order_number = created.json()["order_number"]

        staff_password = "StaffPass!2345"
        create_staff(email="orderstaff@example.test", password=staff_password, superuser=True)
        staff_token = self.token_for("orderstaff@example.test", staff_password)

        response = self.json(
            "put",
            f"/api/v1/orders/{order_number}/status/",
            {"status": "processing", "tracking_number": "TRK123", "note": "Packed"},
            token=staff_token,
        )
        self.assertEqual(response.status_code, 200, response.content)

        order = Order.objects.get(order_number=order_number)
        self.assertEqual(order.status, Order.Status.PROCESSING)
        self.assertEqual(order.tracking_number, "TRK123")

    def test_staff_cannot_make_an_illegal_transition(self):
        created = self.json(
            "post",
            "/api/v1/orders/",
            {"address_id": self.address.pk, "payment_method": "cod"},
            token=self.token,
        )
        order_number = created.json()["order_number"]

        staff_password = "StaffPass!2345"
        create_staff(email="orderstaff2@example.test", password=staff_password, superuser=True)
        staff_token = self.token_for("orderstaff2@example.test", staff_password)

        response = self.json(
            "put",
            f"/api/v1/orders/{order_number}/status/",
            {"status": "refunded"},
            token=staff_token,
        )
        self.assertEqual(response.status_code, 400)

    def test_cancel_an_order_restores_stock(self):
        created = self.json(
            "post",
            "/api/v1/orders/",
            {"address_id": self.address.pk, "payment_method": "cod"},
            token=self.token,
        )
        order_number = created.json()["order_number"]

        response = self.json(
            "post",
            f"/api/v1/orders/{order_number}/cancel/",
            {"reason": "Changed my mind"},
            token=self.token,
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["status"], Order.Status.CANCELLED)

        self.variant.inventory.refresh_from_db()
        self.assertEqual(self.variant.inventory.quantity_available, 10)
        self.assertEqual(self.variant.inventory.quantity_reserved, 0)


class StaffOnlyEndpointTests(ApiTestCase):
    def setUp(self):
        self.customer_password = "TestPass!2345"
        create_user(email="plain@example.test", password=self.customer_password)
        self.customer_token = self.token_for("plain@example.test", self.customer_password)

        self.staff_password = "StaffPass!2345"
        create_staff(email="boss@example.test", password=self.staff_password, superuser=True)
        self.staff_token = self.token_for("boss@example.test", self.staff_password)

    STAFF_ENDPOINTS = [
        "/api/v1/customers/",
        "/api/v1/coupons/",
        "/api/v1/dashboard/stats/",
        "/api/v1/dashboard/charts/revenue/",
    ]

    def test_anonymous_is_refused(self):
        for url in self.STAFF_ENDPOINTS:
            with self.subTest(url=url):
                self.assertIn(self.json("get", url).status_code, (401, 403))

    def test_customer_is_refused(self):
        for url in self.STAFF_ENDPOINTS:
            with self.subTest(url=url):
                response = self.json("get", url, token=self.customer_token)
                self.assertEqual(response.status_code, 403, url)

    def test_staff_is_allowed(self):
        for url in self.STAFF_ENDPOINTS:
            with self.subTest(url=url):
                response = self.json("get", url, token=self.staff_token)
                self.assertEqual(response.status_code, 200, url)

    def test_public_coupon_endpoint_hides_internal_fields(self):
        create_coupon(code="PUBLIC1", value=Decimal("10"), is_public=True)
        response = self.client.get("/api/v1/coupons/public/")
        self.assertEqual(response.status_code, 200)
        payload = response.json()[0]
        self.assertIn("code", payload)
        self.assertNotIn("used_count", payload)
        self.assertNotIn("usage_limit", payload)

    def test_staff_can_create_a_coupon(self):
        response = self.json(
            "post",
            "/api/v1/coupons/",
            {
                "code": "APICREATED",
                "discount_type": "percentage",
                "value": "15.00",
                "min_order_value": "500.00",
                "usage_limit_per_user": 1,
                "valid_from": "2026-01-01T00:00:00Z",
            },
            token=self.staff_token,
        )
        self.assertEqual(response.status_code, 201, response.content)


class ApiErrorEnvelopeTests(ApiTestCase):
    def test_errors_use_the_standard_envelope(self):
        response = self.client.get("/api/v1/products/does-not-exist/")
        self.assertEqual(response.status_code, 404)
        payload = response.json()
        self.assertIn("error", payload)
        self.assertEqual(payload["error"]["type"], "not_found")
        self.assertEqual(payload["error"]["status_code"], 404)
        self.assertIn("message", payload["error"])

    def test_validation_errors_are_labelled(self):
        response = self.json("post", "/api/v1/auth/register/", {"email": "not-an-email"})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"]["type"], "validation_error")


class ApiSchemaTests(TestCase):
    def test_openapi_schema_generates(self):
        response = self.client.get(reverse("api:schema"))
        self.assertEqual(response.status_code, 200)

    def test_swagger_ui_renders(self):
        response = self.client.get(reverse("api:swagger-ui"))
        self.assertEqual(response.status_code, 200)

    def test_spec_alias_paths_exist(self):
        """The unversioned paths named in the project spec still resolve."""
        for url in ("/api/products/", "/api/categories/", "/api/coupons/public/"):
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 200)
