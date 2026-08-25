"""Cart behaviour: stock clamping, totals, guest/user merge."""
from decimal import Decimal

from django.conf import settings
from django.test import TestCase
from django.urls import reverse

from apps.cart.models import Cart, CartItem
from apps.cart.services import CartError, merge_carts
from apps.core.tests.factories import (
    create_coupon,
    create_product,
    create_user,
    variant_of,
)
from apps.coupons.models import Coupon


class CartMutationTests(TestCase):
    def setUp(self):
        self.user = create_user()
        self.product = create_product(price="500.00", stock=5)
        self.variant = variant_of(self.product)
        self.client.force_login(self.user)

    def _add(self, quantity=1, variant=None):
        return self.client.post(
            reverse("cart:add"),
            {"variant_id": (variant or self.variant).pk, "quantity": quantity},
        )

    def test_add_to_cart(self):
        self._add(2)
        item = CartItem.objects.get(cart__user=self.user, variant=self.variant)
        self.assertEqual(item.quantity, 2)

    def test_adding_twice_accumulates(self):
        self._add(2)
        self._add(1)
        item = CartItem.objects.get(cart__user=self.user)
        self.assertEqual(item.quantity, 3)

    def test_quantity_is_clamped_to_available_stock(self):
        """Asking for more than exists silently caps at what we have."""
        self._add(99)
        item = CartItem.objects.get(cart__user=self.user)
        self.assertEqual(item.quantity, 5)

    def test_quantity_is_clamped_to_per_item_maximum(self):
        generous = create_product(price="100.00", stock=500)
        self._add(999, variant=variant_of(generous))
        item = CartItem.objects.get(cart__user=self.user, variant=variant_of(generous))
        self.assertEqual(item.quantity, settings.MAX_CART_QUANTITY_PER_ITEM)

    def test_cannot_add_out_of_stock_item(self):
        sold_out = create_product(price="100.00", stock=0)
        self._add(1, variant=variant_of(sold_out))
        self.assertFalse(
            CartItem.objects.filter(variant=variant_of(sold_out)).exists()
        )

    def test_cannot_add_inactive_product(self):
        self.product.is_active = False
        self.product.save(update_fields=["is_active"])
        self._add(1)
        self.assertFalse(CartItem.objects.filter(cart__user=self.user).exists())

    def test_update_quantity(self):
        self._add(1)
        item = CartItem.objects.get(cart__user=self.user)
        self.client.post(reverse("cart:update", args=[item.pk]), {"quantity": 4})
        item.refresh_from_db()
        self.assertEqual(item.quantity, 4)

    def test_update_quantity_above_stock_is_clamped(self):
        self._add(1)
        item = CartItem.objects.get(cart__user=self.user)
        self.client.post(reverse("cart:update", args=[item.pk]), {"quantity": 50})
        item.refresh_from_db()
        self.assertEqual(item.quantity, 5)

    def test_update_quantity_to_zero_removes_the_row(self):
        self._add(2)
        item = CartItem.objects.get(cart__user=self.user)
        self.client.post(reverse("cart:update", args=[item.pk]), {"quantity": 0})
        self.assertFalse(CartItem.objects.filter(pk=item.pk).exists())

    def test_increment_and_decrement(self):
        self._add(2)
        item = CartItem.objects.get(cart__user=self.user)

        self.client.post(reverse("cart:increment", args=[item.pk]))
        item.refresh_from_db()
        self.assertEqual(item.quantity, 3)

        self.client.post(reverse("cart:decrement", args=[item.pk]))
        item.refresh_from_db()
        self.assertEqual(item.quantity, 2)

    def test_decrement_to_zero_removes_row(self):
        self._add(1)
        item = CartItem.objects.get(cart__user=self.user)
        self.client.post(reverse("cart:decrement", args=[item.pk]))
        self.assertFalse(CartItem.objects.filter(pk=item.pk).exists())

    def test_remove_item(self):
        self._add(1)
        item = CartItem.objects.get(cart__user=self.user)
        self.client.post(reverse("cart:remove", args=[item.pk]))
        self.assertFalse(CartItem.objects.filter(pk=item.pk).exists())

    def test_clear_cart(self):
        self._add(1)
        self.client.post(reverse("cart:clear"))
        self.assertEqual(CartItem.objects.filter(cart__user=self.user).count(), 0)

    def test_cannot_touch_another_users_cart_item(self):
        self._add(1)
        item = CartItem.objects.get(cart__user=self.user)

        intruder = create_user()
        self.client.force_login(intruder)
        self.client.post(reverse("cart:remove", args=[item.pk]))
        self.assertTrue(CartItem.objects.filter(pk=item.pk).exists())


