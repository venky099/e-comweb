"""Opening tickets and replying to them.

Everything that changes a ticket goes through here so the status rules live
in one place: a customer reply puts the ticket back on us, a staff reply puts
it back on them, and neither silently reopens something that was closed.
"""
import logging

from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext as _

from apps.support.models import Ticket, TicketMessage

logger = logging.getLogger("ecommerce")


class SupportError(Exception):
    """Raised when a ticket cannot be changed as asked."""


def _reference():
    from apps.invoices.models import NumberSeries

    try:
        return NumberSeries.allocate(NumberSeries.Kind.SUPPORT)
    except Exception:
        # Support must stay reachable even if the counter is unavailable --
        # a customer who cannot ask for help has no other route.
        logger.exception("Falling back to a timestamp ticket reference")
        return f"SUP-{timezone.now():%Y%m%d%H%M%S}"


@transaction.atomic
def open_ticket(user, subject, body, topic=Ticket.Topic.OTHER, order=None, attachment=None):
    """Start a conversation."""
    subject = (subject or "").strip()
    body = (body or "").strip()
    if not subject:
        raise SupportError(_("Give your message a subject."))
    if not body:
        raise SupportError(_("Tell us what you need help with."))

    if order is not None and order.user_id != user.id:
        # Never let a ticket attach to somebody else's order.
        raise SupportError(_("That order is not on your account."))

    ticket = Ticket.objects.create(
        reference=_reference(),
        user=user,
        order=order,
        topic=topic,
        subject=subject[:150],
        status=Ticket.Status.AWAITING_STAFF,
    )
    add_message(ticket, user, body, attachment=attachment)
    return ticket


@transaction.atomic
def add_message(ticket, author, body, is_internal_note=False, attachment=None):
    """Append a message and move the ticket to whoever has the ball."""
    body = (body or "").strip()
    if not body:
        raise SupportError(_("A reply cannot be empty."))
    if not ticket.can_reply:
        raise SupportError(_("This ticket is closed. Please open a new one."))

    is_staff_reply = bool(author and author.is_staff and author.id != ticket.user_id)

    message = TicketMessage.objects.create(
        ticket=ticket,
        author=author,
        author_label=(str(author) if author else "")[:150],
        body=body,
        is_staff_reply=is_staff_reply,
        is_internal_note=is_internal_note and is_staff_reply,
    )

    # An internal note is staff talking to each other; it does not change
    # whose turn it is, and the customer never sees it.
    if not message.is_internal_note:
        ticket.status = (
            Ticket.Status.AWAITING_CUSTOMER if is_staff_reply else Ticket.Status.AWAITING_STAFF
        )
        ticket.last_reply_at = timezone.now()
        ticket.save(update_fields=["status", "last_reply_at", "updated_at"])
        if is_staff_reply:
            _notify_customer(ticket, message)

    return message


def _notify_customer(ticket, message):
    from apps.notifications import services as notification_services
    from apps.notifications.models import Notification

    try:
        notification_services.send(
            "support_reply",
            to=ticket.user.email,
            context={
                "ticket": ticket,
                "message": message,
                "user": ticket.user,
                "subject": f"Re: {ticket.subject} ({ticket.reference})",
            },
            user=ticket.user,
            notify={
                "kind": Notification.Kind.ACCOUNT,
                "title": f"Reply to {ticket.reference}",
                "body": ticket.subject,
                "url": ticket.get_absolute_url(),
            },
        )
    except Exception:
        logger.exception("Could not notify about ticket %s", ticket.reference)


@transaction.atomic
def set_status(ticket, status, actor=None):
    """Move a ticket, recording when it was resolved."""
    if status not in dict(Ticket.Status.choices):
        raise SupportError(_("That is not a valid status."))

    ticket.status = status
    fields = ["status", "updated_at"]
    if status == Ticket.Status.RESOLVED and ticket.resolved_at is None:
        ticket.resolved_at = timezone.now()
        fields.append("resolved_at")
    ticket.save(update_fields=fields)
    return ticket


def visible_messages(ticket, viewer):
    """Thread messages, hiding internal notes from the customer."""
    messages = ticket.messages.select_related("author")
    if viewer is not None and viewer.is_staff:
        return messages
    return messages.filter(is_internal_note=False)
