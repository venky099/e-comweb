"""Signal receivers for the inventory app."""
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.utils import timezone

from .models import Inventory


@receiver(post_save, sender=Inventory, dispatch_uid="inventory.stamp_restock")
def stamp_restock_time(sender, instance, created, **kwargs):
    """Record when stock first appears, for the inventory movement report."""
    if instance.quantity_available > 0 and instance.restocked_at is None:
        Inventory.objects.filter(pk=instance.pk).update(restocked_at=timezone.now())
