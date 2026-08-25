"""Signal receivers for the orders app."""
from django.db.models.signals import post_save
from django.dispatch import receiver

from .models import Order, OrderStatusHistory


@receiver(post_save, sender=Order, dispatch_uid="orders.seed_status_history")
def seed_status_history(sender, instance, created, **kwargs):
    """Record the opening status so tracking pages always show a first step.

    Later transitions are logged explicitly by
    ``apps.orders.services.transition_order`` with the acting user attached.
    """
    if created:
        OrderStatusHistory.objects.create(
            order=instance,
            status=instance.status,
            note="Order placed",
        )
