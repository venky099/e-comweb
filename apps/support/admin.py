from django.contrib import admin
from django.utils.translation import gettext_lazy as _

from apps.support.models import Ticket, TicketMessage


class TicketMessageInline(admin.TabularInline):
    model = TicketMessage
    extra = 1
    fields = ("author", "body", "is_staff_reply", "is_internal_note", "attachment")
    readonly_fields = ("is_staff_reply",)
    autocomplete_fields = ("author",)


@admin.register(Ticket)
class TicketAdmin(admin.ModelAdmin):
    list_display = (
        "reference",
        "subject",
        "user",
        "topic",
        "status",
        "priority",
        "assigned_to",
        "last_reply_at",
    )
    list_filter = ("status", "priority", "topic", "assigned_to")
    search_fields = ("reference", "subject", "user__email", "messages__body")
    date_hierarchy = "created_at"
    autocomplete_fields = ("user", "order", "assigned_to")
    list_editable = ("status", "priority", "assigned_to")
    readonly_fields = ("reference", "last_reply_at", "resolved_at")
    inlines = [TicketMessageInline]

    def has_add_permission(self, request):
        # Tickets start with a customer's message, so they are opened from
        # the storefront rather than here.
        return False

    @admin.display(description=_("Messages"))
    def message_count(self, obj):
        return obj.messages.count()


@admin.register(TicketMessage)
class TicketMessageAdmin(admin.ModelAdmin):
    list_display = ("ticket", "author_label", "is_staff_reply", "is_internal_note", "created_at")
    list_filter = ("is_staff_reply", "is_internal_note")
    search_fields = ("ticket__reference", "body", "author_label")
