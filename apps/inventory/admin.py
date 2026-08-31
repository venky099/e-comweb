"""Inventory administration."""
from django import forms
from django.contrib import admin
from django.db.models import F
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.html import format_html
from django.utils.translation import gettext_lazy as _

from apps.audit.mixins import AuditedModelAdmin
from apps.core.admin import ExportCsvMixin, badge

from . import services
from .models import Inventory, StockMovement, Warehouse


class StockLevelFilter(admin.SimpleListFilter):
    """Filter by computed stock status rather than a raw column."""

    title = _("stock level")
    parameter_name = "stock_level"

    def lookups(self, request, model_admin):
        return (
            ("out", _("Out of stock")),
            ("low", _("Low stock")),
            ("healthy", _("In stock")),
        )

    def queryset(self, request, queryset):
        value = self.value()
        if value == "out":
            return queryset.out_of_stock()
        if value == "low":
            return queryset.low_stock()
        if value == "healthy":
            return queryset.filter(
                quantity_available__gt=F("quantity_reserved") + F("low_stock_threshold")
            )
        return queryset


class BulkRestockForm(forms.Form):
    """Intermediate form for the bulk restock action."""

    quantity = forms.IntegerField(
        min_value=1, label=_("Units to add to each selected variant")
    )
    note = forms.CharField(max_length=255, required=False, label=_("Note (e.g. PO number)"))


@admin.register(Inventory)
class InventoryAdmin(AuditedModelAdmin, ExportCsvMixin, admin.ModelAdmin):
    """Stock control screen.

    Quantities are edited through actions rather than free-text fields so
    every change lands in the movement log with a reason attached.
    """

    list_display = (
        "variant_column",
        "product_link",
        "quantity_available",
        "quantity_reserved",
        "sellable_column",
        "quantity_sold",
        "low_stock_threshold",
        "status_badge",
        "restocked_at",
    )
    list_display_links = ("variant_column",)
    list_editable = ("low_stock_threshold",)
    list_filter = (StockLevelFilter, "allow_backorder", "variant__product__category")
    search_fields = (
        "variant__sku",
        "variant__product__name",
        "variant__size",
        "variant__color",
        "warehouse_location",
    )
    readonly_fields = ("quantity_reserved", "quantity_sold", "created_at", "updated_at")
    list_per_page = 50
    actions = ["bulk_restock", "export_as_csv"]
    csv_fields = (
        "id",
        "quantity_available",
        "quantity_reserved",
        "quantity_sold",
        "low_stock_threshold",
    )

    def get_queryset(self, request):
        return super().get_queryset(request).with_variant()

    @admin.display(description=_("Variant"), ordering="variant__sku")
    def variant_column(self, obj):
        return f"{obj.variant.sku} - {obj.variant.label}"

    @admin.display(description=_("Product"))
    def product_link(self, obj):
        product = obj.variant.product
        url = reverse("admin:catalog_product_change", args=[product.pk])
        return format_html('<a href="{}">{}</a>', url, product.name)

    @admin.display(description=_("Sellable"))
    def sellable_column(self, obj):
        return format_html("<strong>{}</strong>", obj.sellable_quantity)

    @admin.display(description=_("Status"))
    def status_badge(self, obj):
        colour = {"in_stock": "success", "low_stock": "warning", "out_of_stock": "danger"}[
            obj.stock_status
        ]
        return badge(obj.stock_label, colour)

    @admin.action(description=_("Restock selected variants"))
    def bulk_restock(self, request, queryset):
        """Two-step action: ask for a quantity, then apply it with an audit row."""
        if "apply" in request.POST:
            form = BulkRestockForm(request.POST)
            if form.is_valid():
                quantity = form.cleaned_data["quantity"]
                note = form.cleaned_data["note"]
                for inventory in queryset.select_related("variant"):
                    services.restock(
                        inventory.variant, quantity, note=note, user=request.user
                    )
                self.message_user(
                    request,
                    _("Added %(q)d unit(s) to %(n)d variant(s).")
                    % {"q": quantity, "n": queryset.count()},
                )
                return redirect(request.get_full_path())
        else:
            form = BulkRestockForm()

        return render(
            request,
            "admin/inventory/bulk_restock.html",
            {
                "title": _("Restock variants"),
                "form": form,
                "items": queryset,
                "action_checkbox_name": admin.helpers.ACTION_CHECKBOX_NAME,
                "opts": self.model._meta,
            },
        )


@admin.register(StockMovement)
class StockMovementAdmin(ExportCsvMixin, admin.ModelAdmin):
    """Read-only audit trail. Rows are written by the service layer only."""

    list_display = (
        "created_at",
        "variant_column",
        "reason_badge",
        "quantity_column",
        "quantity_after",
        "reference",
        "created_by",
    )
    list_filter = ("reason", "created_at")
    search_fields = ("variant__sku", "variant__product__name", "reference", "note")
    date_hierarchy = "created_at"
    list_select_related = ("variant__product", "created_by")
    list_per_page = 60
    actions = ["export_as_csv"]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    @admin.display(description=_("Variant"))
    def variant_column(self, obj):
        return f"{obj.variant.sku} ({obj.variant.product.name})"

    @admin.display(description=_("Reason"))
    def reason_badge(self, obj):
        colour = {
            StockMovement.Reason.PURCHASE: "success",
            StockMovement.Reason.SALE: "primary",
            StockMovement.Reason.RESERVATION: "info",
            StockMovement.Reason.RELEASE: "secondary",
            StockMovement.Reason.CANCELLATION: "warning",
            StockMovement.Reason.RETURN: "warning",
            StockMovement.Reason.ADJUSTMENT: "dark",
            StockMovement.Reason.DAMAGE: "danger",
        }.get(obj.reason, "secondary")
        return badge(obj.get_reason_display(), colour)

    @admin.display(description=_("Change"), ordering="quantity")
    def quantity_column(self, obj):
        colour = "#198754" if obj.quantity > 0 else "#dc3545"
        return format_html('<span style="color:{};font-weight:600">{:+d}</span>', colour, obj.quantity)


@admin.register(Warehouse)
class WarehouseAdmin(AuditedModelAdmin, admin.ModelAdmin):
    list_display = ("name", "code", "city", "country", "priority", "is_default", "is_active", "held_lines")
    list_filter = ("is_active", "is_default", "country")
    search_fields = ("name", "code", "city")
    list_editable = ("priority", "is_default", "is_active")
    prepopulated_fields = {"code": ("name",)}

    @admin.display(description=_("Stock lines"))
    def held_lines(self, obj):
        return obj.stock.count()
