"""Order administration -- the staff order-management screen."""
from django.contrib import admin, messages
from django.urls import reverse
from django.utils.html import format_html, format_html_join
from django.utils.translation import gettext_lazy as _

from apps.audit.mixins import AuditedModelAdmin
from apps.core.admin import ExportCsvMixin, badge
from apps.payments.models import Payment

from . import services
from .models import Order, OrderItem, OrderStatusHistory, ReturnRequest


class OrderItemInline(admin.TabularInline):
    """Purchased lines. Snapshots are read-only: an order is a receipt."""

    model = OrderItem
    extra = 0
    can_delete = False
    fields = (
        "thumb",
        "product_name",
        "variant_label",
        "sku",
        "unit_price",
        "quantity",
        "line_total",
        "status",
    )
    readonly_fields = (
        "thumb",
        "product_name",
        "variant_label",
        "sku",
        "unit_price",
        "quantity",
        "line_total",
    )

    def has_add_permission(self, request, obj=None):
        return False

    @admin.display(description=_("Image"))
    def thumb(self, obj):
        if not obj.image_url:
            return "-"
        return format_html(
            '<img src="{}" style="width:44px;height:44px;object-fit:cover;border-radius:6px" />',
            obj.image_url,
        )


class OrderStatusHistoryInline(admin.TabularInline):
    model = OrderStatusHistory
    extra = 0
    fields = ("created_at", "status", "note", "changed_by")
    readonly_fields = ("created_at", "status", "note", "changed_by")
    ordering = ("created_at",)

    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False


class ReturnRequestInline(admin.TabularInline):
    model = ReturnRequest
    extra = 0
    fields = ("order_item", "quantity", "reason", "status", "refund_amount", "staff_note")
    readonly_fields = ("order_item", "quantity", "reason", "refund_amount")
    show_change_link = True

    def has_add_permission(self, request, obj=None):
        return False


class PaymentInline(admin.TabularInline):
    model = Payment
    extra = 0
    fields = ("gateway", "method", "amount", "status", "gateway_payment_id", "paid_at")
    readonly_fields = fields

    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(Order)
