"""Catalog app configuration."""
from django.apps import AppConfig


class CatalogConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.catalog"
    label = "catalog"
    verbose_name = "Catalog"

    def ready(self):
        # Importing registers the signal receivers declared in signals.py.
        from . import signals  # noqa: F401
