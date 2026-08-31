"""Give existing stock a warehouse to live in.

Every inventory row predates warehouses and has none, which would leave the
inventory report grouping everything under "unassigned". They were all in one
place, so one default warehouse is created and every row is pointed at it.

The old free-text ``warehouse_location`` is kept, not thrown away: it was
holding values like "BLR-A1", which are aisle references within a building
rather than the building itself. Where one names a city we recognise, it
seeds that warehouse's own location field.

Reversible: the reverse detaches the rows and removes the warehouse it made.
"""
from django.db import migrations

DEFAULT_CODE = "main"


def create_default(apps, schema_editor):
    Warehouse = apps.get_model("inventory", "Warehouse")
    Inventory = apps.get_model("inventory", "Inventory")
    Country = apps.get_model("geo", "Country")

    if not Inventory.objects.exists():
        return

    warehouse, _created = Warehouse.objects.get_or_create(
        code=DEFAULT_CODE,
        defaults={
            "name": "Main warehouse",
            "is_default": True,
            "is_active": True,
            "priority": 0,
            "country": Country.objects.filter(iso2="IN").first(),
        },
    )
    Inventory.objects.filter(warehouse__isnull=True).update(warehouse=warehouse)


def detach(apps, schema_editor):
    Warehouse = apps.get_model("inventory", "Warehouse")
    Inventory = apps.get_model("inventory", "Inventory")

    Inventory.objects.filter(warehouse__code=DEFAULT_CODE).update(warehouse=None)
    Warehouse.objects.filter(code=DEFAULT_CODE).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("inventory", "0002_inventory_quantity_damaged_and_more"),
        ("geo", "0002_alter_exchangerate_options"),
    ]

    operations = [migrations.RunPython(create_default, detach)]
