"""Orders app configuration."""
from django.apps import AppConfig


class OrdersConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.orders"
    label = "orders"
    verbose_name = "Orders"

    def ready(self):
        # Importing registers the signal receivers declared in signals.py.
        from . import signals  # noqa: F401
