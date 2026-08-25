"""Payment administration."""
from django.contrib import admin
from django.urls import reverse
from django.utils.html import format_html
from django.utils.translation import gettext_lazy as _

from apps.core.admin import ExportCsvMixin, badge

from .models import Payment, Refund, WebhookEvent


class RefundInline(admin.TabularInline):
    model = Refund
    extra = 0
    fields = ("amount", "status", "reason", "gateway_refund_id", "processed_at")
    readonly_fields = ("gateway_refund_id", "processed_at")


@admin.register(Payment)
class PaymentAdmin(ExportCsvMixin, admin.ModelAdmin):
    """Payments are written by the gateway layer; this view is read-mostly."""

    inlines = [RefundInline]
    list_display = (
        "created_at",
        "order_link",
        "gateway",
        "method",
        "amount",
        "status_badge",
        "gateway_payment_id",
        "paid_at",
    )
    list_filter = ("gateway", "status", "created_at")
    search_fields = (
        "order__order_number",
        "gateway_order_id",
        "gateway_payment_id",
        "order__email",
    )
    date_hierarchy = "created_at"
    list_select_related = ("order",)
    list_per_page = 50
    actions = ["export_as_csv"]
    readonly_fields = (
        "order",
        "gateway",
        "amount",
        "currency",
        "gateway_order_id",
        "gateway_payment_id",
        "gateway_signature",
        "raw_response",
        "paid_at",
        "created_at",
        "updated_at",
    )
    csv_fields = ("id", "gateway", "amount", "status", "gateway_payment_id", "paid_at")

    def has_add_permission(self, request):
        # Payments are only ever created through a gateway call.
        return False

    @admin.display(description=_("Order"), ordering="order__order_number")
    def order_link(self, obj):
        url = reverse("admin:orders_order_change", args=[obj.order_id])
        return format_html('<a href="{}">{}</a>', url, obj.order.order_number)

    @admin.display(description=_("Status"))
    def status_badge(self, obj):
        colour = {
            Payment.Status.CAPTURED: "success",
            Payment.Status.AUTHORIZED: "info",
            Payment.Status.PENDING: "warning",
            Payment.Status.CREATED: "secondary",
            Payment.Status.FAILED: "danger",
            Payment.Status.CANCELLED: "secondary",
            Payment.Status.REFUNDED: "dark",
            Payment.Status.PARTIALLY_REFUNDED: "warning",
        }.get(obj.status, "secondary")
        return badge(obj.get_status_display(), colour)


@admin.register(Refund)
class RefundAdmin(ExportCsvMixin, admin.ModelAdmin):
    list_display = ("created_at", "payment", "amount", "status_badge", "gateway_refund_id")
    list_filter = ("status", "created_at")
    search_fields = ("payment__order__order_number", "gateway_refund_id", "reason")
    date_hierarchy = "created_at"
    list_select_related = ("payment__order",)
    actions = ["export_as_csv"]

    @admin.display(description=_("Status"))
    def status_badge(self, obj):
        colour = {
            Refund.Status.PROCESSED: "success",
            Refund.Status.PENDING: "warning",
            Refund.Status.FAILED: "danger",
        }.get(obj.status, "secondary")
        return badge(obj.get_status_display(), colour)


@admin.register(WebhookEvent)
class WebhookEventAdmin(admin.ModelAdmin):
    """Raw gateway callbacks -- the first place to look when a payment 'vanished'."""

    list_display = (
        "created_at",
        "gateway",
        "event_type",
        "event_id",
        "verified_badge",
        "processed_badge",
    )
    list_filter = ("gateway", "processed", "signature_verified", "created_at")
    search_fields = ("event_id", "event_type", "processing_error")
    date_hierarchy = "created_at"
    readonly_fields = (
        "gateway",
        "event_id",
        "event_type",
        "payload",
        "signature_verified",
        "processed",
        "processing_error",
        "created_at",
        "updated_at",
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    @admin.display(description=_("Signature"))
    def verified_badge(self, obj):
        return badge("Verified", "success") if obj.signature_verified else badge("Unverified", "danger")

    @admin.display(description=_("Processed"))
    def processed_badge(self, obj):
        if obj.processing_error:
            return badge("Error", "danger")
        return badge("Yes", "success") if obj.processed else badge("Pending", "warning")
