"""Coupon administration."""
from django.contrib import admin
from django.db.models import Sum
from django.utils.translation import gettext_lazy as _

from apps.core.admin import ExportCsvMixin, badge

from .models import Coupon, CouponUsage


class CouponUsageInline(admin.TabularInline):
    model = CouponUsage
    extra = 0
    can_delete = False
    fields = ("user", "order", "discount_amount", "created_at")
    readonly_fields = ("user", "order", "discount_amount", "created_at")

    def has_add_permission(self, request, obj=None):
        return False


class CouponStateFilter(admin.SimpleListFilter):
    title = _("state")
    parameter_name = "state"

    def lookups(self, request, model_admin):
        return (
            ("live", _("Live now")),
            ("scheduled", _("Scheduled")),
            ("expired", _("Expired")),
            ("exhausted", _("Usage limit reached")),
        )

    def queryset(self, request, queryset):
        from django.db.models import F, Q
        from django.utils import timezone

        now = timezone.now()
        value = self.value()
        if value == "live":
            return queryset.live()
        if value == "scheduled":
            return queryset.filter(valid_from__gt=now)
        if value == "expired":
            return queryset.filter(valid_to__lt=now)
        if value == "exhausted":
            return queryset.filter(
                usage_limit__isnull=False, used_count__gte=F("usage_limit")
            )
        return queryset


@admin.register(Coupon)
class CouponAdmin(ExportCsvMixin, admin.ModelAdmin):
    inlines = [CouponUsageInline]
    list_display = (
        "code",
        "discount_column",
        "min_order_value",
        "usage_column",
        "total_discount_given",
        "valid_from",
        "valid_to",
        "state_badge",
        "is_active",
        "is_public",
    )
    list_display_links = ("code",)
    list_editable = ("is_active", "is_public")
    list_filter = (CouponStateFilter, "discount_type", "is_active", "is_public", "first_order_only")
    search_fields = ("code", "description")
    filter_horizontal = ("applicable_categories", "applicable_products")
    readonly_fields = ("used_count", "created_at", "updated_at")
    date_hierarchy = "valid_from"
    actions = ["activate", "deactivate", "export_as_csv"]
    csv_fields = ("id", "code", "discount_type", "value", "used_count", "valid_from", "valid_to")

    fieldsets = (
        (None, {"fields": ("code", "description", "is_active", "is_public")}),
        (
            _("Discount"),
            {"fields": ("discount_type", "value", "max_discount_amount", "min_order_value")},
        ),
        (_("Validity"), {"fields": ("valid_from", "valid_to")}),
        (
            _("Usage limits"),
            {"fields": ("usage_limit", "usage_limit_per_user", "first_order_only", "used_count")},
        ),
        (
            _("Restrict to"),
            {
                "fields": ("applicable_categories", "applicable_products"),
                "classes": ("collapse",),
                "description": _("Leave both empty to apply the coupon store-wide."),
            },
        ),
        (_("Timestamps"), {"fields": ("created_at", "updated_at"), "classes": ("collapse",)}),
    )

    def get_queryset(self, request):
        return super().get_queryset(request).annotate(
            _total_discount=Sum("usages__discount_amount")
        )

    @admin.display(description=_("Discount"))
    def discount_column(self, obj):
        return obj.discount_label

    @admin.display(description=_("Used"))
    def usage_column(self, obj):
        if obj.usage_limit is None:
            return f"{obj.used_count} / unlimited"
        return f"{obj.used_count} / {obj.usage_limit}"

    @admin.display(description=_("Discount given"), ordering="_total_discount")
    def total_discount_given(self, obj):
        return f"{obj._total_discount or 0:,.2f}"

    @admin.display(description=_("State"))
    def state_badge(self, obj):
        if not obj.is_active:
            return badge("Disabled", "secondary")
        if obj.is_expired:
            return badge("Expired", "danger")
        if obj.is_exhausted:
            return badge("Exhausted", "warning")
        return badge("Live", "success")

    @admin.action(description=_("Activate selected coupons"))
    def activate(self, request, queryset):
        self.message_user(request, f"{queryset.update(is_active=True)} coupon(s) activated.")

    @admin.action(description=_("Deactivate selected coupons"))
    def deactivate(self, request, queryset):
        self.message_user(request, f"{queryset.update(is_active=False)} coupon(s) deactivated.")


@admin.register(CouponUsage)
class CouponUsageAdmin(ExportCsvMixin, admin.ModelAdmin):
    list_display = ("coupon", "user", "order", "discount_amount", "created_at")
    list_filter = ("coupon", "created_at")
    search_fields = ("coupon__code", "user__email", "order__order_number")
    autocomplete_fields = ("coupon", "user", "order")
    list_select_related = ("coupon", "user", "order")
    date_hierarchy = "created_at"
    actions = ["export_as_csv"]

    def has_add_permission(self, request):
        return False
