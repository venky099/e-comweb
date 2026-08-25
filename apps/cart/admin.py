"""Cart administration -- mostly for support staff investigating a basket."""
from django.contrib import admin
from django.db.models import Count, Sum
from django.utils.translation import gettext_lazy as _

from apps.core.admin import ExportCsvMixin, badge

from .models import Cart, CartItem


class CartItemInline(admin.TabularInline):
    model = CartItem
    extra = 0
    fields = ("variant", "quantity", "unit_price_column", "line_total_column", "stock_column")
    readonly_fields = ("unit_price_column", "line_total_column", "stock_column")
    autocomplete_fields = ("variant",)

    @admin.display(description=_("Unit price"))
    def unit_price_column(self, obj):
        return f"{obj.unit_price:.2f}" if obj.pk else "-"

    @admin.display(description=_("Line total"))
    def line_total_column(self, obj):
        return f"{obj.line_total:.2f}" if obj.pk else "-"

    @admin.display(description=_("Stock"))
    def stock_column(self, obj):
        if not obj.pk:
            return "-"
        available = obj.available_quantity
        colour = "danger" if available < obj.quantity else "success"
        return badge(f"{available} available", colour)


@admin.register(Cart)
class CartAdmin(ExportCsvMixin, admin.ModelAdmin):
    inlines = [CartItemInline]
    list_display = (
        "id",
        "owner_column",
        "line_count",
        "unit_count",
        "subtotal_column",
        "coupon",
        "is_active",
        "updated_at",
    )
    list_display_links = ("id", "owner_column")
    list_filter = ("is_active", "created_at", "updated_at")
    search_fields = ("user__email", "user__phone", "session_key", "coupon__code")
    autocomplete_fields = ("user", "coupon")
    readonly_fields = ("session_key", "created_at", "updated_at")
    date_hierarchy = "updated_at"
    list_per_page = 40
    actions = ["export_as_csv"]

    def get_queryset(self, request):
        return (
            super()
            .get_queryset(request)
            .select_related("user", "coupon")
            .annotate(_lines=Count("items", distinct=True), _units=Sum("items__quantity"))
        )

    @admin.display(description=_("Owner"))
    def owner_column(self, obj):
        if obj.user:
            return obj.user.get_display_name()
        return f"Guest ({obj.session_key[:10]}...)" if obj.session_key else "Guest"

    @admin.display(description=_("Lines"), ordering="_lines")
    def line_count(self, obj):
        return obj._lines or 0

    @admin.display(description=_("Units"), ordering="_units")
    def unit_count(self, obj):
        return obj._units or 0

    @admin.display(description=_("Subtotal"))
    def subtotal_column(self, obj):
        return f"{obj.subtotal:.2f}"


@admin.register(CartItem)
class CartItemAdmin(admin.ModelAdmin):
    list_display = ("cart", "variant", "quantity", "line_total_column", "created_at")
    list_filter = ("created_at",)
    search_fields = ("variant__sku", "variant__product__name", "cart__user__email")
    autocomplete_fields = ("cart", "variant")
    list_select_related = ("cart__user", "variant__product")

    @admin.display(description=_("Line total"))
    def line_total_column(self, obj):
        return f"{obj.line_total:.2f}"
