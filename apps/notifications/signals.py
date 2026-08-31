"""Turning things that happen into messages.

Listening to model signals rather than editing each service keeps the
notification rules in one readable place -- and means a new message can be
added without touching order or shipping code.

Every handler is defensive: a message that cannot be sent must not undo the
order, parcel or stock change that prompted it.
"""
import logging

from django.db.models.signals import post_save
from django.dispatch import receiver

from apps.notifications import services
from apps.notifications.models import Notification

logger = logging.getLogger("ecommerce")


@receiver(post_save, sender="orders.OrderStatusHistory")
def on_order_status(sender, instance, created, **kwargs):
    """Tell the customer when their order moves."""
    if not created:
        return
    try:
        order = instance.order
        user = order.user
        if user is None:
            return

        messages = {
            "confirmed": ("Order confirmed", "order_confirmed", Notification.Kind.ORDER),
            "shipped": ("Order shipped", "order_shipped", Notification.Kind.SHIPPING),
            "out_for_delivery": ("Out for delivery", "order_out_for_delivery", Notification.Kind.SHIPPING),
            "delivered": ("Order delivered", "order_delivered", Notification.Kind.SHIPPING),
            "cancelled": ("Order cancelled", "order_cancelled", Notification.Kind.ORDER),
            "refunded": ("Refund complete", "order_refunded", Notification.Kind.RETURN),
        }
        entry = messages.get(instance.status)
        if entry is None:
            return

        title, code, kind = entry
        services.send(
            code,
            to=order.email,
            context={"order": order, "user": user, "subject": f"{title} - {order.order_number}"},
            user=user,
            notify={
                "kind": kind,
                "title": f"{title}: {order.order_number}",
                "body": (order.shipping_method_name or "")[:500],
                "url": order.get_absolute_url(),
            },
        )
    except Exception:
        logger.exception("Could not notify about order status %s", instance.pk)


@receiver(post_save, sender="inventory.Inventory")
def on_low_stock(sender, instance, created, **kwargs):
    """Low-stock alert (MST section 33).

    Recorded for staff rather than emailed on every save: stock changes on
    every order, and an inbox that fills up is an inbox nobody reads.
    """
    if created:
        return
    try:
        if not instance.needs_reorder or instance.sellable_quantity <= 0:
            return

        from django.contrib.auth import get_user_model

        recipients = get_user_model().objects.filter(is_staff=True, is_active=True)[:10]
        variant = instance.variant
        for staff in recipients:
            already = Notification.objects.filter(
                user=staff,
                kind=Notification.Kind.STOCK,
                title__startswith=f"Low stock: {variant.sku}",
                read_at__isnull=True,
            ).exists()
            if already:
                continue
            services.record(
                staff,
                kind=Notification.Kind.STOCK,
                title=f"Low stock: {variant.sku}",
                body=f"{variant} is down to {instance.sellable_quantity} sellable.",
            )
    except Exception:
        logger.exception("Could not raise a low-stock alert for inventory %s", instance.pk)
