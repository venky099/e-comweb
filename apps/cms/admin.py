from django.contrib import admin
from django.utils.translation import gettext_lazy as _

from apps.audit.mixins import AuditedModelAdmin
from apps.cms.models import Page, Post, PostCategory


@admin.register(Page)
class PageAdmin(AuditedModelAdmin, admin.ModelAdmin):
    list_display = ("title", "slug", "is_published", "show_in_footer", "sort_order", "updated_at")
    list_filter = ("is_published", "show_in_footer")
    search_fields = ("title", "slug", "body")
    prepopulated_fields = {"slug": ("title",)}
    list_editable = ("is_published", "show_in_footer", "sort_order")
    fieldsets = (
        (None, {"fields": ("title", "slug", "summary", "body")}),
        (_("Visibility"), {"fields": ("is_published", "published_at", "show_in_footer", "sort_order")}),
        (_("Search engines"), {"fields": ("meta_title", "meta_description"), "classes": ("collapse",)}),
    )


@admin.register(PostCategory)
class PostCategoryAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "post_count")
    search_fields = ("name",)
    prepopulated_fields = {"slug": ("name",)}

    @admin.display(description=_("Posts"))
    def post_count(self, obj):
        return obj.posts.count()


@admin.register(Post)
class PostAdmin(AuditedModelAdmin, admin.ModelAdmin):
    list_display = ("title", "category", "author_label", "is_published", "published_at", "view_count")
    list_filter = ("is_published", "category", "published_at")
    search_fields = ("title", "excerpt", "body", "tags")
    prepopulated_fields = {"slug": ("title",)}
    date_hierarchy = "published_at"
    autocomplete_fields = ("category", "author", "products")
    readonly_fields = ("view_count",)
    fieldsets = (
        (None, {"fields": ("title", "slug", "category", "excerpt", "body", "cover_image")}),
        (_("Attribution"), {"fields": ("author", "author_label")}),
        (
            _("Publishing"),
            {
                "fields": ("is_published", "published_at", "tags", "products"),
                "description": _("A future date schedules the post rather than hiding it."),
            },
        ),
        (_("Search engines"), {"fields": ("meta_title", "meta_description"), "classes": ("collapse",)}),
    )

    def save_model(self, request, obj, form, change):
        if not obj.author_id:
            obj.author = request.user
        super().save_model(request, obj, form, change)
