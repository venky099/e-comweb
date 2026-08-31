"""Catalog administration.

This is where the spec's "Product Management / Category Management" screens
live -- as ModelAdmin configuration rather than a hand-built dashboard.
"""
from django.contrib import admin
from django.db.models import Count, F, Sum
from django.urls import reverse
from django.utils.html import format_html
from django.utils.translation import gettext_lazy as _

from apps.audit.mixins import AuditedModelAdmin
from apps.core.admin import ExportCsvMixin, badge, thumbnail
from apps.inventory.models import Inventory

from .models import Brand, Category, Product, ProductImage, ProductVariant, Attribute, AttributeValue, SizeGuide


class ProductImageInline(admin.TabularInline):
    model = ProductImage
    extra = 1
    fields = ("preview", "image", "alt_text", "is_primary", "sort_order")
    readonly_fields = ("preview",)

    @admin.display(description=_("Preview"))
    def preview(self, obj):
        return thumbnail(obj.image, 60)


class InventoryInline(admin.StackedInline):
    model = Inventory
    extra = 0
    can_delete = False
    fields = (
        ("quantity_available", "quantity_reserved", "quantity_sold"),
        ("low_stock_threshold", "allow_backorder"),
        "warehouse_location",
    )
    readonly_fields = ("quantity_reserved", "quantity_sold")


class ProductVariantInline(admin.TabularInline):
    model = ProductVariant
    extra = 1
    fields = (
        "preview",
        "sku",
        "size",
        "color",
        "color_hex",
        "price_override",
        "compare_at_price_override",
        "image",
        "is_active",
        "sort_order",
        "stock_column",
    )
    readonly_fields = ("preview", "stock_column")
    show_change_link = True

    @admin.display(description=_("Image"))
    def preview(self, obj):
        return thumbnail(obj.image, 44)

    @admin.display(description=_("Stock"))
    def stock_column(self, obj):
        if not obj.pk:
            return "-"
        inventory = getattr(obj, "inventory", None)
        if inventory is None:
            return badge("No inventory row", "warning")
        colour = {"in_stock": "success", "low_stock": "warning", "out_of_stock": "danger"}[
            inventory.stock_status
        ]
        return badge(f"{inventory.sellable_quantity} sellable", colour)


@admin.register(Category)
class CategoryAdmin(AuditedModelAdmin, ExportCsvMixin, admin.ModelAdmin):
    list_display = (
        "indented_name",
        "thumb",
        "parent",
        "product_count",
        "is_active",
        "is_featured",
        "sort_order",
    )
    list_display_links = ("indented_name",)
    list_editable = ("is_active", "is_featured", "sort_order")
    list_filter = ("is_active", "is_featured", "parent")
    search_fields = ("name", "slug", "description")
    prepopulated_fields = {"slug": ("name",)}
    autocomplete_fields = ("parent",)
    actions = ["activate", "deactivate", "export_as_csv"]
    readonly_fields = ("created_at", "updated_at")
    list_per_page = 50

    fieldsets = (
        (None, {"fields": ("name", "slug", "parent", "description")}),
        (_("Display"), {"fields": ("image", "icon_class", "is_active", "is_featured", "sort_order")}),
        (_("SEO"), {"fields": ("meta_title", "meta_description"), "classes": ("collapse",)}),
        (_("Timestamps"), {"fields": ("created_at", "updated_at"), "classes": ("collapse",)}),
    )

    def get_queryset(self, request):
        return (
            super()
            .get_queryset(request)
            .select_related("parent")
            .annotate(_product_count=Count("products"))
        )

    @admin.display(description=_("Category"), ordering="name")
    def indented_name(self, obj):
        depth = len(obj.ancestors())
        return format_html("{}{}", format_html("&nbsp;" * (depth * 4)), obj.name)

    @admin.display(description=_("Image"))
    def thumb(self, obj):
        return thumbnail(obj.image, 36)

    @admin.display(description=_("Products"), ordering="_product_count")
    def product_count(self, obj):
        count = obj._product_count or 0
        if not count:
            return "0"
        url = reverse("admin:catalog_product_changelist") + f"?category__id__exact={obj.pk}"
        return format_html('<a href="{}">{}</a>', url, count)

    @admin.action(description=_("Activate selected categories"))
    def activate(self, request, queryset):
        self.message_user(request, f"{queryset.update(is_active=True)} category(ies) activated.")

    @admin.action(description=_("Deactivate selected categories"))
    def deactivate(self, request, queryset):
        self.message_user(request, f"{queryset.update(is_active=False)} category(ies) deactivated.")


