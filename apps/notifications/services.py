"""Sending messages.

One entry point, ``send()``, resolving a template by code and language:

    1. an active row for this code in this language
    2. an active row for this code in the default language
    3. a file template, templates/emails/<code>.html
    4. nothing -- log and move on

The last step matters. A missing template must never break the thing that
triggered the message: an order that succeeded is not undone because nobody
wrote the confirmation email yet.
"""
import logging

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.template import Context, Template, TemplateDoesNotExist
from django.template.loader import render_to_string
from django.utils import timezone, translation

from apps.notifications.models import EmailTemplate, Notification

logger = logging.getLogger("ecommerce")


def default_language():
    return (getattr(settings, "LANGUAGE_CODE", "en") or "en").split("-")[0]


def find_template(code, language=None):
    """The best database template for a code, or None."""
    language = (language or translation.get_language() or default_language()).split("-")[0]
    return (
        EmailTemplate.objects.filter(code=code, language=language, is_active=True).first()
        or EmailTemplate.objects.filter(
            code=code, language=default_language(), is_active=True
        ).first()
    )


def render(code, context, language=None):
    """Return ``(subject, html)`` for a message, or ``(None, None)``.

    Database templates are rendered with Django's own engine, so an admin can
    use ``{{ order.order_number }}`` exactly as a file template would. They
    are rendered without the request context on purpose -- a template someone
    edits in a browser should not reach into the session.
    """
    row = find_template(code, language)
    if row is not None:
        try:
            subject = Template(row.subject).render(Context(context))
            body = Template(row.body).render(Context(context))
            return subject.strip(), body
        except Exception:
            logger.exception("Email template %s failed to render", code)
            return None, None

    try:
        body = render_to_string(f"emails/{code}.html", context)
    except TemplateDoesNotExist:
        return None, None

    subject = context.get("subject") or code.replace("_", " ").title()
    return subject, body


def send(code, to, context=None, language=None, user=None, notify=None):
    """Send one message. Returns True if mail actually went out.

    ``notify`` optionally records an in-app notification as well, as
    ``{"kind": ..., "title": ..., "body": ..., "url": ...}``.
    """
    context = dict(context or {})
    context.setdefault("site_name", getattr(settings, "SITE_NAME", ""))
    context.setdefault("support_email", getattr(settings, "SUPPORT_EMAIL", ""))

    sent = False
    recipients = [address for address in ([to] if isinstance(to, str) else to or []) if address]

    if recipients:
        subject, body = render(code, context, language)
        if subject is None:
            logger.warning("No email template for %s -- nothing sent", code)
        else:
            try:
                message = EmailMultiAlternatives(
                    subject=subject,
                    body=body,
                    from_email=getattr(settings, "DEFAULT_FROM_EMAIL", None),
                    to=recipients,
                )
                message.attach_alternative(body, "text/html")
                message.send(fail_silently=False)
                sent = True
            except Exception:
                # A mail server that is down must not roll back an order.
                logger.exception("Could not send the %s email", code)

    if notify and user is not None:
        record(user, **notify)

    return sent


def record(user, kind=Notification.Kind.ORDER, title="", body="", url=""):
    """Store an in-app notification. Never raises."""
    try:
        return Notification.objects.create(
            user=user, kind=kind, title=title[:150], body=body[:500], url=url[:255]
        )
    except Exception:
        logger.exception("Could not record a notification for user %s", getattr(user, "pk", None))
        return None


def unread_count(user):
    if not getattr(user, "is_authenticated", False):
        return 0
    return Notification.objects.filter(user=user, read_at__isnull=True).count()


def mark_all_read(user):
    return Notification.objects.filter(user=user, read_at__isnull=True).update(
        read_at=timezone.now()
    )
