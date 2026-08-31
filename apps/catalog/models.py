"""Catalog: categories, brands, products, images and variants."""
from decimal import Decimal

from django.core.validators import MinValueValidator
from django.db import models
from django.urls import reverse
from django.utils.translation import gettext_lazy as _

from apps.core.models import ActiveManager, SluggedModel, TimeStampedModel

ZERO = Decimal("0.00")


class CategoryQuerySet(models.QuerySet):
    def active(self):
        return self.filter(is_active=True)

    def roots(self):
        return self.filter(parent__isnull=True)


class Category(TimeStampedModel, SluggedModel):
    """Self-referential category tree (category -> subcategory -> ...)."""

    name = models.CharField(max_length=150, db_index=True)
    parent = models.ForeignKey(
        "self",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="children",
        db_index=True,
    )
    description = models.TextField(blank=True)
    image = models.ImageField(upload_to="categories/", blank=True, null=True)
    icon_class = models.CharField(
        max_length=64, blank=True, help_text=_("Optional Bootstrap icon class, e.g. bi-laptop.")
    )
    is_active = models.BooleanField(default=True, db_index=True)
    is_featured = models.BooleanField(default=False, db_index=True)
    sort_order = models.PositiveIntegerField(default=0, db_index=True)
    meta_title = models.CharField(max_length=255, blank=True)
    meta_description = models.CharField(max_length=320, blank=True)

    objects = CategoryQuerySet.as_manager()

    class Meta:
        verbose_name_plural = _("categories")
        ordering = ("sort_order", "name")
        constraints = [
            models.UniqueConstraint(
                fields=["parent", "name"], name="unique_category_name_per_parent"
            )
        ]
        indexes = [models.Index(fields=["is_active", "sort_order"], name="category_active_sort_idx")]

    def __str__(self):
        return self.full_path

    def get_absolute_url(self):
        return reverse("catalog:category", kwargs={"slug": self.slug})

    @property
    def full_path(self):
        """``Electronics > Laptops`` style breadcrumb label."""
        names, node, guard = [], self, 0
        while node is not None and guard < 10:
            names.append(node.name)
            node = node.parent
            guard += 1
        return " > ".join(reversed(names))

    def ancestors(self):
        chain, node, guard = [], self.parent, 0
        while node is not None and guard < 10:
            chain.append(node)
            node = node.parent
            guard += 1
        return list(reversed(chain))

    def descendant_ids(self):
        """All ids in this subtree, including self.

        Used to make a parent-category listing include products filed under
        its children.
        """
        ids, frontier, guard = [self.pk], [self.pk], 0
        while frontier and guard < 10:
            frontier = list(
                Category.objects.filter(parent_id__in=frontier).values_list("id", flat=True)
            )
            ids.extend(frontier)
            guard += 1
        return ids


class Brand(TimeStampedModel, SluggedModel):
    name = models.CharField(max_length=150, unique=True, db_index=True)
    description = models.TextField(blank=True)
    logo = models.ImageField(upload_to="brands/", blank=True, null=True)
    website = models.URLField(blank=True)
    is_active = models.BooleanField(default=True, db_index=True)
    is_featured = models.BooleanField(default=False)

    objects = ActiveManager()

    class Meta:
        ordering = ("name",)

    def __str__(self):
        return self.name

    def get_absolute_url(self):
        return f"{reverse('catalog:product_list')}?brand={self.slug}"


class ProductQuerySet(models.QuerySet):
    def active(self):
        return self.filter(is_active=True)

    def published(self):
        return self.filter(is_active=True, status=Product.Status.PUBLISHED)

    def with_related(self):
        """Standard eager-loading for listing/detail pages (kills N+1)."""
        return self.select_related("category", "brand").prefetch_related(
            "images", "variants__inventory"
        )

    def in_stock(self):
        return self.filter(variants__inventory__quantity_available__gt=0).distinct()

    def featured(self):
        return self.published().filter(is_featured=True)

    def best_sellers(self):
        return self.published().order_by("-sold_count", "-rating_average")

    def new_arrivals(self):
        return self.published().order_by("-created_at")


