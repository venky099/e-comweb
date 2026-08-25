"""Payment gateway abstraction.

Three backends share one interface so the order flow never branches on which
provider is configured:

* ``RazorpayGateway`` -- Razorpay Orders API + HMAC signature verification
* ``StripeGateway``   -- Stripe PaymentIntents + webhook signature verification
* ``MockGateway``     -- development stand-in, enabled with PAYMENT_GATEWAY=mock

Secrets are read from settings (which read the environment). Nothing here is
ever rendered into a template or exposed to JavaScript except the *public*
key id, which is designed to be public.

Security note: a payment is only ever marked successful after the signature
returned by the browser is verified server-side against our secret. A POST
claiming "payment succeeded" without a valid signature is rejected.
"""
import hashlib
import hmac
import json
import logging
from decimal import Decimal

from django.conf import settings
from django.utils import timezone
from django.utils.crypto import constant_time_compare, get_random_string

from .models import Payment

logger = logging.getLogger("ecommerce")


class PaymentError(Exception):
    """Raised when a gateway call or verification fails."""


class BaseGateway:
    name = "base"
    #: Whether the storefront should render a gateway checkout widget.
    is_interactive = True

    def __init__(self):
        self.key = settings.PAYMENT_KEY
        self.secret = settings.PAYMENT_SECRET
        self.currency = settings.PAYMENT_CURRENCY

    # ---- helpers -------------------------------------------------------
    @staticmethod
    def to_minor_units(amount):
        """Gateways bill in paise/cents, not rupees/dollars."""
        return int((Decimal(amount) * 100).quantize(Decimal("1")))

    def create_payment_record(self, order, gateway_order_id="", raw=None):
        return Payment.objects.create(
            order=order,
            gateway=self.name,
            amount=order.total_amount,
            currency=order.currency,
            status=Payment.Status.CREATED,
            gateway_order_id=gateway_order_id,
            raw_response=raw or {},
        )

    # ---- interface -----------------------------------------------------
    def create_order(self, order):
        """Create the gateway-side order/intent. Returns (payment, context)."""
        raise NotImplementedError

    def verify(self, payment, data):
        """Verify a client callback. Returns True when genuinely paid."""
        raise NotImplementedError

    def verify_webhook(self, body, signature):
        """Verify a server-to-server webhook signature."""
        raise NotImplementedError

    def refund(self, payment, amount, reason=""):
        raise NotImplementedError


