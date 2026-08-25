"""Signal receivers for the catalog app."""
from django.core.cache import cache
from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver
from django.utils import timezone

from apps.inventory.models import Inventory

from .context_processors import NAV_CACHE_KEY
from .models import Brand, Category, Product, ProductVariant


@receiver([post_save, post_delete], sender=Category, dispatch_uid="catalog.bust_nav_cache")
def bust_navigation_cache(sender, instance, **kwargs):
    """Any category edit invalidates the cached mega-menu."""
    cache.delete(NAV_CACHE_KEY)
    cache.delete("home:categories:v1")


@receiver(post_save, sender=ProductVariant, dispatch_uid="catalog.ensure_inventory")
def ensure_inventory_row(sender, instance, created, **kwargs):
    """Guarantee every variant has an inventory row.

    Stock reads assume the row exists; creating it here means a variant added
    through the admin, the API or a fixture behaves identically.
    """
    if created:
        Inventory.objects.get_or_create(variant=instance)


@receiver([post_save, post_delete], sender=Brand, dispatch_uid="catalog.bust_brand_cache")
def bust_brand_cache(sender, instance, **kwargs):
    """The homepage brand wall is cached; any brand edit invalidates it."""
    cache.delete("home:brands:v1")


@receiver(post_save, sender=Product, dispatch_uid="catalog.stamp_published_at")
def stamp_published_at(sender, instance, created, **kwargs):
    """Record when a product first went live (used by 'new arrivals')."""
    if instance.status == Product.Status.PUBLISHED and instance.published_at is None:
        Product.objects.filter(pk=instance.pk).update(published_at=timezone.now())
