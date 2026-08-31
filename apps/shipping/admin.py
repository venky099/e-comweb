from django.contrib import admin
from django.utils.translation import gettext_lazy as _

from apps.shipping.models import (
    Shipment,
    ShipmentItem,
    ShippingMethod,
    ShippingRate,
    ShippingZone,
    TrackingEvent,
)


class ShippingRateInline(admin.TabularInline):
    model = ShippingRate
    extra = 0
    fields = (
        "method",
        "min_weight_grams",
        "max_weight_grams",
        "min_order_value",
        "max_order_value",
        "price",
        "free_over",
        "is_active",
    )
    autocomplete_fields = ("method",)


@admin.register(ShippingZone)
class ShippingZoneAdmin(admin.ModelAdmin):
    list_display = ("name", "country_list", "rate_count", "is_active", "sort_order")
    list_filter = ("is_active",)
    search_fields = ("name", "countries__name")
    filter_horizontal = ("countries",)
    list_editable = ("is_active", "sort_order")
    inlines = [ShippingRateInline]

    @admin.display(description=_("Countries"))
    def country_list(self, obj):
        names = list(obj.countries.values_list("name", flat=True)[:4])
        extra = obj.countries.count() - len(names)
        return ", ".join(names) + (f" +{extra}" if extra > 0 else "")

    @admin.display(description=_("Rates"))
    def rate_count(self, obj):
        return obj.rates.count()


@admin.register(ShippingMethod)
class ShippingMethodAdmin(admin.ModelAdmin):
    list_display = ("name", "code", "carrier", "estimate", "is_active", "sort_order")
    list_filter = ("is_active",)
    search_fields = ("name", "code", "carrier")
    prepopulated_fields = {"code": ("name",)}
    list_editable = ("is_active", "sort_order")


@admin.register(ShippingRate)
class ShippingRateAdmin(admin.ModelAdmin):
    list_display = (
        "zone",
        "method",
        "min_weight_grams",
        "max_weight_grams",
        "min_order_value",
        "max_order_value",
        "price",
        "free_over",
        "is_active",
    )
    list_filter = ("zone", "method", "is_active")
    autocomplete_fields = ("zone", "method")
    list_editable = ("price", "free_over", "is_active")


class ShipmentItemInline(admin.TabularInline):
    model = ShipmentItem
    extra = 0
    autocomplete_fields = ("order_item",)


class TrackingEventInline(admin.TabularInline):
    """Tracking is history: rows are added, never edited."""

    model = TrackingEvent
    extra = 0
    fields = ("status", "description", "location", "occurred_at")
    readonly_fields = ("occurred_at",)
    ordering = ("-occurred_at",)


@admin.register(Shipment)
class ShipmentAdmin(admin.ModelAdmin):
    list_display = (
        "number",
        "order",
        "carrier",
        "tracking_number",
        "status",
        "weight_grams",
        "dispatched_at",
        "delivered_at",
    )
    list_filter = ("status", "carrier", "method")
    search_fields = ("number", "tracking_number", "order__order_number")
    date_hierarchy = "created_at"
    readonly_fields = ("number", "declared_value", "weight_grams")
    inlines = [ShipmentItemInline, TrackingEventInline]
    fieldsets = (
        (None, {"fields": ("number", "order", "status", "note")}),
        (
            _("Carrier"),
            {"fields": ("method", "carrier", "tracking_number", "tracking_url")},
        ),
        (
            _("Parcel"),
            {"fields": ("weight_grams", "length_mm", "width_mm", "height_mm")},
        ),
        (
            _("Customs"),
            {
                "fields": ("contents_description", "declared_value", "hs_code"),
                "description": _("Used on the commercial invoice for international parcels."),
            },
        ),
        (_("Timeline"), {"fields": ("dispatched_at", "delivered_at")}),
    )

    def has_add_permission(self, request):
        # Parcels are created from an order, so that stock and quantities are
        # checked. Adding one here would bypass those checks.
        return False


@admin.register(TrackingEvent)
class TrackingEventAdmin(admin.ModelAdmin):
    list_display = ("shipment", "status", "location", "occurred_at")
    list_filter = ("status",)
    search_fields = ("shipment__number", "shipment__tracking_number", "description")
    date_hierarchy = "occurred_at"

    def has_change_permission(self, request, obj=None):
        return False