class Product(TimeStampedModel, SluggedModel):
    """A sellable product. Every product has one or more variants."""

    class Status(models.TextChoices):
        DRAFT = "draft", _("Draft")
        PUBLISHED = "published", _("Published")
        ARCHIVED = "archived", _("Archived")

    name = models.CharField(max_length=255, db_index=True)
    category = models.ForeignKey(
        Category, on_delete=models.PROTECT, related_name="products", db_index=True
    )
    brand = models.ForeignKey(
        Brand,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="products",
        db_index=True,
    )
    sku = models.CharField(max_length=64, unique=True, db_index=True)
    short_description = models.CharField(max_length=320, blank=True)
    description = models.TextField(blank=True)
    specifications = models.JSONField(
        default=dict,
        blank=True,
        help_text=_('Key/value spec sheet, e.g. {"RAM": "16 GB", "Screen": "14 inch"}.'),
    )

    # Money. ``price`` is what a customer pays; ``compare_at_price`` is the
    # struck-through MRP used to derive the discount percentage.
    price = models.DecimalField(
        max_digits=12, decimal_places=2, validators=[MinValueValidator(ZERO)], db_index=True
    )
    compare_at_price = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
        validators=[MinValueValidator(ZERO)],
        help_text=_("Original / MRP price. Leave blank when there is no discount."),
    )
    cost_price = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
        validators=[MinValueValidator(ZERO)],
        help_text=_("Internal purchase cost. Never exposed to customers."),
    )
    tax_rate_percent = models.DecimalField(
        max_digits=5, decimal_places=2, default=ZERO, validators=[MinValueValidator(ZERO)]
    )

    status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.DRAFT, db_index=True
    )
    is_active = models.BooleanField(default=True, db_index=True)
    is_featured = models.BooleanField(default=False, db_index=True)
    is_best_seller = models.BooleanField(default=False, db_index=True)
    is_returnable = models.BooleanField(default=True)
    is_cod_available = models.BooleanField(default=True)

    weight_grams = models.PositiveIntegerField(default=0)
    warranty = models.CharField(max_length=120, blank=True)
    tags = models.CharField(
        max_length=255, blank=True, help_text=_("Comma separated keywords used by search.")
    )

    # Denormalised counters, maintained by signals/services -- never by the
    # client. They exist so listing pages avoid aggregate queries per row.
    rating_average = models.DecimalField(max_digits=3, decimal_places=2, default=ZERO, db_index=True)
    rating_count = models.PositiveIntegerField(default=0)
    sold_count = models.PositiveIntegerField(default=0, db_index=True)
    view_count = models.PositiveIntegerField(default=0)

    video_url = models.URLField(
        blank=True,
        help_text=_("Product video (section 5). YouTube, Vimeo or a direct file."),
    )
    spin_url = models.URLField(blank=True, help_text=_("Optional 360-degree view."))
    meta_title = models.CharField(max_length=255, blank=True)
    meta_description = models.CharField(max_length=320, blank=True)
    published_at = models.DateTimeField(null=True, blank=True, db_index=True)

    objects = ProductQuerySet.as_manager()

    class Meta:
        ordering = ("-created_at",)
        indexes = [
            models.Index(fields=["status", "is_active", "-created_at"], name="product_live_idx"),
            models.Index(fields=["category", "price"], name="product_cat_price_idx"),
            models.Index(fields=["-sold_count"], name="product_sold_idx"),
            models.Index(fields=["-rating_average"], name="product_rating_idx"),
            models.Index(fields=["price"], name="product_price_idx"),
        ]

    def __str__(self):
        return self.name

    def get_absolute_url(self):
        return reverse("catalog:product_detail", kwargs={"slug": self.slug})

    # ---- pricing -------------------------------------------------------
    @property
    def has_discount(self):
        return bool(self.compare_at_price and self.compare_at_price > self.price)

    @property
    def discount_amount(self):
        if not self.has_discount:
            return ZERO
        return (self.compare_at_price - self.price).quantize(Decimal("0.01"))

    @property
    def discount_percent(self):
        if not self.has_discount or not self.compare_at_price:
            return 0
        return int(round((self.discount_amount / self.compare_at_price) * 100))

    # ---- media ---------------------------------------------------------
    @property
    def primary_image(self):
        """First image marked primary, else the first by sort order.

        Reads from the prefetched ``images`` cache when available so product
        grids stay at one query.
        """
        images = list(self.images.all())
        for image in images:
            if image.is_primary:
                return image
        return images[0] if images else None

    # ---- stock ---------------------------------------------------------
    @property
    def total_stock(self):
        return sum(v.available_quantity for v in self.variants.all() if v.is_active)

    @property
    def in_stock(self):
        return self.total_stock > 0

    @property
    def default_variant(self):
        variants = [v for v in self.variants.all() if v.is_active]
        if not variants:
            return None
        in_stock = [v for v in variants if v.available_quantity > 0]
        pool = in_stock or variants
        return sorted(pool, key=lambda v: (v.sort_order, v.pk))[0]

    @property
    def tag_list(self):
        return [t.strip() for t in self.tags.split(",") if t.strip()]

    @property
    def rating_percent(self):
        """Average rating as a 0-100 value, for CSS star-fill widths."""
        return int((self.rating_average / Decimal("5")) * 100) if self.rating_count else 0


#: See the note in apps/orders/models.py about enum naming.
PRODUCT_STATUS_CHOICES = Product.Status.choices


