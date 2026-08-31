"""Product attributes an administrator can define (MST sections 5 and 12).

Section 12 asks shoppers to filter by fabric, pattern, occasion and gender.
Hard-coding four columns would answer that list and nothing else -- the next
season brings sleeve length, neckline, work type -- and section 4 is explicit
that the admin should be able to create things without a developer.

So attributes are data. An administrator defines "Fabric" with values Silk,
Cotton, Georgette; the filter sidebar and the product page pick them up
without a migration.

Size and colour deliberately stay as columns on ProductVariant. They are not
descriptive: they identify which physical item is being bought, they carry
their own SKU, price and stock, and the variant picker depends on them. An
attribute describes a product; a variant option *is* one.
"""
from django.db import models
from django.utils.text import slugify
from django.utils.translation import gettext_lazy as _

from apps.core.models import TimeStampedModel


class Attribute(TimeStampedModel):
    """A named property products can carry, e.g. Fabric or Occasion."""

    class Kind(models.TextChoices):
        CHOICE = "choice", _("Pick from a list")
        TEXT = "text", _("Free text")

    name = models.CharField(max_length=64, unique=True)
    code = models.SlugField(
        max_length=32,
        unique=True,
        help_text=_("Used in filter URLs, e.g. ?fabric=silk"),
    )
    kind = models.CharField(max_length=8, choices=Kind.choices, default=Kind.CHOICE)
    is_filterable = models.BooleanField(
        default=True, help_text=_("Show as a filter on listing pages.")
    )
    show_on_product = models.BooleanField(
        default=True, help_text=_("List in the product's specifications.")
    )
    categories = models.ManyToManyField(
        "catalog.Category",
        blank=True,
        related_name="attributes",
        help_text=_("Limit to these categories. Empty means every category."),
    )
    sort_order = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["sort_order", "name"]

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if not self.code:
            self.code = slugify(self.name)[:32]
        super().save(*args, **kwargs)


class AttributeValue(TimeStampedModel):
    """One allowed value of an attribute, e.g. Silk."""

    attribute = models.ForeignKey(
        Attribute, on_delete=models.CASCADE, related_name="values"
    )
    value = models.CharField(max_length=64)
    slug = models.SlugField(max_length=64)
    sort_order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["sort_order", "value"]
        constraints = [
            models.UniqueConstraint(
                fields=["attribute", "slug"], name="catalog_attrvalue_unique_slug"
            )
        ]

    def __str__(self):
        return f"{self.attribute.name}: {self.value}"

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.value)[:64]
        super().save(*args, **kwargs)


class ProductAttribute(TimeStampedModel):
    """An attribute value carried by one product.

    ``text_value`` holds free-text attributes; ``value`` holds the ones picked
    from a list, which are the ones that can be filtered on.
    """

    product = models.ForeignKey(
        "catalog.Product", on_delete=models.CASCADE, related_name="attribute_values"
    )
    attribute = models.ForeignKey(
        Attribute, on_delete=models.CASCADE, related_name="product_values"
    )
    value = models.ForeignKey(
        AttributeValue,
        on_delete=models.CASCADE,
        related_name="products",
        blank=True,
        null=True,
    )
    text_value = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["attribute__sort_order", "attribute__name"]
        constraints = [
            models.UniqueConstraint(
                fields=["product", "attribute", "value"],
                name="catalog_product_attribute_once",
            )
        ]

    def __str__(self):
        return f"{self.product}: {self.display}"

    @property
    def display(self):
        if self.value_id:
            return self.value.value
        return self.text_value


class SizeGuide(TimeStampedModel):
    """A sizing table (MST section 14).

    Clothing sizes are not comparable between brands or garment types, so a
    guide belongs to a category or a brand rather than being one global table.
    Rows are stored as JSON because the useful columns differ per garment --
    a saree guide has a blouse length, a shirt guide has a collar.
    """

    name = models.CharField(max_length=100)
    category = models.ForeignKey(
        "catalog.Category",
        on_delete=models.CASCADE,
        related_name="size_guides",
        blank=True,
        null=True,
    )
    brand = models.ForeignKey(
        "catalog.Brand",
        on_delete=models.CASCADE,
        related_name="size_guides",
        blank=True,
        null=True,
    )
    unit = models.CharField(
        max_length=16, default="cm", help_text=_("The measurements' unit, e.g. cm or in.")
    )
    columns = models.JSONField(
        default=list, help_text=_('e.g. ["Size", "Bust", "Waist", "Length"]')
    )
    rows = models.JSONField(
        default=list, help_text=_('e.g. [["S", "86", "68", "104"], ...]')
    )
    note = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name

    @property
    def is_usable(self):
        """A guide with no rows is worse than no guide -- it looks broken."""
        return bool(self.columns) and bool(self.rows)
