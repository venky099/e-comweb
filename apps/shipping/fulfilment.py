"""Creating parcels and recording what happens to them.

Kept apart from the rating services: quoting a delivery and dispatching one
are different jobs, and only this half writes to an order.
"""
import logging

from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.shipping.shipments import Shipment, ShipmentItem, TrackingEvent

logger = logging.getLogger("ecommerce")


class ShipmentError(Exception):
    """Raised when a parcel cannot be created as asked."""


def remaining_to_ship(order):
    """How much of each order line is not in a parcel yet.

    Returns ``{order_item: quantity}``, skipping anything fully shipped, so a
    second parcel cannot re-send goods that already left.
    """
    already = {}
    for item in ShipmentItem.objects.filter(shipment__order=order):
        already[item.order_item_id] = already.get(item.order_item_id, 0) + item.quantity

    remaining = {}
    for line in order.items.all():
        left = line.quantity - already.get(line.pk, 0)
        if left > 0:
            remaining[line] = left
    return remaining


@transaction.atomic
def create_shipment(order, items=None, carrier="", tracking_number="", method=None, **kwargs):
    """Pack some or all of an order into a parcel.

    ``items`` maps order items to quantities; omitting it ships everything
    still outstanding. Over-shipping is refused rather than silently clamped:
    a request to send three of something we owe two of is a mistake worth
    surfacing.
    """
    outstanding = remaining_to_ship(order)
    if not outstanding:
        raise ShipmentError(_("Every item on this order has already been shipped."))

    if items is None:
        items = outstanding
    else:
        for line, quantity in items.items():
            if quantity <= 0:
                raise ShipmentError(_("A parcel cannot contain zero of an item."))
            if quantity > outstanding.get(line, 0):
                raise ShipmentError(
                    _("Only %(left)d of %(name)s is left to ship.")
                    % {"left": outstanding.get(line, 0), "name": line.product_name}
                )

    from apps.invoices.models import NumberSeries

    weight = sum(
        (getattr(line.variant, "shipping_weight_grams", 0) or 0) * quantity
        for line, quantity in items.items()
        if line.variant_id
    )
    declared = sum(line.unit_price * quantity for line, quantity in items.items())

    shipment = Shipment.objects.create(
        order=order,
        number=NumberSeries.allocate(NumberSeries.Kind.SHIPMENT),
        method=method or order.shipping_method,
        carrier=carrier or (order.shipping_method_name or ""),
        tracking_number=tracking_number,
        weight_grams=weight,
        declared_value=declared,
        contents_description=kwargs.pop(
            "contents_description",
            ", ".join(line.product_name for line in items)[:255],
        ),
        **kwargs,
    )
    ShipmentItem.objects.bulk_create(
        [
            ShipmentItem(shipment=shipment, order_item=line, quantity=quantity)
            for line, quantity in items.items()
        ]
    )
    logger.info("Shipment %s created for order %s", shipment.number, order.order_number)
    return shipment


@transaction.atomic
def record_event(shipment, status, description="", location="", occurred_at=None, raw=None):
    """Log a carrier update and move the parcel's status forward.

    The event is always stored, even when it repeats one already seen --
    carriers resend, and the history should show that they did. Only the
    parcel's current status is deduplicated.
    """
    event = TrackingEvent.objects.create(
        shipment=shipment,
        status=status,
        description=description,
        location=location,
        occurred_at=occurred_at or timezone.now(),
        raw=raw or {},
    )

    if shipment.status != status:
        shipment.status = status
        fields = ["status", "updated_at"]
        if status == Shipment.Status.DISPATCHED and shipment.dispatched_at is None:
            shipment.dispatched_at = event.occurred_at
            fields.append("dispatched_at")
        if status == Shipment.Status.DELIVERED and shipment.delivered_at is None:
            shipment.delivered_at = event.occurred_at
            fields.append("delivered_at")
        shipment.save(update_fields=fields)
        _sync_order(shipment)

    return event


def _fulfilment_path(order, target):
    """The shortest legal run of statuses from where the order is to ``target``.

    Dispatching a parcel from a confirmed order is ordinary -- you pack and
    send without clicking "processing" first -- but the order state machine
    only allows one step at a time. Rather than loosen it, walk it, so the
    history records that the order really did pass through each state.

    Only fulfilment states are used as stepping stones. Routing through
    cancellation would restore stock for a parcel that is on a van.
    """
    from apps.orders.models import Order

    safe = {
        Order.Status.PENDING,
        Order.Status.CONFIRMED,
        Order.Status.PROCESSING,
        Order.Status.SHIPPED,
        Order.Status.OUT_FOR_DELIVERY,
        Order.Status.DELIVERED,
    }

    if order.status == target:
        return []

    queue = [(order.status, [])]
    seen = {order.status}
    while queue:
        node, path = queue.pop(0)
        for step in Order.TRANSITIONS.get(node, set()):
            if step == target:
                return path + [step]
            if step in seen or step not in safe:
                continue
            seen.add(step)
            queue.append((step, path + [step]))
    return None


def _sync_order(shipment):
    """Move the order along when its parcels do.

    An order is only delivered once every parcel is; a single delivered
    parcel out of two would otherwise close an order that is still partly in
    transit.
    """
    from apps.orders.models import Order
    from apps.orders import services as order_services

    order = shipment.order
    parcels = list(order.shipments.all())
    statuses = {p.status for p in parcels}

    target = None
    if statuses == {Shipment.Status.DELIVERED} and not remaining_to_ship(order):
        target = Order.Status.DELIVERED
    elif Shipment.Status.OUT_FOR_DELIVERY in statuses:
        target = Order.Status.OUT_FOR_DELIVERY
    elif statuses & {Shipment.Status.DISPATCHED, Shipment.Status.IN_TRANSIT}:
        target = Order.Status.SHIPPED

    if target is None or order.status == target:
        return

    path = _fulfilment_path(order, target)
    if not path:
        logger.info(
            "Order %s stays at %s; no route to %s",
            order.order_number,
            order.status,
            target,
        )
        return

    for step in path:
        try:
            order_services.transition_order(
                order, step, note=f"Parcel {shipment.number}"
            )
        except order_services.OrderError:
            # A parcel update must never fail because the order will not
            # move -- the parcel really did.
            logger.warning(
                "Could not move order %s to %s", order.order_number, step
            )
            return
        order.refresh_from_db()
