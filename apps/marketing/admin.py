"""Marketing administration: banners, offers and flash sales."""
from django.contrib import admin
from django.utils.translation import gettext_lazy as _

from apps.core.admin import ExportCsvMixin, badge, thumbnail

from .models import Banner, FlashSale, FlashSaleItem, Offer


class LiveStateMixin:
    """Shared 'is this running right now?' column for scheduled records."""

    @admin.display(description=_("State"))
    def live_badge(self, obj):
        if not obj.is_active:
            return badge("Disabled", "secondary")
        return badge("Live", "success") if obj.is_live else badge("Scheduled", "info")


@admin.register(Banner)
class BannerAdmin(LiveStateMixin, ExportCsvMixin, admin.ModelAdmin):
    list_display = (
        "preview",
        "title",
        "position",
        "sort_order",
        "click_count",
        "start_at",
        "end_at",
        "live_badge",
        "is_active",
    )
    list_display_links = ("title",)
    list_editable = ("sort_order", "is_active")
    list_filter = ("position", "is_active", "start_at")
    search_fields = ("title", "subtitle", "link_url")
    readonly_fields = ("click_count", "created_at", "updated_at")
    actions = ["export_as_csv"]

    fieldsets = (
        (None, {"fields": ("title", "subtitle", "position", "sort_order")}),
        (_("Media"), {"fields": ("image", "mobile_image", "background_color")}),
        (_("Link"), {"fields": ("link_url", "cta_label")}),
        (_("Schedule"), {"fields": ("is_active", "start_at", "end_at")}),
        (
            _("Stats"),
            {"fields": ("click_count", "created_at", "updated_at"), "classes": ("collapse",)},
        ),
    )

    @admin.display(description=_("Preview"))
    def preview(self, obj):
        return thumbnail(obj.image, 60)


@admin.register(Offer)
class OfferAdmin(LiveStateMixin, ExportCsvMixin, admin.ModelAdmin):
    list_display = (
        "title",
        "badge_text",
        "coupon",
        "sort_order",
        "start_at",
        "end_at",
        "live_badge",
        "is_active",
    )
    list_display_links = ("title",)
    list_editable = ("sort_order", "is_active")
    list_filter = ("is_active", "start_at")
    search_fields = ("title", "description", "coupon__code")
    autocomplete_fields = ("coupon",)
    readonly_fields = ("created_at", "updated_at")
    actions = ["export_as_csv"]


class FlashSaleItemInline(admin.TabularInline):
    model = FlashSaleItem
    extra = 1
    fields = ("variant", "sale_price", "quantity_limit", "sold_count", "sort_order")
    readonly_fields = ("sold_count",)
    autocomplete_fields = ("variant",)


@admin.register(FlashSale)
class FlashSaleAdmin(LiveStateMixin, ExportCsvMixin, admin.ModelAdmin):
    inlines = [FlashSaleItemInline]
    list_display = ("name", "start_at", "end_at", "item_count", "live_badge", "is_active")
    list_display_links = ("name",)
    list_editable = ("is_active",)
    list_filter = ("is_active", "start_at")
    search_fields = ("name", "description")
    readonly_fields = ("created_at", "updated_at")
    actions = ["export_as_csv"]

    @admin.display(description=_("Items"))
    def item_count(self, obj):
        return obj.items.count()


@admin.register(FlashSaleItem)
class FlashSaleItemAdmin(admin.ModelAdmin):
    list_display = (
        "flash_sale",
        "variant",
        "sale_price",
        "discount_column",
        "quantity_limit",
        "sold_count",
    )
    list_filter = ("flash_sale",)
    search_fields = ("variant__sku", "variant__product__name")
    autocomplete_fields = ("flash_sale", "variant")
    list_select_related = ("flash_sale", "variant__product")

    @admin.display(description=_("Discount"))
    def discount_column(self, obj):
        return f"-{obj.discount_percent}%" if obj.discount_percent else "-"
