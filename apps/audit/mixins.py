"""Automatic auditing for the Django admin.

Mix ``AuditedModelAdmin`` into a ModelAdmin and every save, delete and bulk
action through it is recorded with the values that changed. It reads the row
back from the database before saving rather than trusting the form's
``changed_data``, so a change made by a signal or a model's own save() is
captured too.
"""
from apps.audit import services
from apps.audit.models import AuditLog


class AuditedModelAdmin:
    """Records creations, edits and deletions made through the admin."""

    def save_model(self, request, obj, form, change):
        before = {}
        if change and obj.pk:
            current = self.model.objects.filter(pk=obj.pk).first()
            before = services.snapshot(current)

        super().save_model(request, obj, form, change)

        if change:
            services.record_change(obj, before, request=request)
        else:
            services.record(
                AuditLog.Action.CREATE,
                instance=obj,
                request=request,
                changes=services.diff({}, services.snapshot(obj)),
            )

    def delete_model(self, request, obj):
        # Snapshot first: after the delete there is nothing left to describe.
        details = services.snapshot(obj)
        label = str(obj)[:255]
        model_label = f"{obj._meta.app_label}.{obj._meta.object_name}"
        object_id = str(obj.pk)

        super().delete_model(request, obj)

        services.record(
            AuditLog.Action.DELETE,
            request=request,
            model_label=model_label,
            object_id=object_id,
            object_label=label,
            changes={k: {"from": v, "to": None} for k, v in details.items()},
        )

    def delete_queryset(self, request, queryset):
        recorded = [
            (
                f"{obj._meta.app_label}.{obj._meta.object_name}",
                str(obj.pk),
                str(obj)[:255],
                services.snapshot(obj),
            )
            for obj in queryset
        ]

        super().delete_queryset(request, queryset)

        for model_label, object_id, label, details in recorded:
            services.record(
                AuditLog.Action.DELETE,
                request=request,
                model_label=model_label,
                object_id=object_id,
                object_label=label,
                changes={k: {"from": v, "to": None} for k, v in details.items()},
            )
