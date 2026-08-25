"""Order placement, stock movement, status transitions, returns and refunds."""
from decimal import Decimal

from django.conf import settings
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.cart.models import Cart, CartItem
from apps.core.tests.factories import (
    create_address,
    create_coupon,
    create_product,
    create_staff,
    create_user,
    variant_of,
)
from apps.coupons.models import CouponUsage
from apps.inventory.models import StockMovement
from apps.orders import services
from apps.orders.models import Order, OrderItem, ReturnRequest


class OrderPlacementTests(TestCase):
    def setUp(self):
        self.user = create_user()
        self.address = create_address(self.user)
        self.product = create_product(price="1000.00", compare_at_price=Decimal("1200.00"), stock=10)
        self.variant = variant_of(self.product)
        self.cart = Cart.objects.create(user=self.user)
        CartItem.objects.create(cart=self.cart, variant=self.variant, quantity=2)

    def test_place_order_creates_order_with_server_computed_totals(self):
        order = services.place_order(
            self.user, self.cart, self.address, Order.PaymentMethod.UPI
        )

        self.assertEqual(order.subtotal, Decimal("2000.00"))
        self.assertEqual(order.product_discount, Decimal("400.00"))
        self.assertEqual(order.delivery_charge, Decimal("0.00"))  # over the threshold
        self.assertEqual(order.total_amount, Decimal("2000.00"))
        self.assertEqual(order.status, Order.Status.PENDING)
        self.assertEqual(order.items.count(), 1)

    def test_order_number_is_unique_and_formatted(self):
        order = services.place_order(
            self.user, self.cart, self.address, Order.PaymentMethod.COD
        )
        self.assertTrue(order.order_number.startswith("LS-"))
        self.assertEqual(len(order.order_number.split("-")), 3)

    def test_order_snapshots_product_and_address(self):
        order = services.place_order(
            self.user, self.cart, self.address, Order.PaymentMethod.COD
        )
        item = order.items.first()

        original_name = self.product.name
        self.product.name = "Renamed After Purchase"
        self.product.price = Decimal("99.00")
        self.product.save(update_fields=["name", "price"])

        item.refresh_from_db()
        self.assertEqual(item.product_name, original_name)
        self.assertEqual(item.unit_price, Decimal("1000.00"))
        self.assertEqual(order.shipping_city, self.address.city)

    def test_placing_an_order_reserves_stock(self):
        inventory = self.variant.inventory
        self.assertEqual(inventory.quantity_reserved, 0)

        services.place_order(self.user, self.cart, self.address, Order.PaymentMethod.UPI)

        inventory.refresh_from_db()
        self.assertEqual(inventory.quantity_reserved, 2)
        self.assertEqual(inventory.quantity_available, 10)  # not sold yet
        self.assertEqual(inventory.sellable_quantity, 8)

    def test_cart_is_emptied_after_placement(self):
        services.place_order(self.user, self.cart, self.address, Order.PaymentMethod.COD)
        self.cart.refresh_from_db()
        self.assertTrue(self.cart.is_empty)

    def test_cannot_place_an_order_from_an_empty_cart(self):
        self.cart.clear()
        with self.assertRaises(services.OrderError):
            services.place_order(self.user, self.cart, self.address, Order.PaymentMethod.COD)

    def test_cannot_place_an_order_beyond_available_stock(self):
        inventory = self.variant.inventory
        inventory.quantity_available = 1
        inventory.save(update_fields=["quantity_available"])

        with self.assertRaises(services.OrderError):
            services.place_order(self.user, self.cart, self.address, Order.PaymentMethod.COD)

        self.assertEqual(Order.objects.count(), 0)  # transaction rolled back

    def test_cod_blocked_for_prepaid_only_products(self):
        self.product.is_cod_available = False
        self.product.save(update_fields=["is_cod_available"])

        with self.assertRaises(services.OrderError):
            services.place_order(self.user, self.cart, self.address, Order.PaymentMethod.COD)

    def test_invalid_payment_method_rejected(self):
        with self.assertRaises(services.OrderError):
            services.place_order(self.user, self.cart, self.address, "bitcoin")

    def test_delivery_charge_applied_below_threshold(self):
        self.cart.clear()
        cheap = create_product(price="100.00", compare_at_price=None, stock=5)
        CartItem.objects.create(cart=self.cart, variant=variant_of(cheap), quantity=1)

        order = services.place_order(
            self.user, self.cart, self.address, Order.PaymentMethod.COD
        )
        self.assertEqual(order.delivery_charge, Decimal(settings.DELIVERY_CHARGE))
        self.assertEqual(
            order.total_amount, Decimal("100.00") + Decimal(settings.DELIVERY_CHARGE)
        )