@admin.register(Brand)
class BrandAdmin(AuditedModelAdmin, ExportCsvMixin, admin.ModelAdmin):
    list_display = ("logo_thumb", "name", "product_count", "is_active", "is_featured", "website")
    list_display_links = ("name",)
    list_editable = ("is_active", "is_featured")
    list_filter = ("is_active", "is_featured")
    search_fields = ("name", "slug", "description")
    prepopulated_fields = {"slug": ("name",)}
    actions = ["export_as_csv"]
    readonly_fields = ("created_at", "updated_at")

    def get_queryset(self, request):
        return super().get_queryset(request).annotate(_product_count=Count("products"))

    @admin.display(description=_("Logo"))
    def logo_thumb(self, obj):
        return thumbnail(obj.logo, 36)

    @admin.display(description=_("Products"), ordering="_product_count")
    def product_count(self, obj):
        return obj._product_count or 0


@admin.register(Product)
class ProductAdmin(AuditedModelAdmin, ExportCsvMixin, admin.ModelAdmin):
    """The main merchandising screen.

    ``list_editable`` on price/status/flags is deliberate: it turns the
    changelist into a bulk pricing and merchandising tool.
    """

    inlines = [ProductImageInline, ProductVariantInline]
    list_display = (
        "thumb",
        "name",
        "sku",
        "category",
        "brand",
        "price",
        "compare_at_price",
        "discount_badge",
        "stock_badge",
        "rating_column",
        "sold_count",
        "status",
        "is_featured",
        "is_active",
    )
    list_display_links = ("name",)
    list_editable = ("price", "compare_at_price", "status", "is_featured", "is_active")
    list_filter = (
        "status",
        "is_active",
        "is_featured",
        "is_best_seller",
        "category",
        "brand",
        "is_returnable",
        "is_cod_available",
        "created_at",
    )
    search_fields = ("name", "sku", "slug", "description", "tags", "brand__name", "category__name")
    prepopulated_fields = {"slug": ("name",)}
    autocomplete_fields = ("category", "brand")
    date_hierarchy = "created_at"
    list_per_page = 30
    save_on_top = True
    readonly_fields = (
        "rating_average",
        "rating_count",
        "sold_count",
        "view_count",
        "published_at",
        "created_at",
        "updated_at",
    )
    actions = [
        "mark_featured",
        "unmark_featured",
        "mark_best_seller",
        "publish_products",
        "archive_products",
        "export_as_csv",
    ]
    csv_fields = (
        "id",
        "name",
        "sku",
        "price",
        "compare_at_price",
        "status",
        "sold_count",
        "rating_average",
    )

    fieldsets = (
        (None, {"fields": ("name", "slug", "sku", "category", "brand", "status", "is_active")}),
        (_("Content"), {"fields": ("short_description", "description", "specifications", "tags")}),
        (
            _("Pricing"),
            {"fields": (("price", "compare_at_price"), ("cost_price", "tax_rate_percent"))},
        ),
        (
            _("Merchandising"),
            {"fields": ("is_featured", "is_best_seller", "is_returnable", "is_cod_available")},
        ),
        (_("Logistics"), {"fields": ("weight_grams", "warranty"), "classes": ("collapse",)}),
        (_("SEO"), {"fields": ("meta_title", "meta_description"), "classes": ("collapse",)}),
        (
            _("Statistics"),
            {
                "fields": (
                    ("rating_average", "rating_count"),
                    ("sold_count", "view_count"),
                    ("published_at", "created_at", "updated_at"),
                ),
                "classes": ("collapse",),
            },
        ),
    )

    def get_queryset(self, request):
        return (
            super()
            .get_queryset(request)
            .select_related("category", "brand")
            .prefetch_related("images", "variants__inventory")
            .annotate(
                _stock=Sum(
                    F("variants__inventory__quantity_available")
                    - F("variants__inventory__quantity_reserved")
                )
            )
        )

    @admin.display(description=_("Image"))
    def thumb(self, obj):
        image = obj.primary_image
        return thumbnail(image.image, 44) if image else "-"

    @admin.display(description=_("Discount"))
    def discount_badge(self, obj):
        if not obj.has_discount:
            return "-"
        return badge(f"-{obj.discount_percent}%", "danger")

    @admin.display(description=_("Stock"), ordering="_stock")
    def stock_badge(self, obj):
        stock = obj._stock or 0
        if stock <= 0:
            return badge("Out of stock", "danger")
        if stock <= 5:
            return badge(f"Low: {stock}", "warning")
        return badge(str(stock), "success")

    @admin.display(description=_("Rating"), ordering="rating_average")
    def rating_column(self, obj):
        if not obj.rating_count:
            return "-"
        return format_html("{} ({})", f"{obj.rating_average:.1f}*", obj.rating_count)

    # ---- bulk actions --------------------------------------------------
    @admin.action(description=_("Mark as featured"))
    def mark_featured(self, request, queryset):
        self.message_user(request, f"{queryset.update(is_featured=True)} product(s) featured.")

    @admin.action(description=_("Remove featured flag"))
    def unmark_featured(self, request, queryset):
        self.message_user(request, f"{queryset.update(is_featured=False)} product(s) unfeatured.")

    @admin.action(description=_("Mark as best seller"))
    def mark_best_seller(self, request, queryset):
        self.message_user(request, f"{queryset.update(is_best_seller=True)} product(s) updated.")

    @admin.action(description=_("Publish selected products"))
    def publish_products(self, request, queryset):
        count = queryset.update(status=Product.Status.PUBLISHED, is_active=True)
        self.message_user(request, f"{count} product(s) published.")

    @admin.action(description=_("Archive selected products"))
    def archive_products(self, request, queryset):
        count = queryset.update(status=Product.Status.ARCHIVED, is_active=False)
        self.message_user(request, f"{count} product(s) archived.")


