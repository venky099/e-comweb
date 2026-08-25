"""Product listing, detail and search views."""
from django.db.models import Avg, Count, F, Prefetch, Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, render
from django.views.generic import DetailView, ListView

from apps.reviews.models import Review

from .filters import ProductFilterForm, apply_filters, apply_sorting, facet_options
from .models import Category, Product, ProductVariant
from .search import autocomplete_suggestions, search_products


class ProductListView(ListView):
    """Filterable, sortable, paginated product grid.

    Also serves ``/products/category/<slug>/`` and ``/search/``; those views
    subclass this and only change the starting queryset or the page title.
    """

    template_name = "catalog/product_list.html"
    context_object_name = "products"
    paginate_by = 24

    def get_base_queryset(self):
        return Product.objects.published().with_related()

    def get_filter_data(self):
        form = ProductFilterForm(self.request.GET or None)
        self.filter_form = form
        return form.cleaned_data if form.is_valid() else {}

    def get_queryset(self):
        data = self.get_filter_data()
        queryset = self.get_base_queryset()

        term = (data.get("q") or "").strip()
        self.search_term = term
        if term:
            queryset = search_products(queryset, term)

        # Facets are built from the pre-filter set so counts stay stable.
        self.unfiltered_queryset = queryset

        queryset = apply_filters(queryset, data)
        queryset, self.sort_key = apply_sorting(queryset, data.get("sort"), bool(term))
        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["filter_form"] = self.filter_form
        context["search_term"] = getattr(self, "search_term", "")
        context["sort_key"] = getattr(self, "sort_key", "popularity")
        context["facets"] = facet_options(self.unfiltered_queryset)
        context["categories"] = Category.objects.active().roots().order_by("sort_order", "name")
        context["active_filters"] = self.filter_form.active_filters
        context["querystring"] = self.build_querystring()
        context["result_count"] = context["paginator"].count if context.get("paginator") else 0
        return context

    def build_querystring(self):
        """Current filters minus ``page``, for pagination links."""
        params = self.request.GET.copy()
        params.pop("page", None)
        encoded = params.urlencode()
        return f"&{encoded}" if encoded else ""

    def render_to_response(self, context, **response_kwargs):
        # HTMX filter/sort changes swap only the grid, not the whole page.
        if self.request.headers.get("HX-Request"):
            self.template_name = "catalog/partials/product_grid.html"
        return super().render_to_response(context, **response_kwargs)


