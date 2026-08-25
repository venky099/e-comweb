"""Reviews app configuration."""
from django.apps import AppConfig


class ReviewsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.reviews"
    label = "reviews"
    verbose_name = "Reviews"

    def ready(self):
        # Importing registers the signal receivers declared in signals.py.
        from . import signals  # noqa: F401
