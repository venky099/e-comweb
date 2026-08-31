"""Order service layer.

Order placement is the one place where money, stock and coupons all have to
agree. Every figure written to an ``Order`` is recomputed here from the
database -- the checkout form contributes an address, a payment method and a
coupon *code*, and nothing else.

Stock lifecycle across the flow:

    place_order()      reserves stock for the new (unpaid) order
    mark_paid()        commits the reservation into a sale
    mark_payment_failed() / cancel_order() before payment releases it
    cancel_order() / complete_return() after payment restores it
"""
import logging
from datetime import timedelta
from decimal import ROUND_HALF_UP, Decimal

from django.conf import settings
from django.db import transaction
from django.db.models import F
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.catalog.models import Product
from apps.coupons import services as coupon_services
from apps.geo import services as geo_services
from apps.inventory import services as inventory_services
from apps.inventory.models import StockMovement
from apps.shipping import services as shipping_services
from apps.tax import services as tax_services

from .models import Order, OrderItem, OrderStatusHistory, ReturnRequest

logger = logging.getLogger("ecommerce")

ZERO = Decimal("0.00")


def money(value):
    return Decimal(value or 0).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


class OrderError(Exception):
    """Raised when an order operation is not allowed."""


def destination_for(snapshot):
    """Resolve a shipping address into the country and state tax needs.

    Addresses store text, not foreign keys, because an address must keep
    reading correctly after a country is renamed or deactivated. Tax and
    shipping need the real rows, so they are looked up here -- and a miss
    returns None rather than a guess, which makes the order untaxed instead
    of wrongly taxed.
    """
    from apps.geo.models import Country, State

    name = (snapshot.get("country") or "").strip()
    if not name:
        return None, None
    country = Country.objects.filter(name__iexact=name).first()
    if country is None and len(name) in (2, 3):
        country = Country.objects.filter(iso2__iexact=name).first()
    if country is None:
        return None, None

    state_name = (snapshot.get("state") or "").strip()
    state = (
        State.objects.filter(country=country, name__iexact=state_name).first()
        if state_name
        else None
    )
    return country, state




