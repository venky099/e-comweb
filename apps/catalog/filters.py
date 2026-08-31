"""Product listing filters and sorting.

A single Django ``Form`` bound to the querystring drives both the sidebar UI
and the queryset, so what the shopper sees and what the database returns can
never disagree.
"""
from decimal import Decimal, InvalidOperation

from django import forms
from django.db.models import Count, F, Max, Min, Q
from django.utils.translation import gettext_lazy as _

from .models import Attribute, AttributeValue, Brand, Category, ProductVariant

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
    """Bound to ``request.GET``; every field is optional.

    Prices are entered in whatever currency the visitor is browsing in, but
    products are stored in the base currency, so the bounds are converted
    back before they reach the query. Without that, someone browsing in
    dollars who asks for "under 100" would silently be filtering for under
    100 rupees.
    """

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

    def __init__(self, *args, locale=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.locale = locale

    def _attribute_selections(self):
        """Read ?fabric=silk,cotton for every attribute an admin has defined.

        Declared fields cannot cover these: which parameters exist is decided
        in the database, not in this file. Unknown parameters are ignored
        rather than erroring, so a stale bookmark still loads the page.
        """
        if not self.data:
            return {}
        codes = set(
            Attribute.objects.filter(is_active=True, is_filterable=True).values_list(
                "code", flat=True
            )
        )
        selections = {}
        for code in codes:
            # Plain checkboxes post the parameter once per ticked box; a link
            # or a bookmark may instead comma-join them. Accept both, so the
            # sidebar needs no JavaScript to work.
            raw = (
                self.data.getlist(code)
                if hasattr(self.data, "getlist")
                else [self.data.get(code) or ""]
            )
            slugs = [
                part.strip()
                for chunk in raw
                for part in str(chunk).split(",")
                if part.strip()
            ]
            if slugs:
                selections[code] = slugs
        return selections

    @property
    def selected_attribute_slugs(self):
        """Every selected attribute value, for ticking the sidebar boxes."""
        if not self.is_valid():
            return set()
        picked = set()
        for slugs in (self.cleaned_data.get("attributes") or {}).values():
            picked.update(slugs)
        return picked

    def to_base(self, value):
        """Convert a visitor-entered amount into the base currency."""
        rate = getattr(self.locale, "rate", None)
        if not rate or Decimal(rate) == 1:
            return value
        return (Decimal(value) / Decimal(rate)).quantize(Decimal("0.01"))

    def for_display(self, value):
        """Render a stored base amount the way the visitor entered it."""
        if self.locale is None:
            return value
        return self.locale.display(value)

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
        if value < 0:
            return None
        return self.to_base(value)

    def clean(self):
        cleaned = super().clean()
        low, high = cleaned.get("min_price"), cleaned.get("max_price")
        # A reversed range is a user slip, not an error worth a red box.
        if low is not None and high is not None and low > high:
            cleaned["min_price"], cleaned["max_price"] = high, low
        # Attribute filters are not declared fields -- which ones exist is
        # decided in the database -- so they are collected here.
        cleaned["attributes"] = self._attribute_selections()
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
            chips.append(("min_price", _("Min price"), self.for_display(data["min_price"])))
        if data.get("max_price") is not None:
            chips.append(("max_price", _("Max price"), self.for_display(data["max_price"])))
        if data.get("rating"):
            chips.append(("rating", _("Rating"), f"{data['rating']}+ stars"))
        if data.get("size"):
            chips.append(("size", _("Size"), data["size"]))
        if data.get("color"):
            chips.append(("color", _("Colour"), data["color"]))
        if data.get("availability"):
            chips.append(("availability", _("Availability"), data["availability"]))
        for code, slugs in (data.get("attributes") or {}).items():
            attribute = Attribute.objects.filter(code=code).first()
            label = attribute.name if attribute else code.title()
            names = list(
                AttributeValue.objects.filter(
                    attribute__code=code, slug__in=slugs
                ).values_list("value", flat=True)
            )
            chips.append((code, label, ", ".join(names) or ", ".join(slugs)))
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

    # Attribute filters arrive as ?fabric=silk,cotton -- one query parameter
    # per attribute, whatever the administrator has defined.
    attribute_filters = data.get("attributes") or {}
    for code, slugs in attribute_filters.items():
        if not slugs:
            continue
        # Values of the *same* attribute widen the result (silk OR cotton);
        # different attributes narrow it (silk AND festive). Chaining one
        # filter() per attribute is what produces that.
        queryset = queryset.filter(
            attribute_values__attribute__code=code,
            attribute_values__value__slug__in=slugs,
        )

    if sizes or colors or attribute_filters or availability == "in_stock":
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
        "attributes": attribute_facets(product_ids),
        "price_min": price_range["low"] or 0,
        "price_max": price_range["high"] or 0,
    }


def attribute_facets(product_ids):
    """Filterable attributes, with only the values these products actually use.

    Offering a value nothing matches is a dead end -- the shopper picks it and
    gets an empty page -- so values with no products are left out.
    """
    facets = []
    attributes = (
        Attribute.objects.filter(is_active=True, is_filterable=True)
        .prefetch_related("values")
        .order_by("sort_order", "name")
    )
    for attribute in attributes:
        values = list(
            AttributeValue.objects.filter(
                attribute=attribute, products__product_id__in=product_ids
            )
            .annotate(count=Count("products", distinct=True))
            .order_by("sort_order", "value")
            .values("value", "slug", "count")[:40]
        )
        if values:
            facets.append(
                {"name": attribute.name, "code": attribute.code, "values": values}
            )
    return facets


def size_guide_for(product):
    """The most specific active size guide for a product, or None.

    A brand's own guide beats its category's, because sizing varies more
    between brands than between garment types. A guide with no rows is
    treated as absent -- an empty table reads as a broken page.
    """
    from apps.catalog.models import SizeGuide

    candidates = SizeGuide.objects.filter(is_active=True).filter(
        Q(brand_id=product.brand_id, brand__isnull=False)
        | Q(category_id__in=[product.category_id], category__isnull=False)
    )
    ranked = sorted(
        (g for g in candidates if g.is_usable),
        key=lambda g: 0 if g.brand_id else 1,
    )
    return ranked[0] if ranked else None