class OrderAdmin(AuditedModelAdmin, ExportCsvMixin, admin.ModelAdmin):
    """The order desk.

    Status changes go through ``services.transition_order`` (via the bulk
    actions) so the allowed-transition rules and the audit log apply here
    exactly as they do on the storefront.
    """

    inlines = [OrderItemInline, PaymentInline, ReturnRequestInline, OrderStatusHistoryInline]
    list_display = (
        "order_number",
        "placed_at",
        "customer_column",
        "item_count_column",
        "total_amount",
        "payment_method",
        "payment_badge",
        "status_badge",
        "tracking_number",
    )
    list_display_links = ("order_number",)
    list_filter = (
        "status",
        "payment_status",
        "payment_method",
        "placed_at",
        "shipping_state",
    )
    search_fields = (
        "order_number",
        "email",
        "phone",
        "shipping_full_name",
        "shipping_phone",
        "shipping_postal_code",
        "tracking_number",
        "user__email",
        "items__product_name",
        "items__sku",
    )
    date_hierarchy = "placed_at"
    list_select_related = ("user",)
    list_per_page = 30
    save_on_top = True
    autocomplete_fields = ("user", "coupon")
    actions = [
        "mark_confirmed",
        "mark_processing",
        "mark_shipped",
        "mark_delivered",
        "cancel_orders",
        "export_as_csv",
    ]
    csv_fields = (
        "order_number",
        "placed_at",
        "email",
        "status",
        "payment_status",
        "payment_method",
        "subtotal",
        "coupon_discount",
        "delivery_charge",
        "total_amount",
    )

    readonly_fields = (
        "order_number",
        "subtotal",
        "product_discount",
        "coupon_discount",
        "total_amount",
        "refunded_amount",
        "placed_at",
        "confirmed_at",
        "shipped_at",
        "delivered_at",
        "cancelled_at",
        "returned_at",
        "created_at",
        "updated_at",
        "totals_breakdown",
    )

    fieldsets = (
        (None, {"fields": ("order_number", "user", "email", "phone", "placed_at")}),
        (_("Status"), {"fields": ("status", "payment_status", "payment_method")}),
        (
            _("Amounts"),
            {
                "fields": (
                    "totals_breakdown",
                    ("subtotal", "product_discount"),
                    ("coupon", "coupon_code", "coupon_discount"),
                    ("delivery_charge", "tax_amount"),
                    ("total_amount", "refunded_amount"),
                    "currency",
                )
            },
        ),
        (
            _("Shipping address"),
            {
                "fields": (
                    "shipping_full_name",
                    "shipping_phone",
                    "shipping_line1",
                    "shipping_line2",
                    "shipping_landmark",
                    ("shipping_city", "shipping_state"),
                    ("shipping_postal_code", "shipping_country"),
                )
            },
        ),
        (
            _("Fulfilment"),
            {"fields": ("tracking_number", "courier_name", "estimated_delivery", "staff_note")},
        ),
        (
            _("Customer input"),
            {"fields": ("customer_note", "cancel_reason"), "classes": ("collapse",)},
        ),
        (
            _("Timeline"),
            {
                "fields": (
                    "confirmed_at",
                    "shipped_at",
                    "delivered_at",
                    "cancelled_at",
                    "returned_at",
                    "created_at",
                    "updated_at",
                ),
                "classes": ("collapse",),
            },
        ),
    )

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("user").prefetch_related("items")

    @admin.display(description=_("Customer"), ordering="email")
    def customer_column(self, obj):
        if obj.user_id:
            url = reverse("admin:accounts_user_change", args=[obj.user_id])
            return format_html('<a href="{}">{}</a>', url, obj.user.get_display_name())
        return obj.email

    @admin.display(description=_("Items"))
    def item_count_column(self, obj):
        return obj.item_count

    @admin.display(description=_("Status"), ordering="status")
    def status_badge(self, obj):
        return badge(obj.get_status_display(), obj.status_badge)

    @admin.display(description=_("Payment"), ordering="payment_status")
    def payment_badge(self, obj):
        colour = {
            Order.PaymentStatus.PAID: "success",
            Order.PaymentStatus.PENDING: "warning",
            Order.PaymentStatus.FAILED: "danger",
            Order.PaymentStatus.REFUND_PENDING: "warning",
            Order.PaymentStatus.REFUNDED: "dark",
            Order.PaymentStatus.PARTIALLY_REFUNDED: "info",
        }.get(obj.payment_status, "secondary")
        return badge(obj.get_payment_status_display(), colour)

    @admin.display(description=_("Breakdown"))
    def totals_breakdown(self, obj):
        """Human-readable invoice maths, so nobody edits totals by hand."""
        rows = [
            (_("Subtotal"), obj.subtotal),
            (_("Product discount"), -obj.product_discount),
            (_("Coupon discount"), -obj.coupon_discount),
            (_("Delivery"), obj.delivery_charge),
            (_("Tax"), obj.tax_amount),
            (_("Total"), obj.total_amount),
        ]
        body = format_html_join(
            "",
            "<tr><td style=\"padding:2px 14px 2px 0\">{}</td>"
            "<td style=\"text-align:right;font-variant-numeric:tabular-nums\">{}</td></tr>",
            ((label, f"{value:,.2f}") for label, value in rows),
        )
        return format_html("<table>{}</table>", body)

    # ---- bulk status actions -------------------------------------------
    def _bulk_transition(self, request, queryset, new_status, label):
        moved, blocked = 0, 0
        for order in queryset:
            try:
                services.transition_order(
                    order, new_status, user=request.user, note=f"Bulk: {label}"
                )
                moved += 1
            except services.OrderError:
                blocked += 1
        if moved:
            self.message_user(request, _("%(n)d order(s) updated.") % {"n": moved})
        if blocked:
            self.message_user(
                request,
                _("%(n)d order(s) skipped - the transition was not allowed.")
                % {"n": blocked},
                level=messages.WARNING,
            )

    @admin.action(description=_("Mark as confirmed"))
    def mark_confirmed(self, request, queryset):
        self._bulk_transition(request, queryset, Order.Status.CONFIRMED, "confirmed")

    @admin.action(description=_("Mark as processing"))
    def mark_processing(self, request, queryset):
        self._bulk_transition(request, queryset, Order.Status.PROCESSING, "processing")

    @admin.action(description=_("Mark as shipped"))
    def mark_shipped(self, request, queryset):
        self._bulk_transition(request, queryset, Order.Status.SHIPPED, "shipped")

    @admin.action(description=_("Mark as delivered"))
    def mark_delivered(self, request, queryset):
        self._bulk_transition(request, queryset, Order.Status.DELIVERED, "delivered")

    @admin.action(description=_("Cancel selected orders (restores stock)"))
    def cancel_orders(self, request, queryset):
        cancelled, failed = 0, 0
        for order in queryset:
            try:
                services.cancel_order(
                    order,
                    user=request.user,
                    reason="Cancelled by staff",
                    staff_override=True,
                )
                cancelled += 1
            except services.OrderError:
                failed += 1
        self.message_user(request, _("%(n)d order(s) cancelled.") % {"n": cancelled})
        if failed:
            self.message_user(
                request,
                _("%(n)d order(s) could not be cancelled.") % {"n": failed},
                level=messages.WARNING,
            )