# ---------------------------------------------------------------------------
# Placement
# ---------------------------------------------------------------------------
@transaction.atomic
def place_order(
    user,
    cart,
    address,
    payment_method,
    customer_note="",
    currency=None,
    shipping_method_code=None,
):
    """Turn a cart into an order.

    Runs entirely inside one transaction with inventory rows locked, so two
    concurrent checkouts cannot oversell the same unit. Raises ``OrderError``
    (rolling everything back) if anything no longer checks out.
    """
    items = list(cart.live_items())
    if not items:
        raise OrderError(_("Your cart is empty."))

    if payment_method not in dict(Order.PaymentMethod.choices):
        raise OrderError(_("Choose a valid payment method."))

    # ---- 1. Stock, re-checked under lock -------------------------------
    problems = inventory_services.check_availability(items)
    if problems:
        item, available = problems[0]
        raise OrderError(
            _("%(name)s is no longer available in the quantity you wanted (%(n)d left).")
            % {"name": item.variant.product.name, "n": available}
        )

    # ---- 2. COD eligibility --------------------------------------------
    if payment_method == Order.PaymentMethod.COD:
        blocked = [i for i in items if not i.variant.product.is_cod_available]
        if blocked:
            raise OrderError(
                _("%(name)s is not available for cash on delivery.")
                % {"name": blocked[0].variant.product.name}
            )

    # ---- 3. Money, recomputed from the database ------------------------
    subtotal = money(sum((i.line_total for i in items), ZERO))
    product_discount = money(sum((i.line_savings for i in items), ZERO))

    coupon = cart.coupon
    coupon_discount = ZERO
    free_shipping = False
    if coupon is not None:
        is_valid, reason = coupon.check_validity(
            user=user, cart_total=subtotal, cart_items=items
        )
        if not is_valid:
            raise OrderError(reason)
        coupon_discount = coupon.discount_for(subtotal, cart_items=items)
        free_shipping = coupon.gives_free_shipping()

    discounted = money(max(subtotal - coupon_discount, ZERO))

    # ---- 4. Where it is going ------------------------------------------
    snapshot = address.as_snapshot()
    country, state = destination_for(snapshot)
    if country is not None and not country.shipping_enabled:
        raise OrderError(
            _("We do not currently deliver to %(country)s.") % {"country": country.name}
        )

    # ---- 5. Delivery ----------------------------------------------------
    # Quoted, not assumed: the customer picked a method and a price was shown
    # with it, so the same quote is recomputed here and the chosen option
    # must still be on offer.
    options = shipping_services.quote(items, country, discounted)
    chosen = None
    if free_shipping:
        # A free-shipping coupon still needs a method, just not a charge.
        chosen = (
            shipping_services.option_by_code(options, shipping_method_code)
            or shipping_services.default_option(options)
        )
        delivery_charge = ZERO
    elif options:
        chosen = shipping_services.option_by_code(options, shipping_method_code)
        if chosen is None and shipping_method_code:
            raise OrderError(
                _("That delivery option is no longer available. Please choose again.")
            )
        chosen = chosen or shipping_services.default_option(options)
        delivery_charge = money(chosen.price)
    elif shipping_services.has_any_rates() and country is not None:
        raise OrderError(
            _("We could not find a delivery option for %(country)s.")
            % {"country": country.name}
        )
    else:
        # No rate table configured -- behave exactly as the single-country
        # shop did before shipping zones existed.
        delivery_charge = money(shipping_services.legacy_flat_charge(discounted))

    # ---- 6. Tax ---------------------------------------------------------
    tax_result = tax_services.compute(items, country, state)
    tax_amount = money(tax_result.total)
    if tax_amount == ZERO and country is None:
        # Nowhere to look tax up: fall back to the flat legacy rate so an
        # unrecognised country is not silently sold to untaxed.
        legacy_rate = Decimal(settings.TAX_RATE_PERCENT)
        if legacy_rate > ZERO:
            tax_amount = money(discounted * legacy_rate / Decimal("100"))

    total = money(discounted + delivery_charge + tax_amount)

    # ---- 7. Freeze the exchange rate (spec section 60) ------------------
    base = geo_services.base_currency(required=False)
    charged = currency or base
    if charged is not None and base is not None and charged.pk != base.pk:
        rate = geo_services.rate_for(charged)
    else:
        rate = Decimal("1")

    def charged_amount(amount):
        """Convert once, at the frozen rate, in the charged currency."""
        if charged is None:
            return money(amount)
        return geo_services.convert(amount, charged, rate=rate)

    charged_total = charged_amount(total)

    # ---- 8. Create the order -------------------------------------------
    order = Order.objects.create(
        user=user,
        email=user.email,
        phone=user.phone or snapshot["phone"],
        status=Order.Status.PENDING,
        payment_status=Order.PaymentStatus.PENDING,
        payment_method=payment_method,
        subtotal=subtotal,
        product_discount=product_discount,
        coupon=coupon,
        coupon_code=coupon.code if coupon else "",
        coupon_discount=coupon_discount,
        delivery_charge=delivery_charge,
        tax_amount=tax_amount,
        total_amount=total,
        currency=charged.code if charged else settings.DEFAULT_CURRENCY,
        base_currency=base.code if base else settings.DEFAULT_CURRENCY,
        exchange_rate=rate,
        charged_subtotal=charged_amount(subtotal),
        charged_discount=charged_amount(product_discount + coupon_discount),
        charged_delivery_charge=charged_amount(delivery_charge),
        charged_tax_amount=charged_amount(tax_amount),
        charged_total=charged_total,
        destination_country=country,
        shipping_method=chosen.method if chosen else None,
        shipping_method_name=chosen.name if chosen else "",
        customer_note=customer_note or "",
        shipping_full_name=snapshot["full_name"],
        shipping_phone=snapshot["phone"],
        shipping_line1=snapshot["line1"],
        shipping_line2=snapshot["line2"],
        shipping_landmark=snapshot["landmark"],
        shipping_city=snapshot["city"],
        shipping_state=snapshot["state"],
        shipping_country=snapshot["country"],
        shipping_postal_code=snapshot["postal_code"],
        estimated_delivery=timezone.localdate()
        + timedelta(days=chosen.method.max_days if chosen else 5),
    )

    # The tax breakdown is stored, not recomputed: rules and rates change,
    # and an invoice must still show what was charged on the day.
    tax_services.save_lines(order, tax_result)

    # ---- 9. Snapshot the lines and reserve stock -----------------------
    for item in items:
        variant = item.variant
        product = variant.product
        primary = product.primary_image
        image_url = ""
        if variant.image:
            image_url = variant.image.url
        elif primary:
            image_url = primary.image.url

        OrderItem.objects.create(
            order=order,
            variant=variant,
            product=product,
            product_name=product.name,
            variant_label=variant.label,
            sku=variant.sku,
            image_url=image_url,
            unit_price=item.unit_price,
            unit_mrp=item.unit_mrp,
            quantity=item.quantity,
            is_returnable=product.is_returnable,
        )
        inventory_services.reserve(
            variant, item.quantity, reference=order.order_number, user=user
        )

    # ---- 10. Burn the coupon -------------------------------------------
    if coupon is not None:
        coupon_services.redeem(coupon, user, order, coupon_discount)

    # ---- 11. Empty the cart --------------------------------------------
    cart.clear()

    logger.info("Order %s placed by user %s for %s", order.order_number, user.pk, total)
    return order


