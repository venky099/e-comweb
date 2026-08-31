from django.contrib import admin, messages
from django.utils.translation import gettext_lazy as _

from apps.audit.mixins import AuditedModelAdmin
from apps.geo import services
from apps.geo.models import Country, Currency, ExchangeRate, State


@admin.register(Currency)
class CurrencyAdmin(AuditedModelAdmin, admin.ModelAdmin):
    list_display = (
        "code",
        "name",
        "symbol",
        "decimal_places",
        "rounding",
        "is_base",
        "is_active",
        "latest_rate",
        "sort_order",
    )
    list_filter = ("is_active", "is_base", "rounding")
    search_fields = ("code", "name")
    list_editable = ("is_active", "sort_order")
    ordering = ("sort_order", "code")

    @admin.display(description=_("Latest rate"))
    def latest_rate(self, obj):
        if obj.is_base:
            return _("base")
        row = (
            ExchangeRate.objects.filter(quote=obj).order_by("-effective_from", "-id").first()
        )
        return row.rate if row else _("none recorded")


class StateInline(admin.TabularInline):
    model = State
    extra = 0
    fields = ("name", "code", "is_active")


@admin.register(Country)
class CountryAdmin(AuditedModelAdmin, admin.ModelAdmin):
    list_display = (
        "name",
        "iso2",
        "currency",
        "dial_code",
        "is_active",
        "shipping_enabled",
        "sort_order",
    )
    list_filter = ("is_active", "shipping_enabled", "currency")
    search_fields = ("name", "iso2", "iso3")
    list_editable = ("is_active", "shipping_enabled", "sort_order")
    autocomplete_fields = ("currency",)
    inlines = [StateInline]
    ordering = ("sort_order", "name")


@admin.register(State)
class StateAdmin(admin.ModelAdmin):
    list_display = ("name", "code", "country", "is_active")
    list_filter = ("country", "is_active")
    search_fields = ("name", "code")
    autocomplete_fields = ("country",)


@admin.register(ExchangeRate)
class ExchangeRateAdmin(admin.ModelAdmin):
    """Rates are history, so rows are added and never edited.

    Correcting a rate means recording a new one; editing the old row would
    silently change what past orders appear to have been charged at.
    """

    list_display = ("quote", "rate", "base", "source", "effective_from", "note")
    list_filter = ("source", "quote")
    date_hierarchy = "effective_from"
    autocomplete_fields = ("base", "quote")
    readonly_fields = ("created_at", "updated_at")

    def has_change_permission(self, request, obj=None):
        return False

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        messages.info(
            request,
            _("Rate recorded. Prices in %(code)s update immediately.")
            % {"code": obj.quote.code},
        )