class CartTotalsTests(TestCase):
    """Totals are computed by the model, never supplied by the client."""

    def setUp(self):
        self.user = create_user()
        self.cart = Cart.objects.create(user=self.user)

    def _add(self, price, mrp, quantity, stock=20):
        product = create_product(price=price, compare_at_price=Decimal(mrp), stock=stock)
        return CartItem.objects.create(
            cart=self.cart, variant=variant_of(product), quantity=quantity
        )

    def test_subtotal_and_savings(self):
        self._add("500.00", "700.00", 2)
        self._add("300.00", "300.00", 1)

        self.assertEqual(self.cart.subtotal, Decimal("1300.00"))
        self.assertEqual(self.cart.mrp_total, Decimal("1700.00"))
        self.assertEqual(self.cart.product_discount, Decimal("400.00"))
        self.assertEqual(self.cart.item_count, 3)

    def test_delivery_charge_applies_below_threshold(self):
        self._add("100.00", "100.00", 1)
        self.assertEqual(self.cart.delivery_charge, Decimal(settings.DELIVERY_CHARGE))

    def test_delivery_is_free_above_threshold(self):
        self._add("2000.00", "2000.00", 1)
        self.assertEqual(self.cart.delivery_charge, Decimal("0.00"))

    def test_free_delivery_shortfall(self):
        self._add("500.00", "500.00", 1)
        expected = Decimal(settings.FREE_DELIVERY_THRESHOLD) - Decimal("500.00")
        self.assertEqual(self.cart.free_delivery_shortfall, expected)

    def test_total_includes_delivery(self):
        self._add("100.00", "100.00", 1)
        self.assertEqual(
            self.cart.total, Decimal("100.00") + Decimal(settings.DELIVERY_CHARGE)
        )

    def test_coupon_discount_is_recomputed_not_stored(self):
        self._add("1000.00", "1000.00", 1)
        coupon = create_coupon(code="TEN", value=Decimal("10"))
        self.cart.coupon = coupon
        self.cart.save(update_fields=["coupon"])

        self.assertEqual(self.cart.coupon_discount, Decimal("100.00"))

        # Deactivating the coupon must drop the discount on the next read,
        # even though the FK is still attached to the cart.
        coupon.is_active = False
        coupon.save(update_fields=["is_active"])
        self.cart.refresh_from_db()
        self.assertEqual(self.cart.coupon_discount, Decimal("0.00"))

    def test_empty_cart_has_zero_totals(self):
        self.assertEqual(self.cart.subtotal, Decimal("0.00"))
        self.assertEqual(self.cart.delivery_charge, Decimal("0.00"))
        self.assertEqual(self.cart.total, Decimal("0.00"))
        self.assertTrue(self.cart.is_empty)


class CartStockValidationTests(TestCase):
    def setUp(self):
        self.user = create_user()
        self.cart = Cart.objects.create(user=self.user)
        self.product = create_product(price="100.00", stock=10)
        self.variant = variant_of(self.product)
        self.item = CartItem.objects.create(
            cart=self.cart, variant=self.variant, quantity=8
        )

    def test_stock_problem_detected_when_stock_drops(self):
        inventory = self.variant.inventory
        inventory.quantity_available = 3
        inventory.save(update_fields=["quantity_available"])

        problems = self.cart.stock_problems()
        self.assertEqual(len(problems), 1)

    def test_clamp_reduces_quantity_to_available(self):
        inventory = self.variant.inventory
        inventory.quantity_available = 3
        inventory.save(update_fields=["quantity_available"])

        self.assertTrue(self.item.clamp_quantity())
        self.item.refresh_from_db()
        self.assertEqual(self.item.quantity, 3)

    def test_clamp_deletes_row_when_stock_gone(self):
        inventory = self.variant.inventory
        inventory.quantity_available = 0
        inventory.save(update_fields=["quantity_available"])

        self.item.clamp_quantity()
        self.assertFalse(CartItem.objects.filter(pk=self.item.pk).exists())


class GuestCartMergeTests(TestCase):
    """A guest cart folds into the user's cart at login."""

    def setUp(self):
        self.product = create_product(price="200.00", stock=10)
        self.variant = variant_of(self.product)
        self.password = "TestPass!2345"
        self.user = create_user(email="merge@example.test", password=self.password)

    def test_guest_cart_is_merged_on_login(self):
        self.client.post(
            reverse("cart:add"), {"variant_id": self.variant.pk, "quantity": 2}
        )
        self.assertEqual(
            CartItem.objects.filter(cart__user__isnull=True).count(), 1
        )

        self.client.post(
            reverse("accounts:login"),
            {"username": "merge@example.test", "password": self.password},
        )
        # The merge runs on the next request through the middleware.
        self.client.get(reverse("cart:detail"))

        item = CartItem.objects.get(cart__user=self.user)
        self.assertEqual(item.quantity, 2)
        self.assertFalse(Cart.objects.filter(user__isnull=True).exists())

    def test_merge_sums_quantities_and_respects_stock(self):
        guest_cart = Cart.objects.create(session_key="guest-session-key")
        CartItem.objects.create(cart=guest_cart, variant=self.variant, quantity=6)

        user_cart = Cart.objects.create(user=self.user)
        CartItem.objects.create(cart=user_cart, variant=self.variant, quantity=6)

        merge_carts(guest_cart, user_cart)

        item = CartItem.objects.get(cart=user_cart)
        # 6 + 6 = 12, clamped to the 10 in stock.
        self.assertEqual(item.quantity, 10)
        self.assertFalse(Cart.objects.filter(pk=guest_cart.pk).exists())


