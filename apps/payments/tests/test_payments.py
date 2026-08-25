"""Payment flow, signature verification and webhook handling."""
import hashlib
import hmac
import json
from decimal import Decimal

from django.conf import settings
from django.test import TestCase, override_settings
from django.urls import reverse

from apps.cart.models import Cart, CartItem
from apps.core.tests.factories import create_address, create_product, create_user, variant_of
from apps.orders import services as order_services
from apps.orders.models import Order
from apps.payments.gateways import MockGateway, PaymentError, get_gateway
from apps.payments.models import Payment, WebhookEvent


class PaymentFlowTests(TestCase):
    def setUp(self):
        self.user = create_user()
        self.address = create_address(self.user)
        self.product = create_product(price="1500.00", stock=10)
        self.variant = variant_of(self.product)

        cart = Cart.objects.create(user=self.user)
        CartItem.objects.create(cart=cart, variant=self.variant, quantity=1)
        self.order = order_services.place_order(
            self.user, cart, self.address, Order.PaymentMethod.UPI
        )
        self.client.force_login(self.user)

    def _start(self):
        return self.client.get(reverse("payments:start", args=[self.order.order_number]))

    def test_start_creates_a_payment_record(self):
        response = self._start()
        self.assertEqual(response.status_code, 200)

        payment = Payment.objects.get(order=self.order)
        self.assertEqual(payment.status, Payment.Status.CREATED)
        self.assertEqual(payment.amount, self.order.total_amount)

    def test_gateway_secret_is_never_sent_to_the_browser(self):
        response = self._start()
        body = response.content.decode()
        self.assertNotIn(settings.SECRET_KEY, body)
        if settings.PAYMENT_SECRET:
            self.assertNotIn(settings.PAYMENT_SECRET, body)

    def test_valid_signature_marks_the_order_paid(self):
        self._start()
        payment = Payment.objects.get(order=self.order)
        signature = MockGateway()._signature(payment.gateway_order_id)

        response = self.client.post(
            reverse("payments:verify", args=[self.order.order_number]),
            {"signature": signature, "outcome": "success", "method": "upi"},
        )
        self.assertRedirects(
            response, reverse("orders:confirmation", args=[self.order.order_number])
        )

        self.order.refresh_from_db()
        self.assertEqual(self.order.payment_status, Order.PaymentStatus.PAID)
        self.assertEqual(self.order.status, Order.Status.CONFIRMED)

        payment.refresh_from_db()
        self.assertEqual(payment.status, Payment.Status.CAPTURED)
        self.assertIsNotNone(payment.paid_at)

    def test_forged_signature_is_rejected(self):
        """A POST claiming success without a valid signature must not pay."""
        self._start()
        response = self.client.post(
            reverse("payments:verify", args=[self.order.order_number]),
            {"signature": "totally-made-up", "outcome": "success"},
        )
        self.assertRedirects(
            response, reverse("payments:failed", args=[self.order.order_number])
        )

        self.order.refresh_from_db()
        self.assertNotEqual(self.order.payment_status, Order.PaymentStatus.PAID)
        self.assertEqual(self.order.status, Order.Status.PENDING)

    def test_missing_signature_is_rejected(self):
        self._start()
        self.client.post(
            reverse("payments:verify", args=[self.order.order_number]), {"outcome": "success"}
        )
        self.order.refresh_from_db()
        self.assertNotEqual(self.order.payment_status, Order.PaymentStatus.PAID)

    def test_failed_payment_releases_stock(self):
        self._start()
        payment = Payment.objects.get(order=self.order)
        signature = MockGateway()._signature(payment.gateway_order_id)

        self.client.post(
            reverse("payments:verify", args=[self.order.order_number]),
            {"signature": signature, "outcome": "fail"},
        )

        self.variant.inventory.refresh_from_db()
        self.assertEqual(self.variant.inventory.quantity_reserved, 0)
        self.assertEqual(self.variant.inventory.quantity_available, 10)

    def test_paying_twice_does_not_double_count(self):
        self._start()
        payment = Payment.objects.get(order=self.order)
        signature = MockGateway()._signature(payment.gateway_order_id)
        url = reverse("payments:verify", args=[self.order.order_number])

        self.client.post(url, {"signature": signature, "outcome": "success"})
        self.client.post(url, {"signature": signature, "outcome": "success"})

        self.variant.inventory.refresh_from_db()
        self.assertEqual(self.variant.inventory.quantity_sold, 1)
        self.assertEqual(self.variant.inventory.quantity_available, 9)

    def test_cannot_pay_for_someone_elses_order(self):
        intruder = create_user()
        self.client.force_login(intruder)
        response = self.client.get(
            reverse("payments:start", args=[self.order.order_number])
        )
        self.assertEqual(response.status_code, 404)

    def test_already_paid_order_redirects(self):
        order_services.mark_paid(self.order)
        response = self._start()
        self.assertRedirects(
            response, reverse("orders:detail", args=[self.order.order_number])
        )

    def test_cancelling_payment_releases_the_reservation(self):
        self._start()
        self.client.post(reverse("payments:cancel", args=[self.order.order_number]))

        self.variant.inventory.refresh_from_db()
        self.assertEqual(self.variant.inventory.quantity_reserved, 0)

    def test_failed_page_renders_with_retry(self):
        self._start()
        response = self.client.get(
            reverse("payments:failed", args=[self.order.order_number])
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Try payment again")


class WebhookTests(TestCase):
    def setUp(self):
        self.user = create_user()
        self.address = create_address(self.user)
        product = create_product(price="800.00", stock=5)
        cart = Cart.objects.create(user=self.user)
        CartItem.objects.create(cart=cart, variant=variant_of(product), quantity=1)
        self.order = order_services.place_order(
            self.user, cart, self.address, Order.PaymentMethod.CARD
        )
        self.gateway = MockGateway()
        self.payment, _context = self.gateway.create_order(self.order)
        self.url = reverse("payments:webhook", args=["mock"])

    def _signed_body(self, payload):
        body = json.dumps(payload)
        signature = hmac.new(
            settings.SECRET_KEY.encode(),
            payload.get("gateway_order_id", "").encode(),
            hashlib.sha256,
        ).hexdigest()
        return body, signature

    def test_verified_webhook_marks_the_order_paid(self):
        payload = {
            "id": "evt_test_1",
            "event": "payment.captured",
            "gateway_order_id": self.payment.gateway_order_id,
        }
        body, signature = self._signed_body(payload)

        response = self.client.post(
            self.url, data=body, content_type="application/json", HTTP_X_SIGNATURE=signature
        )
        self.assertEqual(response.status_code, 200)

        self.order.refresh_from_db()
        self.assertEqual(self.order.payment_status, Order.PaymentStatus.PAID)
        self.assertTrue(WebhookEvent.objects.get(event_id="evt_test_1").processed)

    def test_unsigned_webhook_is_rejected(self):
        payload = {
            "id": "evt_test_2",
            "event": "payment.captured",
            "gateway_order_id": self.payment.gateway_order_id,
        }
        response = self.client.post(
            self.url, data=json.dumps(payload), content_type="application/json"
        )
        self.assertEqual(response.status_code, 400)

        self.order.refresh_from_db()
        self.assertNotEqual(self.order.payment_status, Order.PaymentStatus.PAID)
        self.assertFalse(WebhookEvent.objects.filter(event_id="evt_test_2").exists())

    def test_tampered_payload_is_rejected(self):
        payload = {
            "id": "evt_test_3",
            "event": "payment.captured",
            "gateway_order_id": self.payment.gateway_order_id,
        }
        _body, signature = self._signed_body(payload)
        payload["gateway_order_id"] = "someone-elses-order"

        response = self.client.post(
            self.url,
            data=json.dumps(payload),
            content_type="application/json",
            HTTP_X_SIGNATURE=signature,
        )
        self.assertEqual(response.status_code, 400)

    def test_replayed_webhook_is_not_applied_twice(self):
        payload = {
            "id": "evt_test_4",
            "event": "payment.captured",
            "gateway_order_id": self.payment.gateway_order_id,
        }
        body, signature = self._signed_body(payload)

        self.client.post(
            self.url, data=body, content_type="application/json", HTTP_X_SIGNATURE=signature
        )
        second = self.client.post(
            self.url, data=body, content_type="application/json", HTTP_X_SIGNATURE=signature
        )

        self.assertEqual(second.status_code, 200)
        self.assertEqual(second.json()["status"], "already processed")
        self.assertEqual(WebhookEvent.objects.filter(event_id="evt_test_4").count(), 1)

    def test_unknown_gateway_returns_404(self):
        response = self.client.post(
            reverse("payments:webhook", args=["not-a-gateway"]),
            data="{}",
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 404)

    def test_webhook_does_not_require_csrf(self):
        """The caller is the gateway, not a browser -- authenticity is the signature."""
        payload = {
            "id": "evt_test_5",
            "event": "payment.captured",
            "gateway_order_id": self.payment.gateway_order_id,
        }
        body, signature = self._signed_body(payload)

        csrf_client = self.client_class(enforce_csrf_checks=True)
        response = csrf_client.post(
            self.url, data=body, content_type="application/json", HTTP_X_SIGNATURE=signature
        )
        self.assertEqual(response.status_code, 200)


class GatewaySelectionTests(TestCase):
    def test_mock_gateway_selected_by_configuration(self):
        self.assertIsInstance(get_gateway(), MockGateway)

    @override_settings(PAYMENT_GATEWAY="razorpay", PAYMENT_KEY="", PAYMENT_SECRET="", DEBUG=False)
    def test_missing_credentials_fail_loudly_when_not_debugging(self):
        """A misconfigured production deploy must not silently accept fake payments."""
        with self.assertRaises(PaymentError):
            get_gateway()

    @override_settings(PAYMENT_GATEWAY="razorpay", PAYMENT_KEY="", PAYMENT_SECRET="", DEBUG=True)
    def test_missing_credentials_fall_back_to_mock_in_debug(self):
        self.assertIsInstance(get_gateway(), MockGateway)

    def test_unknown_gateway_name_raises(self):
        with self.assertRaises(PaymentError):
            get_gateway("paypal")

    def test_minor_unit_conversion(self):
        self.assertEqual(MockGateway.to_minor_units(Decimal("1499.99")), 149999)
        self.assertEqual(MockGateway.to_minor_units(Decimal("10.00")), 1000)
