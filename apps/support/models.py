"""Customer support tickets (MST spec section 51).

A ticket is a conversation, not a form submission: the customer writes, staff
reply, the customer answers back. Modelling it as a thread is what makes
"what did we already tell them" answerable.

Tickets are never deleted from the customer's side. A closed ticket stays
readable, because the history is the point.
"""
from django.conf import settings
from django.db import models
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.core.models import TimeStampedModel


class Ticket(TimeStampedModel):
    """One support conversation."""

    class Status(models.TextChoices):
        OPEN = "open", _("Open")
        AWAITING_CUSTOMER = "awaiting_customer", _("Waiting for the customer")
        AWAITING_STAFF = "awaiting_staff", _("Waiting for us")
        RESOLVED = "resolved", _("Resolved")
        CLOSED = "closed", _("Closed")

    class Priority(models.TextChoices):
        LOW = "low", _("Low")
        NORMAL = "normal", _("Normal")
        HIGH = "high", _("High")
        URGENT = "urgent", _("Urgent")

    class Topic(models.TextChoices):
        ORDER = "order", _("An order")
        DELIVERY = "delivery", _("Delivery")
        RETURN = "return", _("Return or refund")
        PAYMENT = "payment", _("Payment")
        PRODUCT = "product", _("A product")
        ACCOUNT = "account", _("My account")
        OTHER = "other", _("Something else")

    reference = models.CharField(max_length=32, unique=True, db_index=True)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="tickets",
    )
    order = models.ForeignKey(
        "orders.Order",
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="tickets",
        help_text=_("The order this is about, if any."),
    )
    topic = models.CharField(max_length=16, choices=Topic.choices, default=Topic.OTHER)
    subject = models.CharField(max_length=150)
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.OPEN, db_index=True
    )
    priority = models.CharField(
        max_length=8, choices=Priority.choices, default=Priority.NORMAL
    )
    assigned_to = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="assigned_tickets",
        limit_choices_to={"is_staff": True},
    )
    last_reply_at = models.DateTimeField(default=timezone.now, db_index=True)
    resolved_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        ordering = ["-last_reply_at", "-id"]
        indexes = [models.Index(fields=["user", "status"])]

    def __str__(self):
        return f"{self.reference} - {self.subject}"

    def get_absolute_url(self):
        return reverse("support:detail", kwargs={"reference": self.reference})

    @property
    def is_open(self):
        return self.status not in {self.Status.RESOLVED, self.Status.CLOSED}

    @property
    def can_reply(self):
        """A closed ticket is read-only; reopening means a new one."""
        return self.status != self.Status.CLOSED


class TicketMessage(TimeStampedModel):
    """One message in a ticket thread."""

    ticket = models.ForeignKey(
        Ticket, on_delete=models.CASCADE, related_name="messages"
    )
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="ticket_messages",
    )
    author_label = models.CharField(
        max_length=150,
        blank=True,
        help_text=_("Copied at the time, so the thread survives the account."),
    )
    body = models.TextField()
    is_staff_reply = models.BooleanField(default=False)
    is_internal_note = models.BooleanField(
        default=False,
        help_text=_("Visible to staff only. Never shown to the customer."),
    )
    attachment = models.FileField(
        upload_to="support/%Y/%m/", blank=True, null=True
    )

    class Meta:
        ordering = ["created_at", "id"]

    def __str__(self):
        return f"{self.ticket.reference}: {self.body[:40]}"
