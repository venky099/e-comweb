"""In-app notifications and admin-editable email templates.

MST sections 42 and 43. The point of storing templates in the database is
that changing "your order has shipped" should not need a developer or a
deploy -- and that a template can exist per language without a code change.

A file template of the same name is the fallback, so a fresh install sends
sensible mail before anyone has written a single row.
"""
from django.conf import settings
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.core.models import TimeStampedModel


class EmailTemplate(TimeStampedModel):
    """One message, editable by an administrator."""

    code = models.SlugField(
        max_length=64,
        help_text=_("Referenced from code, e.g. order_confirmed."),
    )
    language = models.CharField(
        max_length=10,
        default="en",
        help_text=_("Language code. The default language is the fallback."),
    )
    name = models.CharField(max_length=100)
    subject = models.CharField(max_length=255)
    body = models.TextField(
        help_text=_("Django template syntax is available, e.g. {{ order.order_number }}.")
    )
    is_active = models.BooleanField(default=True)
    description = models.CharField(
        max_length=255, blank=True, help_text=_("When this message is sent.")
    )

    class Meta:
        ordering = ["code", "language"]
        constraints = [
            models.UniqueConstraint(
                fields=["code", "language"], name="notifications_template_per_language"
            )
        ]

    def __str__(self):
        return f"{self.name} ({self.language})"


class Notification(TimeStampedModel):
    """Something a customer should see next time they visit."""

    class Kind(models.TextChoices):
        ORDER = "order", _("Order update")
        PAYMENT = "payment", _("Payment")
        SHIPPING = "shipping", _("Delivery")
        RETURN = "return", _("Return or refund")
        STOCK = "stock", _("Back in stock")
        ACCOUNT = "account", _("Account")
        PROMOTION = "promotion", _("Offer")

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="notifications",
    )
    kind = models.CharField(
        max_length=16, choices=Kind.choices, default=Kind.ORDER, db_index=True
    )
    title = models.CharField(max_length=150)
    body = models.CharField(max_length=500, blank=True)
    url = models.CharField(max_length=255, blank=True)
    read_at = models.DateTimeField(blank=True, null=True, db_index=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [models.Index(fields=["user", "read_at"])]

    def __str__(self):
        return self.title

    @property
    def is_read(self):
        return self.read_at is not None

    def mark_read(self):
        if self.read_at is None:
            self.read_at = timezone.now()
            self.save(update_fields=["read_at", "updated_at"])
