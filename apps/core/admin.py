"""Shared admin helpers reused by every ModelAdmin."""
import csv

from django.contrib import admin
from django.http import HttpResponse
from django.utils import timezone
from django.utils.html import format_html


class ExportCsvMixin:
    """Adds a "Export selected to CSV" action to any ModelAdmin.

    Columns come from ``csv_fields`` when set, otherwise from the model's
    concrete fields, so a new model gets a working export for free.
    """

    csv_fields = None

    def get_csv_fields(self):
        if self.csv_fields:
            return list(self.csv_fields)
        return [f.name for f in self.model._meta.fields]

    @admin.action(description="Export selected to CSV")
    def export_as_csv(self, request, queryset):
        field_names = self.get_csv_fields()
        filename = (
            f"{self.model._meta.model_name}-"
            f"{timezone.localtime().strftime('%Y%m%d-%H%M')}.csv"
        )

        response = HttpResponse(content_type="text/csv")
        response["Content-Disposition"] = f'attachment; filename="{filename}"'

        writer = csv.writer(response)
        writer.writerow([name.replace("_", " ").title() for name in field_names])
        for obj in queryset:
            row = []
            for name in field_names:
                value = getattr(obj, name, "")
                if callable(value):
                    value = value()
                row.append("" if value is None else str(value))
            writer.writerow(row)

        self.message_user(request, f"Exported {queryset.count()} row(s).")
        return response


class ReadOnlyTimestampsMixin:
    """Keeps auto timestamps visible but uneditable in the change form."""

    def get_readonly_fields(self, request, obj=None):
        base = list(super().get_readonly_fields(request, obj))
        for field in ("created_at", "updated_at"):
            if hasattr(self.model, field) and field not in base:
                base.append(field)
        return base


def badge(text, color="secondary"):
    """Render a Bootstrap-ish coloured pill inside an admin list column."""
    palette = {
        "success": "#198754",
        "danger": "#dc3545",
        "warning": "#fd7e14",
        "info": "#0dcaf0",
        "primary": "#0d6efd",
        "secondary": "#6c757d",
        "dark": "#212529",
    }
    return format_html(
        '<span style="display:inline-block;padding:2px 9px;border-radius:10px;'
        'background:{};color:#fff;font-size:11px;font-weight:600;white-space:nowrap">{}</span>',
        palette.get(color, palette["secondary"]),
        text,
    )


def thumbnail(image_field, size=48):
    """Small preview for image columns; a dash when there is no file."""
    if not image_field:
        return "-"
    return format_html(
        '<img src="{}" style="width:{}px;height:{}px;object-fit:cover;'
        'border-radius:6px;border:1px solid #ddd" />',
        image_field.url,
        size,
        size,
    )
