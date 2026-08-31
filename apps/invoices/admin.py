from django.contrib import admin
from django.utils.translation import gettext_lazy as _

from apps.invoices.models import Invoice, InvoiceLine, InvoiceTaxLine, NumberSeries


class InvoiceLineInline(admin.TabularInline):
    model = InvoiceLine
    extra = 0
    can_delete = False
    readonly_fields = ("description", "sku", "quantity", "unit_price", "discount", "tax", "total")


class InvoiceTaxLineInline(admin.TabularInline):
    model = InvoiceTaxLine
    extra = 0
    can_delete = False
    readonly_fields = ("name", "percent", "amount")


@admin.register(Invoice)
class InvoiceAdmin(admin.ModelAdmin):
    """Invoices are records. They can be read and re-sent, never edited."""

    list_display = ("number", "order", "customer_name", "grand_total", "currency", "issued_at", "is_emailed")
    list_filter = ("currency", "issued_at")
    search_fields = ("number", "order__order_number", "customer_name", "customer_email")
    date_hierarchy = "issued_at"
    inlines = [InvoiceLineInline, InvoiceTaxLineInline]
    readonly_fields = [f.name for f in Invoice._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(NumberSeries)
class NumberSeriesAdmin(admin.ModelAdmin):
    list_display = ("prefix", "kind", "year", "last_number", "padding")
    list_filter = ("kind", "year")

    def has_delete_permission(self, request, obj=None):
        # Deleting a series restarts its numbering and duplicates document
        # numbers that are already in customers' hands.
        return False
