"""Signal receivers for the accounts app."""
from django.db.models.signals import post_save
from django.dispatch import receiver

from apps.wishlist.models import Wishlist

from .models import User


@receiver(post_save, sender=User, dispatch_uid="accounts.create_wishlist")
def create_wishlist_for_new_user(sender, instance, created, **kwargs):
    """Every account gets a wishlist container up front.

    (Guest-cart merging is handled by ``apps.cart.middleware`` rather than a
    login signal: ``auth.login`` rotates the session key before the signal
    fires, so the pre-login key is already gone by then.)
    """
    if created:
        Wishlist.objects.get_or_create(user=instance)
