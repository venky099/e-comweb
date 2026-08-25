"""Stock service layer.

Every quantity change goes through one of these functions. They all lock the
inventory row with ``select_for_update`` inside a transaction, which is what
stops two shoppers from buying the same last unit.

Lifecycle of a unit:

    reserve()  -> promised to an unpaid order   (reserved +1)
    commit()   -> paid for                      (reserved -1, available -1, sold +1)
    release()  -> payment abandoned/failed      (reserved -1)
    restore()  -> cancelled/returned after sale (available +1, sold -1)
"""
import logging

from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from .models import Inventory, StockMovement

logger = logging.getLogger("ecommerce")


class InsufficientStock(Exception):
    """Raised when the requested quantity cannot be satisfied."""

    def __init__(self, variant, requested, available):
        self.variant = variant
        self.requested = requested
        self.available = available
        super().__init__(
            _("Only %(available)d of %(name)s left (you asked for %(requested)d).")
            % {"available": available, "name": str(variant), "requested": requested}
        )


def _locked_inventory(variant_id):
    """Fetch-or-create the inventory row with a row-level lock held."""
    inventory = (
        Inventory.objects.select_for_update().filter(variant_id=variant_id).first()
    )
    if inventory is None:
        inventory, _created = Inventory.objects.get_or_create(variant_id=variant_id)
        inventory = Inventory.objects.select_for_update().get(pk=inventory.pk)
    return inventory


def _log_movement(inventory, reason, quantity, reference="", note="", user=None):
    StockMovement.objects.create(
        variant_id=inventory.variant_id,
        reason=reason,
        quantity=quantity,
        quantity_after=inventory.quantity_available,
        reference=reference,
        note=note,
        created_by=user,
    )


@transaction.atomic
def reserve(variant, quantity, reference="", user=None):
    """Hold ``quantity`` units for an order awaiting payment."""
    quantity = int(quantity)
    if quantity <= 0:
        return None

    inventory = _locked_inventory(variant.pk if hasattr(variant, "pk") else variant)
    if inventory.sellable_quantity < quantity and not inventory.allow_backorder:
        raise InsufficientStock(variant, quantity, inventory.sellable_quantity)

    inventory.quantity_reserved += quantity
    inventory.save(update_fields=["quantity_reserved", "updated_at"])
    _log_movement(
        inventory, StockMovement.Reason.RESERVATION, -quantity, reference, user=user
    )
    return inventory


@transaction.atomic
def release(variant, quantity, reference="", user=None):
    """Give a reservation back (payment failed, checkout abandoned)."""
    quantity = int(quantity)
    if quantity <= 0:
        return None

    inventory = _locked_inventory(variant.pk if hasattr(variant, "pk") else variant)
    inventory.quantity_reserved = max(inventory.quantity_reserved - quantity, 0)
    inventory.save(update_fields=["quantity_reserved", "updated_at"])
    _log_movement(inventory, StockMovement.Reason.RELEASE, quantity, reference, user=user)
    return inventory


@transaction.atomic
def commit(variant, quantity, reference="", user=None):
    """Convert a reservation into a sale: stock actually leaves the shelf."""
    quantity = int(quantity)
    if quantity <= 0:
        return None

    inventory = _locked_inventory(variant.pk if hasattr(variant, "pk") else variant)
    inventory.quantity_reserved = max(inventory.quantity_reserved - quantity, 0)
    inventory.quantity_available = max(inventory.quantity_available - quantity, 0)
    inventory.quantity_sold += quantity
    inventory.save(
        update_fields=[
            "quantity_reserved",
            "quantity_available",
            "quantity_sold",
            "updated_at",
        ]
    )
    _log_movement(inventory, StockMovement.Reason.SALE, -quantity, reference, user=user)
    return inventory


@transaction.atomic
def restore(variant, quantity, reason=None, reference="", user=None):
    """Put sold units back (cancellation or accepted return)."""
    quantity = int(quantity)
    if quantity <= 0:
        return None

    inventory = _locked_inventory(variant.pk if hasattr(variant, "pk") else variant)
    inventory.quantity_available += quantity
    inventory.quantity_sold = max(inventory.quantity_sold - quantity, 0)
    inventory.restocked_at = timezone.now()
    inventory.save(
        update_fields=["quantity_available", "quantity_sold", "restocked_at", "updated_at"]
    )
    _log_movement(
        inventory,
        reason or StockMovement.Reason.CANCELLATION,
        quantity,
        reference,
        user=user,
    )
    return inventory


@transaction.atomic
def adjust(variant, new_quantity, note="", user=None):
    """Set an absolute on-hand figure (manual stocktake)."""
    new_quantity = max(int(new_quantity), 0)
    inventory = _locked_inventory(variant.pk if hasattr(variant, "pk") else variant)
    delta = new_quantity - inventory.quantity_available
    inventory.quantity_available = new_quantity
    if delta > 0:
        inventory.restocked_at = timezone.now()
    inventory.save(update_fields=["quantity_available", "restocked_at", "updated_at"])
    _log_movement(
        inventory, StockMovement.Reason.ADJUSTMENT, delta, note=note, user=user
    )
    return inventory


@transaction.atomic
def restock(variant, quantity, note="", user=None):
    """Add units from a purchase order."""
    quantity = int(quantity)
    if quantity <= 0:
        return None
    inventory = _locked_inventory(variant.pk if hasattr(variant, "pk") else variant)
    inventory.quantity_available += quantity
    inventory.restocked_at = timezone.now()
    inventory.save(update_fields=["quantity_available", "restocked_at", "updated_at"])
    _log_movement(
        inventory, StockMovement.Reason.PURCHASE, quantity, note=note, user=user
    )
    return inventory


def check_availability(items):
    """Validate a basket before charging anyone.

    ``items`` is an iterable of objects exposing ``.variant`` and
    ``.quantity``. Returns a list of ``(item, available)`` for rows that
    cannot be fulfilled -- empty means the whole basket is good.
    """
    problems = []
    for item in items:
        variant = item.variant
        available = variant.available_quantity
        if not variant.is_active or not variant.product.is_active:
            problems.append((item, 0))
        elif available < item.quantity:
            problems.append((item, available))
    return problems