class OrderCouponTests(TestCase):
    def setUp(self):
        self.user = create_user()
        self.address = create_address(self.user)
        self.product = create_product(price="1000.00", compare_at_price=None, stock=10)
        self.cart = Cart.objects.create(user=self.user)
        CartItem.objects.create(cart=self.cart, variant=variant_of(self.product), quantity=1)

    def test_coupon_is_applied_and_recorded(self):
        coupon = create_coupon(code="ORDER10", value=Decimal("10"))
        self.cart.coupon = coupon
        self.cart.save(update_fields=["coupon"])

        order = services.place_order(
            self.user, self.cart, self.address, Order.PaymentMethod.COD
        )

        self.assertEqual(order.coupon_discount, Decimal("100.00"))
        self.assertEqual(order.coupon_code, "ORDER10")
        # The coupon drops the subtotal to 900, which is back under the
        # free-delivery threshold, so delivery is charged again.
        self.assertEqual(order.delivery_charge, Decimal(settings.DELIVERY_CHARGE))
        self.assertEqual(
            order.total_amount, Decimal("900.00") + Decimal(settings.DELIVERY_CHARGE)
        )

        coupon.refresh_from_db()
        self.assertEqual(coupon.used_count, 1)
        self.assertTrue(CouponUsage.objects.filter(coupon=coupon, order=order).exists())

    def test_coupon_that_lapsed_between_cart_and_checkout_is_refused(self):
        coupon = create_coupon(code="LAPSED", value=Decimal("10"))
        self.cart.coupon = coupon
        self.cart.save(update_fields=["coupon"])

        coupon.is_active = False
        coupon.save(update_fields=["is_active"])

        with self.assertRaises(services.OrderError):
            services.place_order(self.user, self.cart, self.address, Order.PaymentMethod.COD)

    def test_per_user_usage_limit_is_enforced_at_placement(self):
        coupon = create_coupon(code="ONCE", value=Decimal("10"), usage_limit_per_user=1)
        self.cart.coupon = coupon
        self.cart.save(update_fields=["coupon"])
        services.place_order(self.user, self.cart, self.address, Order.PaymentMethod.COD)

        # Second attempt with the same coupon.
        CartItem.objects.create(cart=self.cart, variant=variant_of(self.product), quantity=1)
        self.cart.coupon = coupon
        self.cart.save(update_fields=["coupon"])

        with self.assertRaises(services.OrderError):
            services.place_order(self.user, self.cart, self.address, Order.PaymentMethod.COD)