class RazorpayGateway(BaseGateway):
    name = Payment.Gateway.RAZORPAY

    def _client(self):
        import razorpay

        if not self.key or not self.secret:
            raise PaymentError("Razorpay credentials are not configured.")
        return razorpay.Client(auth=(self.key, self.secret))

    def create_order(self, order):
        client = self._client()
        try:
            gateway_order = client.order.create(
                {
                    "amount": self.to_minor_units(order.total_amount),
                    "currency": order.currency,
                    "receipt": order.order_number,
                    "notes": {"order_number": order.order_number},
                }
            )
        except Exception as exc:
            logger.exception("Razorpay order creation failed for %s", order.order_number)
            raise PaymentError(str(exc)) from exc

        payment = self.create_payment_record(
            order, gateway_order_id=gateway_order["id"], raw=gateway_order
        )
        context = {
            "gateway": self.name,
            # Public key id only -- the secret never leaves the server.
            "key_id": self.key,
            "amount": gateway_order["amount"],
            "currency": gateway_order["currency"],
            "gateway_order_id": gateway_order["id"],
            "name": settings.SITE_NAME,
            "description": f"Order {order.order_number}",
            "prefill": {
                "name": order.shipping_full_name,
                "email": order.email,
                "contact": order.shipping_phone,
            },
        }
        return payment, context

    def verify(self, payment, data):
        """HMAC-SHA256 over ``order_id|payment_id`` keyed with our secret."""
        order_id = data.get("razorpay_order_id", "")
        payment_id = data.get("razorpay_payment_id", "")
        signature = data.get("razorpay_signature", "")

        if not (order_id and payment_id and signature):
            raise PaymentError("Incomplete payment response.")
        if order_id != payment.gateway_order_id:
            raise PaymentError("Payment does not belong to this order.")

        expected = hmac.new(
            self.secret.encode(), f"{order_id}|{payment_id}".encode(), hashlib.sha256
        ).hexdigest()

        if not constant_time_compare(expected, signature):
            logger.warning("Razorpay signature mismatch on payment %s", payment.pk)
            raise PaymentError("Payment signature verification failed.")

        payment.gateway_payment_id = payment_id
        payment.gateway_signature = signature
        payment.status = Payment.Status.CAPTURED
        payment.paid_at = timezone.now()
        payment.raw_response = {**payment.raw_response, "callback": data}
        payment.save(
            update_fields=[
                "gateway_payment_id",
                "gateway_signature",
                "status",
                "paid_at",
                "raw_response",
                "updated_at",
            ]
        )
        return True

    def verify_webhook(self, body, signature):
        secret = settings.PAYMENT_WEBHOOK_SECRET
        if not secret:
            return False
        expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        return constant_time_compare(expected, signature or "")

    def refund(self, payment, amount, reason=""):
        client = self._client()
        try:
            result = client.payment.refund(
                payment.gateway_payment_id,
                {"amount": self.to_minor_units(amount), "notes": {"reason": reason}},
            )
        except Exception as exc:
            logger.exception("Razorpay refund failed for payment %s", payment.pk)
            raise PaymentError(str(exc)) from exc
        return result.get("id", ""), result


class StripeGateway(BaseGateway):
    name = Payment.Gateway.STRIPE

    def _stripe(self):
        import stripe

        if not self.secret:
            raise PaymentError("Stripe credentials are not configured.")
        stripe.api_key = self.secret
        return stripe

    def create_order(self, order):
        stripe = self._stripe()
        try:
            intent = stripe.PaymentIntent.create(
                amount=self.to_minor_units(order.total_amount),
                currency=order.currency.lower(),
                metadata={"order_number": order.order_number},
                automatic_payment_methods={"enabled": True},
            )
        except Exception as exc:
            logger.exception("Stripe intent creation failed for %s", order.order_number)
            raise PaymentError(str(exc)) from exc

        payment = self.create_payment_record(
            order, gateway_order_id=intent["id"], raw={"id": intent["id"]}
        )
        context = {
            "gateway": self.name,
            "key_id": self.key,  # publishable key
            "client_secret": intent["client_secret"],
            "amount": intent["amount"],
            "currency": intent["currency"],
            "gateway_order_id": intent["id"],
        }
        return payment, context

    def verify(self, payment, data):
        """Re-fetch the intent from Stripe rather than trusting the browser."""
        stripe = self._stripe()
        intent_id = data.get("payment_intent") or payment.gateway_order_id
        try:
            intent = stripe.PaymentIntent.retrieve(intent_id)
        except Exception as exc:
            raise PaymentError(str(exc)) from exc

        if intent["status"] != "succeeded":
            payment.status = Payment.Status.FAILED
            payment.error_message = f"Intent status: {intent['status']}"
            payment.save(update_fields=["status", "error_message", "updated_at"])
            raise PaymentError("Payment was not completed.")

        if intent["amount_received"] < self.to_minor_units(payment.amount):
            raise PaymentError("Paid amount does not match the order total.")

        payment.gateway_payment_id = intent["id"]
        payment.status = Payment.Status.CAPTURED
        payment.paid_at = timezone.now()
        payment.raw_response = {**payment.raw_response, "intent_status": intent["status"]}
        payment.save(
            update_fields=[
                "gateway_payment_id",
                "status",
                "paid_at",
                "raw_response",
                "updated_at",
            ]
        )
        return True

    def verify_webhook(self, body, signature):
        import stripe

        secret = settings.PAYMENT_WEBHOOK_SECRET
        if not secret:
            return False
        try:
            stripe.Webhook.construct_event(body, signature, secret)
            return True
        except Exception:
            logger.warning("Stripe webhook signature verification failed.")
            return False

    def refund(self, payment, amount, reason=""):
        stripe = self._stripe()
        try:
            result = stripe.Refund.create(
                payment_intent=payment.gateway_payment_id or payment.gateway_order_id,
                amount=self.to_minor_units(amount),
                metadata={"reason": reason},
            )
        except Exception as exc:
            raise PaymentError(str(exc)) from exc
        return result["id"], dict(result)


