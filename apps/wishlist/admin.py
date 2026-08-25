"""Wishlist administration."""
from django.contrib import admin
from django.db.models import Count
from django.utils.translation import gettext_lazy as _

from apps.core.admin import ExportCsvMixin

from .models import Wishlist, WishlistItem


class WishlistItemInline(admin.TabularInline):
    model = WishlistItem
    extra = 0
    autocomplete_fields = ("product", "variant")
    fields = ("product", "variant", "note", "created_at")
    readonly_fields = ("created_at",)


@admin.register(Wishlist)
class WishlistAdmin(ExportCsvMixin, admin.ModelAdmin):
    inlines = [WishlistItemInline]
    list_display = ("user", "item_count_column", "updated_at")
    search_fields = ("user__email", "user__first_name", "user__last_name")
    autocomplete_fields = ("user",)
    readonly_fields = ("created_at", "updated_at")
    actions = ["export_as_csv"]

    def get_queryset(self, request):
        return (
            super().get_queryset(request).select_related("user").annotate(_items=Count("items"))
        )

    @admin.display(description=_("Items"), ordering="_items")
    def item_count_column(self, obj):
        return obj._items or 0


@admin.register(WishlistItem)
class WishlistItemAdmin(admin.ModelAdmin):
    """Useful as a demand signal: what customers save but do not buy."""

    list_display = ("product", "variant", "owner", "created_at")
    list_filter = ("created_at", "product__category")
    search_fields = ("product__name", "wishlist__user__email")
    autocomplete_fields = ("wishlist", "product", "variant")
    list_select_related = ("product", "variant", "wishlist__user")
    date_hierarchy = "created_at"

    @admin.display(description=_("Customer"))
    def owner(self, obj):
        return obj.wishlist.user.get_display_name()
