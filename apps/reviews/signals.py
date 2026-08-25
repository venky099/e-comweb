"""Signal receivers for the reviews app."""
from decimal import Decimal

from django.db.models import Avg, Count
from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from apps.catalog.models import Product

from .models import Review


def recalculate_product_rating(product_id):
    """Refresh the denormalised rating columns on a product.

    Product cards read ``rating_average`` / ``rating_count`` directly, so they
    are recomputed here rather than aggregated per row at render time.
    """
    stats = Review.objects.filter(product_id=product_id, is_approved=True).aggregate(
        average=Avg("rating"), total=Count("id")
    )
    average = stats["average"] or 0
    Product.objects.filter(pk=product_id).update(
        rating_average=Decimal(average).quantize(Decimal("0.01")),
        rating_count=stats["total"] or 0,
    )


@receiver(post_save, sender=Review, dispatch_uid="reviews.update_rating_on_save")
def update_rating_on_save(sender, instance, **kwargs):
    recalculate_product_rating(instance.product_id)


@receiver(post_delete, sender=Review, dispatch_uid="reviews.update_rating_on_delete")
def update_rating_on_delete(sender, instance, **kwargs):
    recalculate_product_rating(instance.product_id)
