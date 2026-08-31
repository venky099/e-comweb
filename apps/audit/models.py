"""Who changed what, from what, to what.

MST section 62 calls this "extremely important for a production e-commerce
platform" and shows exactly what it wants recorded:

    Admin: admin@company.com
    Action: Changed product price
    Product: Designer Silk Saree
    Old Price: Rs.5,000
    New Price: Rs.5,500

Django's own admin log stores a change message -- "Changed price" -- but not
the values, which is the half that settles an argument. So this records the
before and after of every field that moved.

Entries are written once and never edited. An audit log that can be edited
answers nothing.
"""
from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.core.models import TimeStampedModel


class AuditLog(TimeStampedModel):
    """One recorded change."""

    class Action(models.TextChoices):
        CREATE = "create", _("Created")
        UPDATE = "update", _("Changed")
        DELETE = "delete", _("Deleted")
        LOGIN = "login", _("Signed in")
        LOGIN_FAILED = "login_failed", _("Failed sign-in")
        EXPORT = "export", _("Exported data")
        ACTION = "action", _("Performed an action")

    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="audit_entries",
        help_text=_("Null once the account is deleted; actor_label keeps the name."),
    )
    actor_label = models.CharField(
        max_length=150,
        blank=True,
        help_text=_("Copied at the time, so the record survives the account."),
    )
    action = models.CharField(max_length=16, choices=Action.choices, db_index=True)
    model_label = models.CharField(
        max_length=100, blank=True, db_index=True, help_text=_("e.g. catalog.Product")
    )
    object_id = models.CharField(max_length=64, blank=True, db_index=True)
    object_label = models.CharField(
        max_length=255, blank=True, help_text=_("What the record was called.")
    )
    changes = models.JSONField(
        default=dict,
        blank=True,
        help_text=_('{"field": {"from": ..., "to": ...}}'),
    )
    summary = models.CharField(max_length=255, blank=True)
    ip_address = models.GenericIPAddressField(blank=True, null=True)
    user_agent = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        verbose_name = _("audit entry")
        verbose_name_plural = _("audit log")
        indexes = [
            models.Index(fields=["model_label", "object_id"]),
            models.Index(fields=["-created_at"]),
        ]

    def __str__(self):
        who = self.actor_label or _("system")
        return f"{who} {self.get_action_display().lower()} {self.object_label or self.model_label}"

    @property
    def changed_fields(self):
        return sorted(self.changes.keys())

    def describe(self):
        """A readable line per changed field, in the spec's own shape."""
        lines = []
        for field in self.changed_fields:
            move = self.changes.get(field) or {}
            lines.append(
                _("%(field)s: %(old)s -> %(new)s")
                % {
                    "field": field.replace("_", " ").capitalize(),
                    "old": move.get("from", ""),
                    "new": move.get("to", ""),
                }
            )
        return lines