# ---------------------------------------------------------------------------
# Payment outcomes
# ---------------------------------------------------------------------------
def _commit_stock(order, user=None):
    """Convert this order's reservation into a sale, exactly once."""
    if order.stock_committed:
        return False

    for item in order.items.select_related("variant"):
        if item.variant_id:
            inventory_services.commit(
                item.variant, item.quantity, reference=order.order_number, user=user
            )
        if item.product_id:
            Product.objects.filter(pk=item.product_id).update(
                sold_count=F("sold_count") + item.quantity
            )

    order.stock_committed = True
    order.save(update_fields=["stock_committed", "updated_at"])
    return True


@transaction.atomic
def mark_paid(order, payment=None):
    """Payment captured: commit reserved stock and confirm the order."""
    order = Order.objects.select_for_update().get(pk=order.pk)
    if order.payment_status == Order.PaymentStatus.PAID:
        return order  # webhook retry -- nothing to do

    _commit_stock(order)

    order.payment_status = Order.PaymentStatus.PAID
    order.status = Order.Status.CONFIRMED
    order.confirmed_at = timezone.now()
    order.save(
        update_fields=["payment_status", "status", "confirmed_at", "updated_at"]
    )
    _log_status(order, Order.Status.CONFIRMED, note="Payment received")
    logger.info("Order %s marked paid", order.order_number)
    return order


@transaction.atomic
def confirm_cod(order):
    """COD orders are confirmed without payment, but stock is committed."""
    order = Order.objects.select_for_update().get(pk=order.pk)
    if order.status != Order.Status.PENDING:
        return order

    _commit_stock(order)

    order.status = Order.Status.CONFIRMED
    order.confirmed_at = timezone.now()
    order.save(update_fields=["status", "confirmed_at", "updated_at"])
    _log_status(order, Order.Status.CONFIRMED, note="Cash on delivery confirmed")
    return order


@transaction.atomic
def mark_payment_failed(order, reason=""):
    """Payment failed: release the reservation, keep the order for retry."""
    order = Order.objects.select_for_update().get(pk=order.pk)
    if order.payment_status == Order.PaymentStatus.PAID:
        return order

    for item in order.items.select_related("variant"):
        if item.variant_id:
            inventory_services.release(
                item.variant, item.quantity, reference=order.order_number
            )

    order.payment_status = Order.PaymentStatus.FAILED
    order.save(update_fields=["payment_status", "updated_at"])
    _log_status(order, order.status, note=f"Payment failed: {reason}"[:255])
    return order


# ---------------------------------------------------------------------------
# Status transitions
# ---------------------------------------------------------------------------
def _log_status(order, status, note="", user=None):
    OrderStatusHistory.objects.create(
        order=order, status=status, note=note or "", changed_by=user
    )