class StockLifecycleTests(TestCase):
    def setUp(self):
        self.user = create_user()
        self.address = create_address(self.user)
        self.product = create_product(price="1000.00", stock=10)
        self.variant = variant_of(self.product)
        self.cart = Cart.objects.create(user=self.user)
        CartItem.objects.create(cart=self.cart, variant=self.variant, quantity=3)
        self.order = services.place_order(
            self.user, self.cart, self.address, Order.PaymentMethod.UPI
        )

    def _inventory(self):
        self.variant.inventory.refresh_from_db()
        return self.variant.inventory

    def test_payment_commits_reserved_stock(self):
        services.mark_paid(self.order)

        inventory = self._inventory()
        self.assertEqual(inventory.quantity_reserved, 0)
        self.assertEqual(inventory.quantity_available, 7)
        self.assertEqual(inventory.quantity_sold, 3)

        self.product.refresh_from_db()
        self.assertEqual(self.product.sold_count, 3)

    def test_payment_is_idempotent(self):
        services.mark_paid(self.order)
        services.mark_paid(self.order)  # a webhook retry

        inventory = self._inventory()
        self.assertEqual(inventory.quantity_available, 7)
        self.assertEqual(inventory.quantity_sold, 3)

    def test_failed_payment_releases_the_reservation(self):
        services.mark_payment_failed(self.order, reason="Card declined")

        inventory = self._inventory()
        self.assertEqual(inventory.quantity_reserved, 0)
        self.assertEqual(inventory.quantity_available, 10)

        self.order.refresh_from_db()
        self.assertEqual(self.order.payment_status, Order.PaymentStatus.FAILED)

    def test_cancelling_before_payment_releases_stock(self):
        services.cancel_order(self.order, user=self.user, reason="Changed my mind")

        inventory = self._inventory()
        self.assertEqual(inventory.quantity_reserved, 0)
        self.assertEqual(inventory.quantity_available, 10)
        self.assertEqual(inventory.quantity_sold, 0)

    def test_cancelling_after_payment_restores_stock(self):
        services.mark_paid(self.order)
        services.cancel_order(self.order, user=self.user, reason="Changed my mind")

        inventory = self._inventory()
        self.assertEqual(inventory.quantity_available, 10)
        self.assertEqual(inventory.quantity_sold, 0)

        self.product.refresh_from_db()
        self.assertEqual(self.product.sold_count, 0)

        self.order.refresh_from_db()
        self.assertEqual(self.order.payment_status, Order.PaymentStatus.REFUND_PENDING)

    def test_stock_movements_are_logged(self):
        services.mark_paid(self.order)
        reasons = set(
            StockMovement.objects.filter(variant=self.variant).values_list(
                "reason", flat=True
            )
        )
        self.assertIn(StockMovement.Reason.RESERVATION, reasons)
        self.assertIn(StockMovement.Reason.SALE, reasons)

    def test_concurrent_orders_cannot_oversell(self):
        """Two carts wanting the last units -- only what exists can be sold."""
        self.variant.inventory.refresh_from_db()
        remaining = self.variant.inventory.sellable_quantity  # 7 after the 3 reserved

        other = create_user()
        other_address = create_address(other)
        other_cart = Cart.objects.create(user=other)
        CartItem.objects.create(
            cart=other_cart, variant=self.variant, quantity=remaining + 1
        )

        with self.assertRaises(services.OrderError):
            services.place_order(
                other, other_cart, other_address, Order.PaymentMethod.COD
            )


