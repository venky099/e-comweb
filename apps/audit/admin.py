from django.contrib import admin
from django.utils.html import format_html
from django.utils.translation import gettext_lazy as _

from apps.audit.models import AuditLog


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    """Read-only by construction.

    An audit log that can be edited answers nothing, so there is no add
    form, no change form and no delete action -- not even for a superuser.
    """

    list_display = ("created_at", "actor_label", "action", "object_label", "model_label", "what_changed")
    list_filter = ("action", "model_label", "created_at")
    search_fields = ("actor_label", "object_label", "object_id", "summary", "model_label")
    date_hierarchy = "created_at"
    readonly_fields = [f.name for f in AuditLog._meta.fields] + ["what_changed"]

    @admin.display(description=_("Changes"))
    def what_changed(self, obj):
        lines = obj.describe()
        if not lines:
            return obj.summary or "\u2014"
        return format_html("<br>".join(str(line) for line in lines[:6]))

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