class CouponApplicationTests(TestCase):
    def setUp(self):
        self.user = create_user()
        self.client.force_login(self.user)
        self.product = create_product(price="2000.00", stock=10)
        self.client.post(
            reverse("cart:add"), {"variant_id": variant_of(self.product).pk, "quantity": 1}
        )
        self.cart = Cart.objects.get(user=self.user)

    def _apply(self, code):
        return self.client.post(reverse("coupons:apply"), {"code": code}, follow=True)

    def test_valid_percentage_coupon(self):
        create_coupon(code="SAVE10", value=Decimal("10"))
        self._apply("SAVE10")
        self.cart.refresh_from_db()
        self.assertEqual(self.cart.coupon.code, "SAVE10")
        self.assertEqual(self.cart.coupon_discount, Decimal("200.00"))

    def test_percentage_cap_is_enforced(self):
        create_coupon(
            code="CAPPED", value=Decimal("50"), max_discount_amount=Decimal("150.00")
        )
        self._apply("CAPPED")
        self.cart.refresh_from_db()
        self.assertEqual(self.cart.coupon_discount, Decimal("150.00"))

    def test_fixed_amount_coupon(self):
        create_coupon(
            code="FLAT250",
            discount_type=Coupon.DiscountType.FIXED,
            value=Decimal("250.00"),
        )
        self._apply("FLAT250")
        self.cart.refresh_from_db()
        self.assertEqual(self.cart.coupon_discount, Decimal("250.00"))

    def test_unknown_code_is_rejected(self):
        response = self._apply("DOESNOTEXIST")
        self.cart.refresh_from_db()
        self.assertIsNone(self.cart.coupon)
        self.assertContains(response, "not valid")

    def test_expired_coupon_is_rejected(self):
        from django.utils import timezone

        create_coupon(
            code="OLD",
            value=Decimal("10"),
            valid_from=timezone.now() - timezone.timedelta(days=10),
            valid_to=timezone.now() - timezone.timedelta(days=1),
        )
        response = self._apply("OLD")
        self.cart.refresh_from_db()
        self.assertIsNone(self.cart.coupon)
        self.assertContains(response, "expired")

    def test_minimum_order_value_is_enforced(self):
        create_coupon(code="BIGSPEND", value=Decimal("10"), min_order_value=Decimal("9999"))
        response = self._apply("BIGSPEND")
        self.cart.refresh_from_db()
        self.assertIsNone(self.cart.coupon)
        self.assertContains(response, "more to use this coupon")

    def test_exhausted_coupon_is_rejected(self):
        coupon = create_coupon(code="SOLDOUT", value=Decimal("10"), usage_limit=1)
        coupon.used_count = 1
        coupon.save(update_fields=["used_count"])

        response = self._apply("SOLDOUT")
        self.cart.refresh_from_db()
        self.assertIsNone(self.cart.coupon)
        self.assertContains(response, "usage limit")

    def test_inactive_coupon_is_rejected(self):
        create_coupon(code="DISABLED", value=Decimal("10"), is_active=False)
        response = self._apply("DISABLED")
        self.cart.refresh_from_db()
        self.assertIsNone(self.cart.coupon)
        self.assertContains(response, "no longer active")

    def test_coupon_code_is_case_insensitive(self):
        create_coupon(code="MIXEDCASE", value=Decimal("10"))
        self._apply("mixedcase")
        self.cart.refresh_from_db()
        self.assertIsNotNone(self.cart.coupon)

    def test_removing_a_coupon(self):
        create_coupon(code="REMOVEME", value=Decimal("10"))
        self._apply("REMOVEME")
        self.client.post(reverse("coupons:remove"), follow=True)
        self.cart.refresh_from_db()
        self.assertIsNone(self.cart.coupon)

    def test_client_cannot_post_a_discount_amount(self):
        """Only a code is accepted -- any posted discount is ignored."""
        create_coupon(code="HONEST", value=Decimal("10"))
        self.client.post(
            reverse("coupons:apply"),
            {"code": "HONEST", "discount": "1999.00", "coupon_discount": "1999.00"},
            follow=True,
        )
        self.cart.refresh_from_db()
        self.assertEqual(self.cart.coupon_discount, Decimal("200.00"))