class StatusTransitionTests(TestCase):
    def setUp(self):
        self.staff = create_staff(superuser=True)
        self.user = create_user()
        self.address = create_address(self.user)
        product = create_product(price="1000.00", stock=10)
        self.cart = Cart.objects.create(user=self.user)
        CartItem.objects.create(cart=self.cart, variant=variant_of(product), quantity=1)
        self.order = services.place_order(
            self.user, self.cart, self.address, Order.PaymentMethod.COD
        )

    def test_happy_path_transitions(self):
        flow = [
            Order.Status.CONFIRMED,
            Order.Status.PROCESSING,
            Order.Status.SHIPPED,
            Order.Status.DELIVERED,
        ]
        for status in flow:
            services.transition_order(self.order, status, user=self.staff)
            self.order.refresh_from_db()
            self.assertEqual(self.order.status, status)

        self.assertIsNotNone(self.order.shipped_at)
        self.assertIsNotNone(self.order.delivered_at)

    def test_illegal_transition_is_refused(self):
        with self.assertRaises(services.OrderError):
            services.transition_order(self.order, Order.Status.DELIVERED, user=self.staff)

    def test_cannot_move_backwards(self):
        services.transition_order(self.order, Order.Status.CONFIRMED, user=self.staff)
        services.transition_order(self.order, Order.Status.PROCESSING, user=self.staff)
        with self.assertRaises(services.OrderError):
            services.transition_order(self.order, Order.Status.CONFIRMED, user=self.staff)

    def test_delivery_marks_cod_order_paid(self):
        for status in (
            Order.Status.CONFIRMED,
            Order.Status.PROCESSING,
            Order.Status.SHIPPED,
            Order.Status.DELIVERED,
        ):
            services.transition_order(self.order, status, user=self.staff)

        self.order.refresh_from_db()
        self.assertEqual(self.order.payment_status, Order.PaymentStatus.PAID)

    def test_every_transition_is_recorded_in_history(self):
        services.transition_order(
            self.order, Order.Status.CONFIRMED, user=self.staff, note="Verified"
        )
        history = self.order.status_history.all()
        self.assertEqual(history.count(), 2)  # creation + this transition
        self.assertEqual(history.last().changed_by, self.staff)


class CancellationPolicyTests(TestCase):
    def setUp(self):
        self.user = create_user()
        self.address = create_address(self.user)
        product = create_product(price="1000.00", stock=10)
        self.cart = Cart.objects.create(user=self.user)
        CartItem.objects.create(cart=self.cart, variant=variant_of(product), quantity=1)
        self.order = services.place_order(
            self.user, self.cart, self.address, Order.PaymentMethod.COD
        )

    def test_fresh_order_can_be_cancelled(self):
        self.assertTrue(self.order.can_be_cancelled)

    def test_shipped_order_cannot_be_cancelled(self):
        staff = create_staff(superuser=True)
        for status in (
            Order.Status.CONFIRMED,
            Order.Status.PROCESSING,
            Order.Status.SHIPPED,
        ):
            services.transition_order(self.order, status, user=staff)
        self.order.refresh_from_db()
        self.assertFalse(self.order.can_be_cancelled)

        with self.assertRaises(services.OrderError):
            services.cancel_order(self.order, user=self.user)

    def test_order_outside_the_cancellation_window_cannot_be_cancelled(self):
        self.order.placed_at = timezone.now() - timezone.timedelta(
            hours=settings.ORDER_CANCEL_WINDOW_HOURS + 1
        )
        self.order.save(update_fields=["placed_at"])
        self.order.refresh_from_db()
        self.assertFalse(self.order.can_be_cancelled)

    def test_staff_override_can_cancel_a_shipped_order(self):
        staff = create_staff(superuser=True)
        for status in (
            Order.Status.CONFIRMED,
            Order.Status.PROCESSING,
            Order.Status.SHIPPED,
        ):
            services.transition_order(self.order, status, user=staff)

        services.cancel_order(self.order, user=staff, staff_override=True)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.Status.CANCELLED)

    def test_cancellation_returns_the_coupon_use(self):
        coupon = create_coupon(code="GIVEBACK", value=Decimal("10"))
        cart = Cart.objects.create(user=create_user())
        owner = cart.user
        create_address(owner)
        product = create_product(price="1000.00", stock=5)
        CartItem.objects.create(cart=cart, variant=variant_of(product), quantity=1)
        cart.coupon = coupon
        cart.save(update_fields=["coupon"])

        order = services.place_order(
            owner, cart, owner.default_address(), Order.PaymentMethod.COD
        )
        coupon.refresh_from_db()
        self.assertEqual(coupon.used_count, 1)

        services.cancel_order(order, user=owner, reason="Changed my mind")
        coupon.refresh_from_db()
        self.assertEqual(coupon.used_count, 0)
        self.assertFalse(CouponUsage.objects.filter(order=order).exists())

    def test_customer_cannot_cancel_someone_elses_order(self):
        intruder = create_user()
        self.client.force_login(intruder)
        response = self.client.post(
            reverse("orders:cancel", args=[self.order.order_number]),
            {"reason": "changed_mind"},
        )
        self.assertEqual(response.status_code, 404)
        self.order.refresh_from_db()
        self.assertNotEqual(self.order.status, Order.Status.CANCELLED)


