"""Editable pages and a blog (MST spec section 50).

Two kinds of content, deliberately separate. A Page is a fixed part of the
site -- About, Returns policy, Terms -- reached by a known URL that must not
change. A Post is dated writing that accumulates: lookbooks, styling guides,
what a fabric is.

Both carry their own SEO fields, because section 49 wants them and because a
content page nobody can find is not content.
"""
from django.conf import settings
from django.db import models
from django.urls import reverse
from django.utils import timezone
from django.utils.text import slugify
from django.utils.translation import gettext_lazy as _

from apps.core.models import TimeStampedModel


class PublishedQuerySet(models.QuerySet):
    def published(self):
        """Live content only: published, and not dated in the future."""
        return self.filter(
            is_published=True, published_at__lte=timezone.now()
        )


class Page(TimeStampedModel):
    """A standing page at a fixed URL."""

    title = models.CharField(max_length=150)
    slug = models.SlugField(max_length=150, unique=True)
    body = models.TextField(help_text=_("HTML is allowed."))
    summary = models.CharField(max_length=255, blank=True)
    is_published = models.BooleanField(default=True)
    published_at = models.DateTimeField(default=timezone.now)
    show_in_footer = models.BooleanField(
        default=False, help_text=_("List this page in the site footer.")
    )
    sort_order = models.PositiveIntegerField(default=0)

    meta_title = models.CharField(max_length=255, blank=True)
    meta_description = models.CharField(max_length=300, blank=True)

    objects = PublishedQuerySet.as_manager()

    class Meta:
        ordering = ["sort_order", "title"]

    def __str__(self):
        return self.title

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.title)[:150]
        super().save(*args, **kwargs)

    def get_absolute_url(self):
        return reverse("cms:page", kwargs={"slug": self.slug})


class PostCategory(TimeStampedModel):
    name = models.CharField(max_length=80, unique=True)
    slug = models.SlugField(max_length=80, unique=True)
    description = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["name"]
        verbose_name_plural = _("post categories")

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.name)[:80]
        super().save(*args, **kwargs)

    def get_absolute_url(self):
        return reverse("cms:category", kwargs={"slug": self.slug})


class Post(TimeStampedModel):
    """A dated article."""

    title = models.CharField(max_length=200)
    slug = models.SlugField(max_length=200, unique=True)
    category = models.ForeignKey(
        PostCategory,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="posts",
    )
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="posts",
    )
    author_label = models.CharField(
        max_length=150,
        blank=True,
        help_text=_("Byline. Copied so it survives the account being removed."),
    )
    excerpt = models.CharField(
        max_length=300, blank=True, help_text=_("Shown in listings and previews.")
    )
    body = models.TextField(help_text=_("HTML is allowed."))
    cover_image = models.ImageField(upload_to="blog/%Y/%m/", blank=True, null=True)
    tags = models.CharField(
        max_length=255, blank=True, help_text=_("Comma separated.")
    )

    is_published = models.BooleanField(default=False)
    published_at = models.DateTimeField(
        default=timezone.now,
        db_index=True,
        help_text=_("A future date schedules the post rather than hiding it."),
    )
    view_count = models.PositiveIntegerField(default=0)

    meta_title = models.CharField(max_length=255, blank=True)
    meta_description = models.CharField(max_length=300, blank=True)

    # Products a post is about, so a lookbook can link to what it shows.
    products = models.ManyToManyField(
        "catalog.Product", blank=True, related_name="posts"
    )

    objects = PublishedQuerySet.as_manager()

    class Meta:
        ordering = ["-published_at", "-id"]
        indexes = [models.Index(fields=["is_published", "-published_at"])]

    def __str__(self):
        return self.title

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.title)[:200]
        if self.author_id and not self.author_label:
            self.author_label = str(self.author)[:150]
        super().save(*args, **kwargs)

    def get_absolute_url(self):
        return reverse("cms:post", kwargs={"slug": self.slug})

    @property
    def is_scheduled(self):
        return self.is_published and self.published_at > timezone.now()

    @property
    def tag_list(self):
        return [tag.strip() for tag in (self.tags or "").split(",") if tag.strip()]
