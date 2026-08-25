"""Abstract base models shared by every app."""
from django.db import models
from django.utils.text import slugify


class TimeStampedModel(models.Model):
    """Adds ``created_at`` / ``updated_at`` to a model.

    Every concrete model in this project inherits from this, per the project
    convention that all rows are auditable by time.
    """

    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class ActiveQuerySet(models.QuerySet):
    """Queryset helper for models carrying an ``is_active`` flag."""

    def active(self):
        return self.filter(is_active=True)

    def inactive(self):
        return self.filter(is_active=False)


class ActiveManager(models.Manager.from_queryset(ActiveQuerySet)):
    pass


class SluggedModel(models.Model):
    """A model with a unique auto-populated slug.

    ``slug_source_field`` names the field the slug is derived from. Collisions
    get a numeric suffix so bulk imports never fail on duplicates.
    """

    slug_source_field = "name"

    slug = models.SlugField(max_length=255, unique=True, blank=True, db_index=True)

    class Meta:
        abstract = True

    def build_slug(self):
        base = slugify(getattr(self, self.slug_source_field) or "")[:200] or "item"
        candidate = base
        suffix = 2
        model = self.__class__
        while (
            model._default_manager.filter(slug=candidate).exclude(pk=self.pk).exists()
        ):
            candidate = f"{base}-{suffix}"
            suffix += 1
        return candidate

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = self.build_slug()
        super().save(*args, **kwargs)
