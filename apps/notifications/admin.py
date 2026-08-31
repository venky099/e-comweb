from django.contrib import admin
from django.utils.translation import gettext_lazy as _

from apps.audit.mixins import AuditedModelAdmin
from apps.notifications.models import EmailTemplate, Notification


@admin.register(EmailTemplate)
class EmailTemplateAdmin(AuditedModelAdmin, admin.ModelAdmin):
    list_display = ("name", "code", "language", "subject", "is_active")
    list_filter = ("language", "is_active", "code")
    search_fields = ("code", "name", "subject", "body")
    list_editable = ("is_active",)
    fieldsets = (
        (None, {"fields": ("code", "language", "name", "description", "is_active")}),
        (
            _("Message"),
            {
                "fields": ("subject", "body"),
                "description": _(
                    "Django template syntax is available. The order is in "
                    "{{ order }}, the customer in {{ user }}. A code with no "
                    "row here falls back to the built-in wording."
                ),
            },
        ),
    )


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = ("title", "user", "kind", "read_at", "created_at")
    list_filter = ("kind", "read_at", "created_at")
    search_fields = ("title", "body", "user__email")
    date_hierarchy = "created_at"

    def has_add_permission(self, request):
        return False