class ProductImage(TimeStampedModel):
    product = models.ForeignKey(
        Product, on_delete=models.CASCADE, related_name="images", db_index=True
    )
    image = models.ImageField(upload_to="products/%Y/%m/")
    alt_text = models.CharField(max_length=255, blank=True)
    is_primary = models.BooleanField(default=False)
    sort_order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ("-is_primary", "sort_order", "id")
        indexes = [models.Index(fields=["product", "-is_primary"], name="image_product_primary_idx")]

    def __str__(self):
        return f"Image for {self.product_id}"

    def save(self, *args, **kwargs):
        if self.is_primary:
            ProductImage.objects.filter(product=self.product, is_primary=True).exclude(
                pk=self.pk
            ).update(is_primary=False)
        super().save(*args, **kwargs)


class ProductVariant(TimeStampedModel):
    """A concrete buyable SKU (a size/colour combination of a product).

    Carts and orders always reference a variant, never a bare product, so
    pricing and stock have exactly one home.
    """

    product = models.ForeignKey(
        Product, on_delete=models.CASCADE, related_name="variants", db_index=True
    )
    sku = models.CharField(max_length=64, unique=True, db_index=True)
    name = models.CharField(
        max_length=120, blank=True, help_text=_("Display label, e.g. 'Blue / M'. Auto-built if blank.")
    )
    size = models.CharField(max_length=50, blank=True, db_index=True)
    color = models.CharField(max_length=50, blank=True, db_index=True)
    color_hex = models.CharField(max_length=7, blank=True, help_text=_("e.g. #1d4ed8"))
    extra_attributes = models.JSONField(default=dict, blank=True)

    # Absolute overrides -- blank means "inherit from the product".
    price_override = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True, validators=[MinValueValidator(ZERO)]
    )
    compare_at_price_override = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True, validators=[MinValueValidator(ZERO)]
    )

    image = models.ImageField(upload_to="variants/%Y/%m/", blank=True, null=True)

    # Physical attributes (MST spec section 6). Shipping is quoted on the
    # parcel, so weight belongs on the thing that actually goes in the box --
    # a size XXL saree does not weigh what a size XS one does. Each falls
    # back to the product's figure when left empty.
    weight_grams = models.PositiveIntegerField(
        blank=True,
        null=True,
        help_text=_("Shipping weight. Falls back to the product's weight."),
    )
    length_mm = models.PositiveIntegerField(blank=True, null=True)
    width_mm = models.PositiveIntegerField(blank=True, null=True)
    height_mm = models.PositiveIntegerField(blank=True, null=True)
    barcode = models.CharField(
        max_length=64,
        blank=True,
        db_index=True,
        help_text=_("EAN, UPC or GTIN, as printed on the item."),
    )

    is_active = models.BooleanField(default=True, db_index=True)
    is_default = models.BooleanField(default=False)
    sort_order = models.PositiveIntegerField(default=0)

    @property
    def shipping_weight_grams(self):
        """What this variant weighs in a parcel.

        Returns zero rather than None when nothing is recorded, so a rate
        table lookup lands in the lightest band instead of raising. A quote
        of "we do not know" is not useful at checkout.
        """
        if self.weight_grams:
            return self.weight_grams
        return getattr(self.product, "weight_grams", 0) or 0

    objects = ActiveManager()

    class Meta:
        ordering = ("sort_order", "id")
        constraints = [
            models.UniqueConstraint(
                fields=["product", "size", "color"], name="unique_variant_per_product_options"
            )
        ]
        indexes = [models.Index(fields=["product", "is_active"], name="variant_product_active_idx")]

    def __str__(self):
        return f"{self.product.name} - {self.label}"

    def save(self, *args, **kwargs):
        if not self.name:
            self.name = " / ".join(p for p in (self.color, self.size) if p)
        super().save(*args, **kwargs)

    @property
    def label(self):
        return self.name or " / ".join(p for p in (self.color, self.size) if p) or "Default"

    # ---- pricing -------------------------------------------------------
    @property
    def price(self):
        """Effective selling price -- the single source of truth for money."""
        return self.price_override if self.price_override is not None else self.product.price

    @property
    def compare_at_price(self):
        if self.compare_at_price_override is not None:
            return self.compare_at_price_override
        return self.product.compare_at_price

    @property
    def has_discount(self):
        cap = self.compare_at_price
        return bool(cap and cap > self.price)

    @property
    def discount_percent(self):
        cap = self.compare_at_price
        if not cap or cap <= self.price:
            return 0
        return int(round(((cap - self.price) / cap) * 100))

    # ---- stock ---------------------------------------------------------
    @property
    def available_quantity(self):
        """Sellable units. Zero when no inventory row exists yet."""
        inventory = getattr(self, "inventory", None)
        return inventory.sellable_quantity if inventory else 0

    @property
    def in_stock(self):
        return self.available_quantity > 0

    @property
    def is_low_stock(self):
        inventory = getattr(self, "inventory", None)
        return bool(inventory and inventory.is_low_stock)

    @property
    def display_image(self):
        return self.image if self.image else None


# Attributes and size guides live in their own module for readability; Django
# needs them importable from models.py to discover them.
from apps.catalog.attributes import (  # noqa: E402,F401  isort:skip
    Attribute,
    AttributeValue,
    ProductAttribute,
    SizeGuide,
)