@admin.register(ProductVariant)
class ProductVariantAdmin(AuditedModelAdmin, ExportCsvMixin, admin.ModelAdmin):
    """Variant-level view -- the fastest way to bulk-edit SKUs and stock."""

    inlines = [InventoryInline]
    list_display = (
        "thumb",
        "sku",
        "product",
        "label_column",
        "effective_price",
        "available_column",
        "is_active",
        "sort_order",
    )
    list_display_links = ("sku",)
    list_editable = ("is_active", "sort_order")
    list_filter = ("is_active", "size", "color", "product__category", "product__brand")
    search_fields = ("sku", "name", "size", "color", "product__name")
    autocomplete_fields = ("product",)
    list_select_related = ("product",)
    list_per_page = 50
    actions = ["activate", "deactivate", "export_as_csv"]
    readonly_fields = ("created_at", "updated_at")

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("product", "inventory")

    @admin.display(description=_("Image"))
    def thumb(self, obj):
        return thumbnail(obj.image, 40)

    @admin.display(description=_("Variant"))
    def label_column(self, obj):
        return obj.label

    @admin.display(description=_("Price"))
    def effective_price(self, obj):
        if obj.price_override is not None:
            return format_html("<strong>{}</strong>", f"{obj.price:.2f}")
        return format_html('<span style="color:#888">{} (inherited)</span>', f"{obj.price:.2f}")

    @admin.display(description=_("Available"))
    def available_column(self, obj):
        inventory = getattr(obj, "inventory", None)
        if inventory is None:
            return badge("No stock row", "warning")
        colour = {"in_stock": "success", "low_stock": "warning", "out_of_stock": "danger"}[
            inventory.stock_status
        ]
        return badge(str(inventory.sellable_quantity), colour)

    @admin.action(description=_("Activate selected variants"))
    def activate(self, request, queryset):
        self.message_user(request, f"{queryset.update(is_active=True)} variant(s) activated.")

    @admin.action(description=_("Deactivate selected variants"))
    def deactivate(self, request, queryset):
        self.message_user(request, f"{queryset.update(is_active=False)} variant(s) deactivated.")


@admin.register(ProductImage)
class ProductImageAdmin(admin.ModelAdmin):
    list_display = ("preview", "product", "alt_text", "is_primary", "sort_order")
    list_display_links = ("product",)
    list_editable = ("is_primary", "sort_order")
    list_filter = ("is_primary",)
    search_fields = ("product__name", "alt_text")
    autocomplete_fields = ("product",)
    list_select_related = ("product",)

    @admin.display(description=_("Preview"))
    def preview(self, obj):
        return thumbnail(obj.image, 48)


class AttributeValueInline(admin.TabularInline):
    model = AttributeValue
    extra = 1
    prepopulated_fields = {"slug": ("value",)}


@admin.register(Attribute)
class AttributeAdmin(AuditedModelAdmin, admin.ModelAdmin):
    list_display = ("name", "code", "kind", "value_count", "is_filterable", "show_on_product", "sort_order", "is_active")
    list_filter = ("kind", "is_filterable", "is_active")
    search_fields = ("name", "code")
    list_editable = ("is_filterable", "show_on_product", "sort_order", "is_active")
    prepopulated_fields = {"code": ("name",)}
    filter_horizontal = ("categories",)
    inlines = [AttributeValueInline]

    @admin.display(description=_("Values"))
    def value_count(self, obj):
        return obj.values.count()


@admin.register(AttributeValue)
class AttributeValueAdmin(admin.ModelAdmin):
    list_display = ("value", "attribute", "slug", "sort_order")
    list_filter = ("attribute",)
    search_fields = ("value", "slug")
    autocomplete_fields = ("attribute",)


@admin.register(SizeGuide)
class SizeGuideAdmin(AuditedModelAdmin, admin.ModelAdmin):
    list_display = ("name", "category", "brand", "unit", "row_count", "is_active")
    list_filter = ("is_active", "category", "brand")
    search_fields = ("name",)
    autocomplete_fields = ("category", "brand")

    @admin.display(description=_("Rows"))
    def row_count(self, obj):
        return len(obj.rows or [])