@transaction.atomic
def transition_order(order, new_status, user=None, note=""):
    """Move an order to ``new_status``, enforcing the allowed transitions."""
    order = Order.objects.select_for_update().get(pk=order.pk)

    if new_status == order.status:
        return order
    if not order.can_transition_to(new_status):
        raise OrderError(
            _("Cannot move an order from %(old)s to %(new)s.")
            % {"old": order.get_status_display(), "new": new_status}
        )

    now = timezone.now()
    fields = ["status", "updated_at"]
    order.status = new_status

    if new_status == Order.Status.CONFIRMED:
        # Confirming from the admin must convert the reservation into a sale,
        # exactly as the checkout and payment paths do.
        _commit_stock(order, user=user)
        if not order.confirmed_at:
            order.confirmed_at = now
            fields.append("confirmed_at")
    elif new_status == Order.Status.SHIPPED:
        order.shipped_at = now
        fields.append("shipped_at")
    elif new_status == Order.Status.DELIVERED:
        order.delivered_at = now
        fields.append("delivered_at")
        if order.is_cod and order.payment_status != Order.PaymentStatus.PAID:
            # Cash changes hands at the door.
            order.payment_status = Order.PaymentStatus.PAID
            fields.append("payment_status")
    elif new_status == Order.Status.RETURNED:
        order.returned_at = now
        fields.append("returned_at")
    elif new_status == Order.Status.CANCELLED:
        order.cancelled_at = now
        fields.append("cancelled_at")

    order.save(update_fields=fields)
    _log_status(order, new_status, note=note, user=user)
    logger.info("Order %s -> %s", order.order_number, new_status)
    return order


@transaction.atomic
def cancel_order(order, user=None, reason="", staff_override=False):
    """Cancel an order, returning stock and the coupon use.

    Whether stock is *released* (never sold) or *restored* (sold and coming
    back) depends on whether the order was already paid.
    """
    order = Order.objects.select_for_update().get(pk=order.pk)

    if not staff_override and not order.can_be_cancelled:
        raise OrderError(
            _("This order can no longer be cancelled. Please request a return instead.")
        )
    if order.status in {Order.Status.CANCELLED, Order.Status.RETURNED, Order.Status.REFUNDED}:
        raise OrderError(_("This order is already closed."))

    # Released when the units were only ever reserved; restored when they were
    # already counted as sold.
    was_committed = order.stock_committed

    for item in order.items.select_related("variant"):
        if not item.variant_id:
            continue
        if was_committed:
            inventory_services.restore(
                item.variant,
                item.quantity,
                reason=StockMovement.Reason.CANCELLATION,
                reference=order.order_number,
                user=user,
            )
            if item.product_id:
                Product.objects.filter(
                    pk=item.product_id, sold_count__gte=item.quantity
                ).update(sold_count=F("sold_count") - item.quantity)
        else:
            inventory_services.release(
                item.variant, item.quantity, reference=order.order_number, user=user
            )

    order.items.update(status=OrderItem.ItemStatus.CANCELLED)

    coupon_services.revoke(order)

    order.status = Order.Status.CANCELLED
    order.cancelled_at = timezone.now()
    order.cancel_reason = (reason or "")[:255]
    order.stock_committed = False
    if order.payment_status == Order.PaymentStatus.PAID:
        order.payment_status = Order.PaymentStatus.REFUND_PENDING
    order.save(
        update_fields=[
            "status",
            "cancelled_at",
            "cancel_reason",
            "payment_status",
            "stock_committed",
            "updated_at",
        ]
    )
    _log_status(order, Order.Status.CANCELLED, note=reason, user=user)
    logger.info("Order %s cancelled", order.order_number)
    return order


