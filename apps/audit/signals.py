"""Sign-in auditing.

Section 62 is about admin actions, but who signed in and when is the first
question asked when something unexplained appears in the log, so the events
that bracket a session are recorded too.

Failed attempts are recorded without an actor -- the credentials did not
identify anyone -- but with the identifier that was tried, which is what makes
a burst of them recognisable.
"""
from django.contrib.auth.signals import (
    user_logged_in,
    user_logged_out,
    user_login_failed,
)
from django.dispatch import receiver

from apps.audit import services
from apps.audit.models import AuditLog


@receiver(user_logged_in)
def on_login(sender, request, user, **kwargs):
    if user.is_staff:
        services.record_login(user, request=request, successful=True)


@receiver(user_logged_out)
def on_logout(sender, request, user, **kwargs):
    if user is not None and user.is_staff:
        services.record(
            AuditLog.Action.ACTION,
            actor=user,
            request=request,
            summary="Signed out",
            model_label="accounts.User",
            object_id=str(user.pk),
            object_label=str(user),
        )


@receiver(user_login_failed)
def on_login_failed(sender, credentials, request=None, **kwargs):
    # Never log the password, only which account was aimed at.
    identifier = credentials.get("username") or credentials.get("email") or ""
    services.record_login(None, request=request, successful=False, identifier=identifier)
