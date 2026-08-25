"""Payment views: start, verify, fail, and the gateway webhook."""
import json
import logging

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.translation import gettext_lazy as _
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from apps.orders import services as order_services
from apps.orders.models import Order

from .gateways import PaymentError, get_gateway
from .models import Payment, WebhookEvent

logger = logging.getLogger("ecommerce")


@login_required
def start_payment(request, order_number):
    """Create the gateway order and render the checkout widget."""
    order = get_object_or_404(
        Order, order_number=order_number, user=request.user
    )

    if order.payment_status == Order.PaymentStatus.PAID:
        messages.info(request, _("This order is already paid."))
        return redirect("orders:detail", order_number=order.order_number)

    if order.status in {Order.Status.CANCELLED, Order.Status.REFUNDED}:
        messages.error(request, _("This order is closed."))
        return redirect("orders:detail", order_number=order.order_number)

    try:
        gateway = get_gateway()
        payment, context = gateway.create_order(order)
    except PaymentError as exc:
        logger.error("Could not start payment for %s: %s", order.order_number, exc)
        messages.error(
            request, _("We could not reach the payment provider. Please try again.")
        )
        return redirect("orders:detail", order_number=order.order_number)

    return render(
        request,
        "payments/checkout.html",
        {"order": order, "payment": payment, "gateway_context": context},
    )


@login_required
@require_POST
def verify_payment(request, order_number):
    """Verify a gateway callback and, only then, mark the order paid.

    Everything the browser posts is treated as a claim. The signature check
    inside the gateway is what turns it into a fact.
    """
    order = get_object_or_404(Order, order_number=order_number, user=request.user)
    payment = (
        Payment.objects.filter(order=order)
        .exclude(status=Payment.Status.FAILED)
        .order_by("-created_at")
        .first()
    )

    if payment is None:
        messages.error(request, _("No payment in progress for this order."))
        return redirect("orders:detail", order_number=order.order_number)

    data = request.POST.dict()
    try:
        gateway = get_gateway(payment.gateway)
        gateway.verify(payment, data)
    except PaymentError as exc:
        logger.warning("Payment verification failed for %s: %s", order.order_number, exc)
        order_services.mark_payment_failed(order, reason=str(exc))
        messages.error(request, _("Payment could not be verified. Please try again."))
        return redirect("payments:failed", order_number=order.order_number)

    with transaction.atomic():
        order_services.mark_paid(order, payment)

    messages.success(
        request,
        _("Payment received. Your order %(number)s is confirmed.")
        % {"number": order.order_number},
    )
    return redirect("orders:confirmation", order_number=order.order_number)


@login_required
def payment_failed(request, order_number):
    """Landing page after a failed or abandoned payment, with a retry link."""
    order = get_object_or_404(Order, order_number=order_number, user=request.user)
    last_payment = order.payments.order_by("-created_at").first()
    return render(
        request,
        "payments/failed.html",
        {"order": order, "payment": last_payment},
    )


@login_required
@require_POST
def cancel_payment(request, order_number):
    """Customer backed out of the gateway widget -- release the reservation."""
    order = get_object_or_404(Order, order_number=order_number, user=request.user)
    order_services.mark_payment_failed(order, reason="Cancelled by customer")
    messages.info(request, _("Payment cancelled. Your order is still awaiting payment."))
    return redirect("orders:detail", order_number=order.order_number)


@csrf_exempt
@require_POST
def webhook(request, gateway_name):
    """Server-to-server gateway callback.

    CSRF-exempt because the caller is the gateway, not a browser session --
    authenticity comes from the signature header instead. Events are stored
    before processing and keyed by a unique id so a retry cannot double-apply.
    """
    body = request.body
    signature = (
        request.headers.get("X-Razorpay-Signature")
        or request.headers.get("Stripe-Signature")
        or request.headers.get("X-Signature")
        or ""
    )

    try:
        gateway = get_gateway(gateway_name)
    except PaymentError:
        return HttpResponse(status=404)

    verified = gateway.verify_webhook(body, signature)
    if not verified:
        logger.warning("Rejected unverified %s webhook", gateway_name)
        return HttpResponse("invalid signature", status=400)

    try:
        payload = json.loads(body or b"{}")
    except ValueError:
        return HttpResponse("invalid payload", status=400)

    event_id = str(
        payload.get("id")
        or payload.get("event_id")
        or payload.get("gateway_order_id")
        or ""
    )
    if not event_id:
        return HttpResponse("missing event id", status=400)

    event, created = WebhookEvent.objects.get_or_create(
        event_id=event_id,
        defaults={
            "gateway": gateway_name,
            "event_type": str(payload.get("event") or payload.get("type") or ""),
            "payload": payload,
            "signature_verified": True,
        },
    )
    if not created and event.processed:
        return JsonResponse({"status": "already processed"})

    try:
        _process_webhook_event(gateway_name, payload)
        event.processed = True
        event.save(update_fields=["processed", "updated_at"])
    except Exception as exc:
        logger.exception("Webhook processing failed for event %s", event_id)
        event.processing_error = str(exc)[:500]
        event.save(update_fields=["processing_error", "updated_at"])
        return HttpResponse("processing error", status=500)

    return JsonResponse({"status": "ok"})


def _process_webhook_event(gateway_name, payload):
    """Apply a verified webhook to the matching order."""
    reference = (
        payload.get("gateway_order_id")
        or _dig(payload, "payload", "payment", "entity", "order_id")
        or _dig(payload, "data", "object", "id")
        or ""
    )
    if not reference:
        return

    payment = Payment.objects.filter(gateway_order_id=reference).first()
    if payment is None:
        logger.info("Webhook referenced unknown gateway order %s", reference)
        return

    event_type = str(payload.get("event") or payload.get("type") or "")

    if "fail" in event_type:
        order_services.mark_payment_failed(payment.order, reason=event_type)
        return

    if payment.status != Payment.Status.CAPTURED:
        payment.status = Payment.Status.CAPTURED
        payment.save(update_fields=["status", "updated_at"])

    with transaction.atomic():
        order_services.mark_paid(payment.order, payment)


def _dig(data, *keys):
    """Safely walk a nested webhook payload."""
    node = data
    for key in keys:
        if not isinstance(node, dict):
            return None
        node = node.get(key)
    return node
