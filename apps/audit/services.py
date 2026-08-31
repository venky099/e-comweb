"""Recording changes.

Two entry points:

    record(...)          an explicit call, for service-layer changes
    AuditedModelAdmin    a mixin that captures admin edits automatically

Both funnel into one writer, so an admin edit and an API change of the same
field look the same in the log.

Nothing here is allowed to raise into the caller. A failed audit write must
not roll back the change it was describing -- losing the record is bad, and
losing the customer's order because we could not describe it is worse. The
failure is logged loudly instead.
"""
import logging
from decimal import Decimal, InvalidOperation

from django.utils import timezone

from apps.audit.models import AuditLog

logger = logging.getLogger("ecommerce")

# Never copy these into the log, whatever model they appear on.
SENSITIVE_FIELDS = {
    "password",
    "token",
    "secret",
    "api_key",
    "signature",
    "webhook_secret",
    "card_number",
    "cvv",
}


def _readable(value, field=None):
    """A JSON-safe, canonical rendering of a field value.

    Decimals are quantized to the column's own precision first. An unsaved
    Decimal("1250.0000") and the Decimal("1250.00") the database returns are
    the same money but different strings, and comparing the strings makes
    every save look like a price change. An audit log full of changes that
    did not happen is worse than no audit log.
    """
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, Decimal):
        places = getattr(field, "decimal_places", None)
        if places is not None:
            try:
                value = value.quantize(Decimal(1).scaleb(-places))
            except (InvalidOperation, ValueError):
                pass
        return str(value)
    if isinstance(value, (str, int, float)):
        return value
    return str(value)


def _is_sensitive(field_name):
    lowered = field_name.lower()
    return any(marker in lowered for marker in SENSITIVE_FIELDS)


def snapshot(instance, fields=None):
    """The current values of ``instance``, for diffing later."""
    if instance is None or instance.pk is None:
        return {}
    columns = {
        f.name: f
        for f in instance._meta.concrete_fields
        if f.name not in {"created_at", "updated_at"}
    }
    names = fields or list(columns)

    data = {}
    for name in names:
        if _is_sensitive(name):
            continue
        try:
            data[name] = _readable(getattr(instance, name, None), columns.get(name))
        except Exception:
            # A property that raises must not stop the audit from happening.
            continue
    return data


def diff(before, after):
    """Fields whose value moved, as ``{field: {"from": x, "to": y}}``."""
    changes = {}
    for field, new_value in (after or {}).items():
        old_value = (before or {}).get(field)
        if old_value != new_value:
            changes[field] = {"from": old_value, "to": new_value}
    return changes


def client_details(request):
    if request is None:
        return {}
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
    ip = forwarded.split(",")[0].strip() if forwarded else request.META.get("REMOTE_ADDR")
    return {
        "ip_address": ip or None,
        "user_agent": (request.META.get("HTTP_USER_AGENT") or "")[:255],
    }


def record(
    action,
    instance=None,
    actor=None,
    changes=None,
    summary="",
    request=None,
    model_label="",
    object_id="",
    object_label="",
):
    """Write one audit entry. Never raises."""
    try:
        if instance is not None:
            model_label = model_label or f"{instance._meta.app_label}.{instance._meta.object_name}"
            object_id = object_id or str(instance.pk or "")
            object_label = object_label or str(instance)[:255]

        if actor is None and request is not None:
            candidate = getattr(request, "user", None)
            if candidate is not None and candidate.is_authenticated:
                actor = candidate

        return AuditLog.objects.create(
            actor=actor if getattr(actor, "pk", None) else None,
            actor_label=(str(actor) if actor else "")[:150],
            action=action,
            model_label=model_label[:100],
            object_id=str(object_id)[:64],
            object_label=object_label[:255],
            changes=changes or {},
            summary=summary[:255],
            **client_details(request),
        )
    except Exception:
        logger.exception("Could not write an audit entry for %s", model_label or instance)
        return None


def record_change(instance, before, actor=None, request=None, summary=""):
    """Record an update, skipping the write when nothing actually moved."""
    changes = diff(before, snapshot(instance))
    if not changes:
        return None
    return record(
        AuditLog.Action.UPDATE,
        instance=instance,
        actor=actor,
        changes=changes,
        summary=summary,
        request=request,
    )


def record_login(user, request=None, successful=True, identifier=""):
    return record(
        AuditLog.Action.LOGIN if successful else AuditLog.Action.LOGIN_FAILED,
        actor=user if successful else None,
        request=request,
        summary=(
            f"Signed in at {timezone.now():%Y-%m-%d %H:%M}"
            if successful
            else f"Failed sign-in for {identifier}"[:255]
        ),
        model_label="accounts.User",
        object_id=str(getattr(user, "pk", "") or ""),
        object_label=str(user) if user else identifier,
    )