# ---------------------------------------------------------------------------
# Returns
# ---------------------------------------------------------------------------
@transaction.atomic
def request_return(order_item, quantity, reason, comment="", user=None):
    """Open a return request against one delivered line."""
    order = order_item.order

    if order.status != Order.Status.DELIVERED:
        raise OrderError(_("Only delivered orders can be returned."))
    if not order_item.is_returnable:
        raise OrderError(_("This item is not eligible for return."))
    if not order.can_be_returned:
        raise OrderError(
            _("The %(days)d-day return window for this order has closed.")
            % {"days": settings.RETURN_WINDOW_DAYS}
        )

    quantity = int(quantity)
    already = sum(
        r.quantity
        for r in order_item.return_requests.exclude(status=ReturnRequest.Status.REJECTED)
    )
    if quantity <= 0 or already + quantity > order_item.quantity:
        raise OrderError(
            _("You can return at most %(n)d of this item.")
            % {"n": order_item.quantity - already}
        )

    request = ReturnRequest.objects.create(
        order=order,
        order_item=order_item,
        quantity=quantity,
        reason=reason,
        comment=comment or "",
        refund_amount=money(order_item.unit_price * quantity),
    )

    order_item.status = OrderItem.ItemStatus.RETURN_REQUESTED
    order_item.save(update_fields=["status", "updated_at"])

    if order.status == Order.Status.DELIVERED:
        order.status = Order.Status.RETURN_REQUESTED
        order.save(update_fields=["status", "updated_at"])
        _log_status(order, Order.Status.RETURN_REQUESTED, note="Return requested", user=user)

    logger.info("Return requested on order %s item %s", order.order_number, order_item.pk)
    return request


@transaction.atomic
def process_return(return_request, new_status, user=None, note=""):
    """Advance a return. Completing one puts stock back on the shelf."""
    return_request = ReturnRequest.objects.select_for_update().get(pk=return_request.pk)
    order = return_request.order

    return_request.status = new_status
    return_request.staff_note = (note or return_request.staff_note)[:255]
    return_request.processed_by = user
    return_request.processed_at = timezone.now()
    return_request.save(
        update_fields=["status", "staff_note", "processed_by", "processed_at", "updated_at"]
    )

    if new_status == ReturnRequest.Status.REJECTED:
        item = return_request.order_item
        item.status = OrderItem.ItemStatus.ACTIVE
        item.save(update_fields=["status", "updated_at"])
        if not order.return_requests.exclude(
            status=ReturnRequest.Status.REJECTED
        ).exists():
            order.status = Order.Status.DELIVERED
            order.save(update_fields=["status", "updated_at"])
            _log_status(order, Order.Status.DELIVERED, note="Return rejected", user=user)
        return return_request

    if new_status in {ReturnRequest.Status.COMPLETED, ReturnRequest.Status.REFUNDED}:
        item = return_request.order_item
        if item.variant_id:
            inventory_services.restore(
                item.variant,
                return_request.quantity,
                reason=StockMovement.Reason.RETURN,
                reference=order.order_number,
                user=user,
            )
        if item.product_id:
            Product.objects.filter(
                pk=item.product_id, sold_count__gte=return_request.quantity
            ).update(sold_count=F("sold_count") - return_request.quantity)

        item.status = OrderItem.ItemStatus.RETURNED
        item.save(update_fields=["status", "updated_at"])

        order.status = Order.Status.RETURNED
        order.returned_at = timezone.now()
        order.payment_status = Order.PaymentStatus.REFUND_PENDING
        order.save(
            update_fields=["status", "returned_at", "payment_status", "updated_at"]
        )
        _log_status(order, Order.Status.RETURNED, note="Return completed", user=user)

    return return_request


@transaction.atomic
def record_refund(order, amount, user=None, note=""):
    """Mark an order refunded (fully or partially) after the money is sent."""
    order = Order.objects.select_for_update().get(pk=order.pk)
    amount = money(amount)

    order.refunded_amount = money(order.refunded_amount + amount)
    if order.refunded_amount >= order.total_amount:
        order.payment_status = Order.PaymentStatus.REFUNDED
        order.status = Order.Status.REFUNDED
    else:
        order.payment_status = Order.PaymentStatus.PARTIALLY_REFUNDED
    order.save(
        update_fields=["refunded_amount", "payment_status", "status", "updated_at"]
    )
    _log_status(order, order.status, note=note or f"Refunded {amount}", user=user)
    return order
