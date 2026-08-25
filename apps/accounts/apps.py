"""Accounts app configuration."""
from django.apps import AppConfig


class AccountsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.accounts"
    label = "accounts"
    verbose_name = "Accounts"

    def ready(self):
        # Importing registers the signal receivers declared in signals.py.
        from . import signals  # noqa: F401