class ReturnTests(TestCase):
    def setUp(self):
        self.staff = create_staff(superuser=True)
        self.user = create_user()
        self.address = create_address(self.user)
        self.product = create_product(price="1000.00", stock=10)
        self.variant = variant_of(self.product)
        cart = Cart.objects.create(user=self.user)
        CartItem.objects.create(cart=cart, variant=self.variant, quantity=2)
        self.order = services.place_order(
            self.user, cart, self.address, Order.PaymentMethod.COD
        )
        for status in (
            Order.Status.CONFIRMED,
            Order.Status.PROCESSING,
            Order.Status.SHIPPED,
            Order.Status.DELIVERED,
        ):
            services.transition_order(self.order, status, user=self.staff)
        self.order.refresh_from_db()
        self.item = self.order.items.first()

    def test_delivered_order_can_be_returned(self):
        self.assertTrue(self.order.can_be_returned)

    def test_return_request_is_created(self):
        request = services.request_return(
            self.item, 1, ReturnRequest.Reason.DAMAGED, "Arrived dented", user=self.user
        )
        self.assertEqual(request.status, ReturnRequest.Status.REQUESTED)
        self.assertEqual(request.refund_amount, Decimal("1000.00"))

        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.Status.RETURN_REQUESTED)

    def test_cannot_return_more_than_purchased(self):
        with self.assertRaises(services.OrderError):
            services.request_return(self.item, 5, ReturnRequest.Reason.OTHER, user=self.user)

    def test_cannot_return_twice_beyond_the_quantity(self):
        services.request_return(self.item, 2, ReturnRequest.Reason.OTHER, user=self.user)
        with self.assertRaises(services.OrderError):
            services.request_return(self.item, 1, ReturnRequest.Reason.OTHER, user=self.user)

    def test_cannot_return_an_undelivered_order(self):
        other_user = create_user()
        address = create_address(other_user)
        cart = Cart.objects.create(user=other_user)
        CartItem.objects.create(cart=cart, variant=self.variant, quantity=1)
        pending = services.place_order(
            other_user, cart, address, Order.PaymentMethod.COD
        )

        with self.assertRaises(services.OrderError):
            services.request_return(
                pending.items.first(), 1, ReturnRequest.Reason.OTHER, user=other_user
            )

    def test_return_outside_the_window_is_refused(self):
        self.order.delivered_at = timezone.now() - timezone.timedelta(
            days=settings.RETURN_WINDOW_DAYS + 1
        )
        self.order.save(update_fields=["delivered_at"])
        self.order.refresh_from_db()
        self.assertFalse(self.order.can_be_returned)

        with self.assertRaises(services.OrderError):
            services.request_return(self.item, 1, ReturnRequest.Reason.OTHER, user=self.user)

    def test_completing_a_return_restores_stock(self):
        request = services.request_return(
            self.item, 2, ReturnRequest.Reason.DAMAGED, user=self.user
        )
        inventory = self.variant.inventory
        inventory.refresh_from_db()
        sold_before = inventory.quantity_sold
        available_before = inventory.quantity_available

        services.process_return(
            request, ReturnRequest.Status.COMPLETED, user=self.staff
        )

        inventory.refresh_from_db()
        self.assertEqual(inventory.quantity_available, available_before + 2)
        self.assertEqual(inventory.quantity_sold, sold_before - 2)

        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.Status.RETURNED)

    def test_rejecting_a_return_reverts_the_order(self):
        request = services.request_return(
            self.item, 1, ReturnRequest.Reason.OTHER, user=self.user
        )
        services.process_return(request, ReturnRequest.Status.REJECTED, user=self.staff)

        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.Status.DELIVERED)
        self.item.refresh_from_db()
        self.assertEqual(self.item.status, OrderItem.ItemStatus.ACTIVE)

    def test_refund_is_recorded(self):
        services.record_refund(self.order, Decimal("2000.00"), user=self.staff)
        self.order.refresh_from_db()
        self.assertEqual(self.order.refunded_amount, Decimal("2000.00"))
        self.assertEqual(self.order.payment_status, Order.PaymentStatus.REFUNDED)


