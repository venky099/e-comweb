"""Review moderation."""
from django.contrib import admin
from django.utils import timezone
from django.utils.html import format_html
from django.utils.translation import gettext_lazy as _

from apps.core.admin import ExportCsvMixin, badge, thumbnail

from .models import Review, ReviewHelpfulVote, ReviewImage


class ReviewImageInline(admin.TabularInline):
    model = ReviewImage
    extra = 0
    fields = ("preview", "image", "caption")
    readonly_fields = ("preview",)

    @admin.display(description=_("Preview"))
    def preview(self, obj):
        return thumbnail(obj.image, 60)


@admin.register(Review)
class ReviewAdmin(ExportCsvMixin, admin.ModelAdmin):
    """Moderation queue.

    Approving/hiding goes through ``save()`` rather than a bulk ``update()``
    so the rating-aggregation signal fires and product averages stay correct.
    """

    inlines = [ReviewImageInline]
    list_display = (
        "created_at",
        "product",
        "stars",
        "title",
        "author_column",
        "verified_badge",
        "helpful_count",
        "is_approved",
    )
    list_display_links = ("title", "product")
    list_editable = ("is_approved",)
    list_filter = ("rating", "is_approved", "verified_purchase", "created_at")
    search_fields = ("title", "comment", "product__name", "user__email")
    autocomplete_fields = ("product", "user", "order_item")
    date_hierarchy = "created_at"
    list_select_related = ("product", "user")
    list_per_page = 40
    actions = ["approve_reviews", "reject_reviews", "export_as_csv"]
    readonly_fields = ("verified_purchase", "helpful_count", "created_at", "updated_at")

    fieldsets = (
        (None, {"fields": ("product", "user", "order_item", "rating", "title", "comment")}),
        (_("Moderation"), {"fields": ("is_approved", "verified_purchase", "helpful_count")}),
        (_("Staff reply"), {"fields": ("staff_reply", "staff_replied_at")}),
        (_("Timestamps"), {"fields": ("created_at", "updated_at"), "classes": ("collapse",)}),
    )

    @admin.display(description=_("Rating"), ordering="rating")
    def stars(self, obj):
        filled = "★" * obj.rating
        empty = "☆" * (5 - obj.rating)
        return format_html(
            '<span style="color:#f59e0b;letter-spacing:1px">{}{}</span>', filled, empty
        )

    @admin.display(description=_("Author"))
    def author_column(self, obj):
        return obj.user.get_display_name()

    @admin.display(description=_("Verified"))
    def verified_badge(self, obj):
        if obj.verified_purchase:
            return badge("Verified buyer", "success")
        return badge("Unverified", "secondary")

    @admin.action(description=_("Approve selected reviews"))
    def approve_reviews(self, request, queryset):
        count = 0
        for review in queryset:
            review.is_approved = True
            review.save(update_fields=["is_approved", "updated_at"])
            count += 1
        self.message_user(request, _("%(n)d review(s) approved.") % {"n": count})

    @admin.action(description=_("Hide selected reviews"))
    def reject_reviews(self, request, queryset):
        count = 0
        for review in queryset:
            review.is_approved = False
            review.save(update_fields=["is_approved", "updated_at"])
            count += 1
        self.message_user(request, _("%(n)d review(s) hidden.") % {"n": count})

    def save_model(self, request, obj, form, change):
        if obj.staff_reply and not obj.staff_replied_at:
            obj.staff_replied_at = timezone.now()
        super().save_model(request, obj, form, change)


@admin.register(ReviewHelpfulVote)
class ReviewHelpfulVoteAdmin(admin.ModelAdmin):
    list_display = ("review", "user", "created_at")
    search_fields = ("review__title", "user__email")
    list_select_related = ("review", "user")

    def has_add_permission(self, request):
        return False
