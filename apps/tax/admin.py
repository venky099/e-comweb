from django.contrib import admin
from django.utils.translation import gettext_lazy as _

from apps.tax.models import OrderTaxLine, TaxRule


@admin.register(TaxRule)
class TaxRuleAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "percent",
        "country",
        "state",
        "category",
        "applies_when",
        "effective_from",
        "effective_to",
        "is_active",
    )
    list_filter = ("country", "applies_when", "is_active")
    search_fields = ("name", "note", "country__name")
    autocomplete_fields = ("country", "state", "category")
    list_editable = ("is_active",)
    date_hierarchy = "effective_from"
    fieldsets = (
        (None, {"fields": ("name", "percent", "note", "is_active")}),
        (
            _("Where it applies"),
            {
                "fields": ("country", "state", "category", "applies_when"),
                "description": _(
                    "Leave state or category empty to apply more broadly. The "
                    "most specific matching rule wins."
                ),
            },
        ),
        (_("When it applies"), {"fields": ("effective_from", "effective_to")}),
    )


@admin.register(OrderTaxLine)
class OrderTaxLineAdmin(admin.ModelAdmin):
    """What an order was actually charged. Read-only: it is history."""

    list_display = ("order", "name", "percent", "amount")
    list_filter = ("name",)
    search_fields = ("order__order_number",)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
