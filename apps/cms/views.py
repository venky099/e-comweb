"""Reading pages and posts.

Every view filters through ``published()``, so a draft or a post dated in the
future is a 404 to the public even if somebody has the URL. Staff see drafts,
which is what makes previewing possible without a second system.
"""
from django.db.models import F
from django.shortcuts import get_object_or_404, render
from django.views.generic import DetailView, ListView

from apps.cms.models import Page, Post, PostCategory


class VisibilityMixin:
    """Drafts and scheduled items are visible to staff only."""

    def visible(self, queryset):
        viewer = self.request.user
        if viewer.is_authenticated and viewer.is_staff:
            return queryset
        return queryset.published()


class PostListView(VisibilityMixin, ListView):
    template_name = "cms/post_list.html"
    context_object_name = "posts"
    paginate_by = 9

    def get_queryset(self):
        return self.visible(
            Post.objects.select_related("category").order_by("-published_at")
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["categories"] = PostCategory.objects.all()
        return context


class PostCategoryView(PostListView):
    def get_queryset(self):
        self.category = get_object_or_404(PostCategory, slug=self.kwargs["slug"])
        return super().get_queryset().filter(category=self.category)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["category"] = self.category
        return context


class PostDetailView(VisibilityMixin, DetailView):
    template_name = "cms/post_detail.html"
    context_object_name = "post"

    def get_queryset(self):
        return self.visible(
            Post.objects.select_related("category", "author").prefetch_related("products")
        )

    def get_object(self, queryset=None):
        post = super().get_object(queryset)
        # F() so concurrent readers cannot lose each other's increments, and
        # without touching updated_at -- a read is not an edit.
        Post.objects.filter(pk=post.pk).update(view_count=F("view_count") + 1)
        return post

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["related"] = (
            self.visible(Post.objects.all())
            .exclude(pk=self.object.pk)
            .filter(category=self.object.category)[:3]
        )
        return context


class PageDetailView(VisibilityMixin, DetailView):
    template_name = "cms/page.html"
    context_object_name = "page"

    def get_queryset(self):
        return self.visible(Page.objects.all())
