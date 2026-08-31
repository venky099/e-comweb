from django.contrib import admin
from django.utils.translation import gettext_lazy as _

from apps.shipping.models import ShippingMethod, ShippingRate, ShippingZone


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