class CheckoutFlowTests(TestCase):
    """The full HTTP checkout journey."""

    def setUp(self):
        self.user = create_user()
        self.address = create_address(self.user)
        self.product = create_product(price="1500.00", stock=5)
        self.client.force_login(self.user)
        self.client.post(
            reverse("cart:add"),
            {"variant_id": variant_of(self.product).pk, "quantity": 1},
        )

    def test_checkout_requires_a_non_empty_cart(self):
        self.client.post(reverse("cart:clear"))
        response = self.client.get(reverse("orders:checkout_address"))
        self.assertRedirects(response, reverse("cart:detail"))

    def test_address_step_renders(self):
        response = self.client.get(reverse("orders:checkout_address"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.address.full_name)

    def test_payment_step_requires_an_address_choice_first(self):
        response = self.client.get(reverse("orders:checkout_payment"))
        self.assertRedirects(response, reverse("orders:checkout_address"))

    def test_full_cod_checkout(self):
        self.client.post(
            reverse("orders:checkout_address"), {"address": self.address.pk}
        )
        response = self.client.post(
            reverse("orders:checkout_payment"),
            {"payment_method": Order.PaymentMethod.COD, "terms_accepted": "on"},
        )

        order = Order.objects.get(user=self.user)
        self.assertRedirects(
            response, reverse("orders:confirmation", args=[order.order_number])
        )
        self.assertEqual(order.status, Order.Status.CONFIRMED)
        self.assertEqual(order.total_amount, Decimal("1500.00"))

    def test_online_payment_routes_to_the_gateway(self):
        self.client.post(
            reverse("orders:checkout_address"), {"address": self.address.pk}
        )
        response = self.client.post(
            reverse("orders:checkout_payment"),
            {"payment_method": Order.PaymentMethod.UPI, "terms_accepted": "on"},
        )
        order = Order.objects.get(user=self.user)
        self.assertRedirects(
            response,
            reverse("payments:start", args=[order.order_number]),
            fetch_redirect_response=False,
        )

    def test_terms_must_be_accepted(self):
        self.client.post(
            reverse("orders:checkout_address"), {"address": self.address.pk}
        )
        response = self.client.post(
            reverse("orders:checkout_payment"),
            {"payment_method": Order.PaymentMethod.COD},
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Order.objects.filter(user=self.user).exists())

    def test_cannot_check_out_with_another_users_address(self):
        stranger_address = create_address(create_user())
        response = self.client.post(
            reverse("orders:checkout_address"), {"address": stranger_address.pk}
        )
        self.assertEqual(response.status_code, 200)  # form redisplayed
        self.assertNotIn("checkout_address_id", self.client.session)

    def test_posted_totals_are_ignored(self):
        """A tampered client cannot dictate what an order costs."""
        self.client.post(
            reverse("orders:checkout_address"), {"address": self.address.pk}
        )
        self.client.post(
            reverse("orders:checkout_payment"),
            {
                "payment_method": Order.PaymentMethod.COD,
                "terms_accepted": "on",
                "total_amount": "1.00",
                "subtotal": "1.00",
                "coupon_discount": "1499.00",
                "delivery_charge": "0.00",
            },
        )
        order = Order.objects.get(user=self.user)
        self.assertEqual(order.total_amount, Decimal("1500.00"))
        self.assertEqual(order.coupon_discount, Decimal("0.00"))
