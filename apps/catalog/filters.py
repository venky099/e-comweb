"""Product listing filters and sorting.

A single Django ``Form`` bound to the querystring drives both the sidebar UI
and the queryset, so what the shopper sees and what the database returns can
never disagree.
"""
from decimal import Decimal, InvalidOperation

from django import forms
from django.db.models import Count, F, Max, Min
from django.utils.translation import gettext_lazy as _

from .models import Brand, Category, ProductVariant

SORT_OPTIONS = [
    ("relevance", _("Relevance")),
    ("newest", _("Newest first")),
    ("price_asc", _("Price: low to high")),
    ("price_desc", _("Price: high to low")),
    ("rating", _("Customer rating")),
    ("popularity", _("Popularity")),
    ("discount", _("Biggest discount")),
]

SORT_EXPRESSIONS = {
    "relevance": ("-search_rank", "-sold_count"),
    "newest": ("-published_at", "-created_at"),
    "price_asc": ("price", "-rating_average"),
    "price_desc": ("-price", "-rating_average"),
    "rating": ("-rating_average", "-rating_count"),
    "popularity": ("-sold_count", "-view_count"),
    "discount": ("-discount_value", "-sold_count"),
}


class ProductFilterForm(forms.Form):
    """Bound to ``request.GET``; every field is optional."""

    q = forms.CharField(required=False, label=_("Search"))
    category = forms.CharField(required=False)
    brand = forms.CharField(required=False)
    min_price = forms.DecimalField(required=False, min_value=0)
    max_price = forms.DecimalField(required=False, min_value=0)
    rating = forms.IntegerField(required=False, min_value=1, max_value=5)
    size = forms.CharField(required=False)
    color = forms.CharField(required=False)
    availability = forms.ChoiceField(
        required=False,
        choices=[("", _("All")), ("in_stock", _("In stock")), ("on_sale", _("On sale"))],
    )
    sort = forms.ChoiceField(required=False, choices=SORT_OPTIONS)

    def clean_min_price(self):
        return self._clean_price("min_price")

    def clean_max_price(self):
        return self._clean_price("max_price")

    def _clean_price(self, field):
        value = self.cleaned_data.get(field)
        if value in (None, ""):
            return None
        try:
            value = Decimal(value)
        except (TypeError, InvalidOperation):
            return None
        return value if value >= 0 else None

    def clean(self):
        cleaned = super().clean()
        low, high = cleaned.get("min_price"), cleaned.get("max_price")
        # A reversed range is a user slip, not an error worth a red box.
        if low is not None and high is not None and low > high:
            cleaned["min_price"], cleaned["max_price"] = high, low
        return cleaned

    @property
    def active_filters(self):
        """Chips shown above the grid, as ``(field, label, value)`` tuples."""
        if not self.is_valid():
            return []
        data = self.cleaned_data
        chips = []
        if data.get("category"):
            chips.append(("category", _("Category"), data["category"]))
        if data.get("brand"):
            chips.append(("brand", _("Brand"), data["brand"]))
        if data.get("min_price") is not None:
            chips.append(("min_price", _("Min price"), data["min_price"]))
        if data.get("max_price") is not None:
            chips.append(("max_price", _("Max price"), data["max_price"]))
        if data.get("rating"):
            chips.append(("rating", _("Rating"), f"{data['rating']}+ stars"))
        if data.get("size"):
            chips.append(("size", _("Size"), data["size"]))
        if data.get("color"):
            chips.append(("color", _("Colour"), data["color"]))
        if data.get("availability"):
            chips.append(("availability", _("Availability"), data["availability"]))
        return chips


def apply_filters(queryset, data):
    """Narrow ``queryset`` using cleaned filter-form data."""
    category_slug = data.get("category")
    if category_slug:
        category = Category.objects.filter(slug=category_slug, is_active=True).first()
        if category:
            # Include the whole subtree so "Electronics" shows laptops too.
            queryset = queryset.filter(category_id__in=category.descendant_ids())

    brand_slugs = [s for s in (data.get("brand") or "").split(",") if s.strip()]
    if brand_slugs:
        queryset = queryset.filter(brand__slug__in=brand_slugs)

    if data.get("min_price") is not None:
        queryset = queryset.filter(price__gte=data["min_price"])
    if data.get("max_price") is not None:
        queryset = queryset.filter(price__lte=data["max_price"])

    if data.get("rating"):
        queryset = queryset.filter(rating_average__gte=data["rating"])

    sizes = [s for s in (data.get("size") or "").split(",") if s.strip()]
    if sizes:
        queryset = queryset.filter(variants__size__in=sizes, variants__is_active=True)

    colors = [c for c in (data.get("color") or "").split(",") if c.strip()]
    if colors:
        queryset = queryset.filter(variants__color__in=colors, variants__is_active=True)

    availability = data.get("availability")
    if availability == "in_stock":
        queryset = queryset.filter(
            variants__is_active=True,
            variants__inventory__quantity_available__gt=F("variants__inventory__quantity_reserved"),
        )
    elif availability == "on_sale":
        queryset = queryset.filter(compare_at_price__gt=F("price"))

    if sizes or colors or availability == "in_stock":
        queryset = queryset.distinct()

    return queryset


def apply_sorting(queryset, sort_key, has_search_term=False):
    """Order the queryset, defaulting sensibly when there is no search term."""
    if not sort_key or sort_key not in SORT_EXPRESSIONS:
        sort_key = "relevance" if has_search_term else "popularity"

    if sort_key == "discount":
        queryset = queryset.annotate(
            discount_value=F("compare_at_price") - F("price")
        )

    return queryset.order_by(*SORT_EXPRESSIONS[sort_key]), sort_key


def facet_options(base_queryset):
    """Build the sidebar's option lists from the products actually available.

    Counts come from the unfiltered listing queryset so a shopper can always
    see (and undo) a filter that would otherwise leave zero results.
    """
    product_ids = base_queryset.values_list("id", flat=True)

    brands = list(
        Brand.objects.filter(products__id__in=product_ids)
        .annotate(product_count=Count("products", distinct=True))
        .order_by("name")
        .values("name", "slug", "product_count")[:40]
    )

    variant_qs = ProductVariant.objects.filter(product_id__in=product_ids, is_active=True)
    sizes = list(
        variant_qs.exclude(size="")
        .values("size")
        .annotate(count=Count("id"))
        .order_by("size")[:30]
    )
    colors = list(
        variant_qs.exclude(color="")
        .values("color", "color_hex")
        .annotate(count=Count("id"))
        .order_by("color")[:30]
    )

    price_range = base_queryset.aggregate(low=Min("price"), high=Max("price"))

    return {
        "brands": brands,
        "sizes": sizes,
        "colors": colors,
        "price_min": price_range["low"] or 0,
        "price_max": price_range["high"] or 0,
    }