class CategoryDetailView(ProductListView):
    """Products inside one category (including its subcategories)."""

    def get_base_queryset(self):
        self.category = get_object_or_404(
            Category.objects.active(), slug=self.kwargs["slug"]
        )
        return (
            Product.objects.published()
            .with_related()
            .filter(category_id__in=self.category.descendant_ids())
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["category"] = self.category
        context["breadcrumbs"] = self.category.ancestors()
        context["subcategories"] = self.category.children.filter(is_active=True).order_by(
            "sort_order", "name"
        )
        return context


class SearchView(ProductListView):
    """Dedicated search results page."""

    template_name = "catalog/search_results.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["is_search_page"] = True
        return context


class ProductDetailView(DetailView):
    """Product page: gallery, variants, specs, reviews and recommendations."""

    template_name = "catalog/product_detail.html"
    context_object_name = "product"

    def get_queryset(self):
        return (
            Product.objects.published()
            .select_related("category", "brand")
            .prefetch_related(
                "images",
                Prefetch(
                    "variants",
                    queryset=ProductVariant.objects.filter(is_active=True)
                    .select_related("inventory")
                    .order_by("sort_order", "id"),
                ),
            )
        )

    def get_object(self, queryset=None):
        product = super().get_object(queryset)
        # Counter bump via F() so concurrent views cannot clobber each other.
        Product.objects.filter(pk=product.pk).update(view_count=F("view_count") + 1)
        return product

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        product = self.object
        variants = list(product.variants.all())

        context["variants"] = variants
        context["selected_variant"] = self.resolve_selected_variant(variants)
        context["sizes"] = sorted({v.size for v in variants if v.size})
        context["colors"] = sorted(
            {(v.color, v.color_hex) for v in variants if v.color}, key=lambda c: c[0]
        )
        context["variant_matrix"] = [
            {
                "id": v.id,
                "size": v.size,
                "color": v.color,
                "price": str(v.price),
                "compare_at_price": str(v.compare_at_price or ""),
                "discount_percent": v.discount_percent,
                "available": v.available_quantity,
                "label": v.label,
                "image": v.image.url if v.image else "",
            }
            for v in variants
        ]

        context["breadcrumbs"] = product.category.ancestors() + [product.category]
        context["review_stats"] = self.get_review_stats(product)
        context["reviews"] = (
            Review.objects.for_product(product).with_author().order_by("-created_at")[:10]
        )
        context["can_review"] = self.user_can_review(product)
        context["related_products"] = self.get_related_products(product)
        context["in_wishlist"] = self.is_in_wishlist(product)
        context["specifications"] = list((product.specifications or {}).items())
        return context

    def resolve_selected_variant(self, variants):
        """Honour ``?variant=<id>`` when valid, else the product default."""
        requested = self.request.GET.get("variant")
        if requested:
            for variant in variants:
                if str(variant.pk) == str(requested):
                    return variant
        return self.object.default_variant

    def get_review_stats(self, product):
        """Average plus the 1-5 star histogram, in one query."""
        rows = (
            Review.objects.for_product(product)
            .values("rating")
            .annotate(count=Count("id"))
            .order_by("-rating")
        )
        counts = {row["rating"]: row["count"] for row in rows}
        total = sum(counts.values())
        distribution = [
            {
                "stars": stars,
                "count": counts.get(stars, 0),
                "percent": int((counts.get(stars, 0) / total) * 100) if total else 0,
            }
            for stars in range(5, 0, -1)
        ]
        average = Review.objects.for_product(product).aggregate(avg=Avg("rating"))["avg"] or 0
        return {
            "average": round(average, 2),
            "total": total,
            "distribution": distribution,
            "percent": int((average / 5) * 100),
        }

    def user_can_review(self, product):
        """True when this user has a delivered line for the product."""
        user = self.request.user
        if not user.is_authenticated:
            return False
        from apps.reviews.services import can_review_product

        return can_review_product(user, product)[0]

    def is_in_wishlist(self, product):
        user = self.request.user
        if not user.is_authenticated:
            return False
        from apps.wishlist.models import WishlistItem

        return WishlistItem.objects.filter(wishlist__user=user, product=product).exists()

    def get_related_products(self, product, limit=8):
        """Same category first, topped up with the brand's other products."""
        related = list(
            Product.objects.published()
            .with_related()
            .filter(category=product.category)
            .exclude(pk=product.pk)
            .order_by("-sold_count", "-rating_average")[:limit]
        )
        if len(related) < limit and product.brand_id:
            seen = {p.pk for p in related} | {product.pk}
            filler = (
                Product.objects.published()
                .with_related()
                .filter(brand_id=product.brand_id)
                .exclude(pk__in=seen)
                .order_by("-sold_count")[: limit - len(related)]
            )
            related.extend(filler)
        return related


def variant_stock(request, pk):
    """JSON stock/price for one variant -- powers the detail-page selector.

    The browser asks the server for price and availability rather than
    computing them, so the figure shown is always the figure charged.
    """
    variant = get_object_or_404(
        ProductVariant.objects.select_related("product", "inventory"),
        pk=pk,
        is_active=True,
        product__is_active=True,
    )
    return JsonResponse(
        {
            "id": variant.pk,
            "label": variant.label,
            "price": str(variant.price),
            "compare_at_price": str(variant.compare_at_price or ""),
            "discount_percent": variant.discount_percent,
            "available_quantity": variant.available_quantity,
            "in_stock": variant.in_stock,
            "is_low_stock": variant.is_low_stock,
            "image": variant.image.url if variant.image else "",
        }
    )


def search_autocomplete(request):
    """HTMX-powered suggestion dropdown for the header search box."""
    term = request.GET.get("q", "")
    products = autocomplete_suggestions(term=term)
    categories = (
        Category.objects.active().filter(name__icontains=term)[:4] if len(term) >= 2 else []
    )
    return render(
        request,
        "catalog/partials/search_suggestions.html",
        {"suggestions": products, "categories": categories, "term": term},
    )


class BrandListView(ListView):
    """All brands, with a live product count each."""

    template_name = "catalog/brand_list.html"
    context_object_name = "brands"

    def get_queryset(self):
        from .models import Brand

        return (
            Brand.objects.active()
            .annotate(
                product_count=Count(
                    "products", filter=Q(products__status=Product.Status.PUBLISHED)
                )
            )
            .filter(product_count__gt=0)
            .order_by("name")
        )
