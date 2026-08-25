"""Admin configuration for users and addresses."""
from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.db.models import Count, Q, Sum
from django.urls import reverse
from django.utils.html import format_html
from django.utils.translation import gettext_lazy as _

from apps.core.admin import ExportCsvMixin, badge, thumbnail

from .forms import AddressForm
from .models import Address, User


class AddressInline(admin.TabularInline):
    model = Address
    form = AddressForm
    extra = 0
    fields = ("label", "full_name", "phone", "city", "state", "postal_code", "is_default")
    show_change_link = True


@admin.register(User)
class UserAdmin(ExportCsvMixin, BaseUserAdmin):
    """Customer/staff management.

    The list is annotated with order counts and lifetime spend so the customer
    report is available without leaving the admin.
    """

    inlines = [AddressInline]
    list_display = (
        "avatar_thumb",
        "email",
        "full_name",
        "phone",
        "order_count",
        "lifetime_value",
        "role_badge",
        "active_badge",
        "created_at",
    )
    list_display_links = ("email",)
    list_filter = (
        "is_active",
        "is_staff",
        "is_superuser",
        "email_verified",
        "marketing_opt_in",
        "gender",
        "created_at",
    )
    search_fields = ("email", "username", "first_name", "last_name", "phone")
    ordering = ("-created_at",)
    date_hierarchy = "created_at"
    list_per_page = 40
    actions = ["activate_users", "deactivate_users", "export_as_csv"]
    readonly_fields = ("last_login", "date_joined", "created_at", "updated_at", "last_seen_at")
    csv_fields = ("id", "email", "first_name", "last_name", "phone", "is_active", "created_at")

    fieldsets = (
        (None, {"fields": ("username", "password")}),
        (
            _("Personal info"),
            {
                "fields": (
                    "first_name",
                    "last_name",
                    "email",
                    "phone",
                    "gender",
                    "date_of_birth",
                    "avatar",
                )
            },
        ),
        (
            _("Verification & marketing"),
            {"fields": ("email_verified", "phone_verified", "marketing_opt_in")},
        ),
        (
            _("Permissions"),
            {
                "fields": (
                    "is_active",
                    "is_staff",
                    "is_superuser",
                    "groups",
                    "user_permissions",
                ),
                "classes": ("collapse",),
            },
        ),
        (
            _("Important dates"),
            {
                "fields": ("last_login", "date_joined", "last_seen_at", "created_at", "updated_at"),
                "classes": ("collapse",),
            },
        ),
    )

    add_fieldsets = (
        (
            None,
            {
                "classes": ("wide",),
                "fields": (
                    "username",
                    "email",
                    "first_name",
                    "last_name",
                    "phone",
                    "password1",
                    "password2",
                ),
            },
        ),
    )

    def get_queryset(self, request):
        # Annotate once so the list view never queries per row.
        return (
            super()
            .get_queryset(request)
            .annotate(
                _order_count=Count("orders", distinct=True),
                _lifetime_value=Sum(
                    "orders__total_amount",
                    filter=~Q(
                        orders__status__in=["cancelled", "returned", "refunded"]
                    ),
                ),
            )
        )

    @admin.display(description=_("Avatar"))
    def avatar_thumb(self, obj):
        return thumbnail(obj.avatar, 36)

    @admin.display(description=_("Name"), ordering="first_name")
    def full_name(self, obj):
        return obj.get_full_name() or "-"

    @admin.display(description=_("Orders"), ordering="_order_count")
    def order_count(self, obj):
        count = obj._order_count or 0
        if not count:
            return "0"
        url = reverse("admin:orders_order_changelist") + f"?user__id__exact={obj.pk}"
        return format_html('<a href="{}">{}</a>', url, count)

    @admin.display(description=_("Lifetime value"), ordering="_lifetime_value")
    def lifetime_value(self, obj):
        return f"{obj._lifetime_value or 0:,.2f}"

    @admin.display(description=_("Role"))
    def role_badge(self, obj):
        if obj.is_superuser:
            return badge("Superuser", "dark")
        if obj.is_staff:
            return badge("Staff", "primary")
        return badge("Customer", "secondary")

    @admin.display(description=_("Status"), boolean=False)
    def active_badge(self, obj):
        return badge("Active", "success") if obj.is_active else badge("Disabled", "danger")

    @admin.action(description=_("Activate selected accounts"))
    def activate_users(self, request, queryset):
        updated = queryset.update(is_active=True)
        self.message_user(request, _("%(n)d account(s) activated.") % {"n": updated})

    @admin.action(description=_("Deactivate selected accounts"))
    def deactivate_users(self, request, queryset):
        # Never let an admin lock themselves out.
        updated = queryset.exclude(pk=request.user.pk).update(is_active=False)
        self.message_user(request, _("%(n)d account(s) deactivated.") % {"n": updated})


@admin.register(Address)
class AddressAdmin(ExportCsvMixin, admin.ModelAdmin):
    list_display = (
        "full_name",
        "user",
        "label",
        "city",
        "state",
        "postal_code",
        "phone",
        "default_badge",
    )
    list_filter = ("label", "is_default", "state", "country", "created_at")
    search_fields = ("full_name", "phone", "city", "postal_code", "user__email")
    autocomplete_fields = ("user",)
    list_select_related = ("user",)
    list_per_page = 50
    actions = ["export_as_csv"]
    readonly_fields = ("created_at", "updated_at")

    @admin.display(description=_("Default"))
    def default_badge(self, obj):
        return badge("Default", "success") if obj.is_default else "-"