@admin.register(OrderItem)
class OrderItemAdmin(ExportCsvMixin, admin.ModelAdmin):
    """Line-level view -- the basis of the best-sellers report."""

    list_display = (
        "order_link",
        "product_name",
        "variant_label",
        "sku",
        "quantity",
        "unit_price",
        "line_total",
        "status",
    )
    list_filter = ("status", "order__status", "created_at")
    search_fields = ("product_name", "sku", "order__order_number")
    list_select_related = ("order",)
    date_hierarchy = "created_at"
    actions = ["export_as_csv"]
    readonly_fields = ("line_total", "created_at", "updated_at")

    def has_add_permission(self, request):
        return False

    @admin.display(description=_("Order"), ordering="order__order_number")
    def order_link(self, obj):
        url = reverse("admin:orders_order_change", args=[obj.order_id])
        return format_html('<a href="{}">{}</a>', url, obj.order.order_number)


@admin.register(ReturnRequest)
class ReturnRequestAdmin(ExportCsvMixin, admin.ModelAdmin):
    """Return desk. Approving a return restores stock through the service layer."""

    list_display = (
        "created_at",
        "order_link",
        "item_column",
        "quantity",
        "reason",
        "refund_amount",
        "status_badge",
        "processed_by",
    )
    list_filter = ("status", "reason", "created_at")
    search_fields = ("order__order_number", "order_item__product_name", "comment")
    date_hierarchy = "created_at"
    list_select_related = ("order", "order_item", "processed_by")
    actions = ["approve_returns", "reject_returns", "complete_returns", "export_as_csv"]
    readonly_fields = ("order", "order_item", "quantity", "refund_amount", "created_at", "updated_at")

    def has_add_permission(self, request):
        return False

    @admin.display(description=_("Order"))
    def order_link(self, obj):
        url = reverse("admin:orders_order_change", args=[obj.order_id])
        return format_html('<a href="{}">{}</a>', url, obj.order.order_number)

    @admin.display(description=_("Item"))
    def item_column(self, obj):
        return obj.order_item.display_title

    @admin.display(description=_("Status"), ordering="status")
    def status_badge(self, obj):
        return badge(obj.get_status_display(), obj.status_badge)

    @admin.action(description=_("Approve selected returns"))
    def approve_returns(self, request, queryset):
        count = 0
        for return_request in queryset.filter(status=ReturnRequest.Status.REQUESTED):
            services.process_return(
                return_request, ReturnRequest.Status.APPROVED, user=request.user
            )
            count += 1
        self.message_user(request, _("%(n)d return(s) approved.") % {"n": count})

    @admin.action(description=_("Reject selected returns"))
    def reject_returns(self, request, queryset):
        count = 0
        for return_request in queryset.exclude(
            status__in=[ReturnRequest.Status.COMPLETED, ReturnRequest.Status.REFUNDED]
        ):
            services.process_return(
                return_request, ReturnRequest.Status.REJECTED, user=request.user
            )
            count += 1
        self.message_user(request, _("%(n)d return(s) rejected.") % {"n": count})

    @admin.action(description=_("Complete selected returns (restores stock)"))
    def complete_returns(self, request, queryset):
        count = 0
        for return_request in queryset.exclude(
            status__in=[ReturnRequest.Status.REJECTED, ReturnRequest.Status.COMPLETED]
        ):
            services.process_return(
                return_request, ReturnRequest.Status.COMPLETED, user=request.user
            )
            count += 1
        self.message_user(
            request, _("%(n)d return(s) completed and stock restored.") % {"n": count}
        )


@admin.register(OrderStatusHistory)
class OrderStatusHistoryAdmin(admin.ModelAdmin):
    list_display = ("created_at", "order", "status", "note", "changed_by")
    list_filter = ("status", "created_at")
    search_fields = ("order__order_number", "note")
    date_hierarchy = "created_at"
    list_select_related = ("order", "changed_by")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