class MockGateway(BaseGateway):
    """Development gateway.

    Signs its own callback with the project SECRET_KEY, so the verification
    path exercised in development is structurally the same one used in
    production -- an unsigned or tampered callback is still rejected.

    It is only reachable when ``PAYMENT_GATEWAY=mock``; production settings
    should never leave it enabled.
    """

    name = Payment.Gateway.MOCK

    def _signature(self, reference):
        return hmac.new(
            settings.SECRET_KEY.encode(), reference.encode(), hashlib.sha256
        ).hexdigest()

    def create_order(self, order):
        reference = f"mock_{order.order_number}_{get_random_string(8)}"
        payment = self.create_payment_record(
            order, gateway_order_id=reference, raw={"mock": True}
        )
        return payment, {
            "gateway": self.name,
            "key_id": "mock_key",
            "gateway_order_id": reference,
            "amount": self.to_minor_units(order.total_amount),
            "currency": order.currency,
            "signature": self._signature(reference),
        }

    def verify(self, payment, data):
        signature = data.get("signature", "")
        if not constant_time_compare(self._signature(payment.gateway_order_id), signature):
            raise PaymentError("Payment signature verification failed.")

        if data.get("outcome") == "fail":
            payment.status = Payment.Status.FAILED
            payment.error_message = "Simulated failure"
            payment.save(update_fields=["status", "error_message", "updated_at"])
            raise PaymentError("Payment failed (simulated).")

        payment.gateway_payment_id = f"pay_{get_random_string(14)}"
        payment.status = Payment.Status.CAPTURED
        payment.method = data.get("method", "upi")
        payment.paid_at = timezone.now()
        payment.save(
            update_fields=[
                "gateway_payment_id",
                "status",
                "method",
                "paid_at",
                "updated_at",
            ]
        )
        return True

    def verify_webhook(self, body, signature):
        try:
            payload = json.loads(body or b"{}")
        except (TypeError, ValueError):
            return False
        reference = payload.get("gateway_order_id", "")
        return constant_time_compare(self._signature(reference), signature or "")

    def refund(self, payment, amount, reason=""):
        return f"rfnd_{get_random_string(12)}", {"mock": True, "amount": str(amount)}


GATEWAYS = {
    Payment.Gateway.RAZORPAY: RazorpayGateway,
    Payment.Gateway.STRIPE: StripeGateway,
    Payment.Gateway.MOCK: MockGateway,
}


def get_gateway(name=None):
    """Resolve the configured gateway.

    Falls back to the mock only when credentials are missing *and* DEBUG is
    on, so a misconfigured production deploy fails loudly instead of quietly
    accepting fake payments.
    """
    name = name or settings.PAYMENT_GATEWAY
    gateway_class = GATEWAYS.get(name)

    if gateway_class is None:
        raise PaymentError(f"Unknown payment gateway: {name}")

    if gateway_class is not MockGateway and not (settings.PAYMENT_KEY and settings.PAYMENT_SECRET):
        if settings.DEBUG:
            logger.warning(
                "PAYMENT_GATEWAY=%s but no credentials configured; using the mock gateway.",
                name,
            )
            return MockGateway()
        raise PaymentError(
            f"{name} is selected but PAYMENT_KEY/PAYMENT_SECRET are not configured."
        )

    return gateway_class()
