"""Inventory app configuration."""
from django.apps import AppConfig


class InventoryConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.inventory"
    label = "inventory"
    verbose_name = "Inventory"

    def ready(self):
        # Importing registers the signal receivers declared in signals.py.
        from . import signals  # noqa: F401
