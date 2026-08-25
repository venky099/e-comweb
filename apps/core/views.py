"""Storefront home, static pages and the custom error handlers."""
from django.conf import settings
from django.core.cache import cache
from django.shortcuts import render
from django.views.generic import TemplateView

from django.db.models import Count, Q

from apps.catalog.models import Brand, Category, Product
from apps.coupons.models import Coupon
from apps.marketing.models import Banner, FlashSale, Offer
from apps.reviews.models import Review


class HomeView(TemplateView):
    """Storefront landing page.

    Every block is a real queryset with explicit eager loading -- the page
    stays at a fixed, small number of queries no matter how many products
    are on it.
    """

    template_name = "core/home.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        live = Product.objects.published().with_related()

        context["hero_banners"] = Banner.objects.live().filter(
            position=Banner.Position.HERO
        )[:5]
        context["strip_banners"] = Banner.objects.live().filter(
            position=Banner.Position.STRIP
        )[:3]
        context["offers"] = Offer.objects.live().select_related("coupon")[:6]
        context["public_coupons"] = Coupon.objects.public()[:4]

        context["featured_products"] = live.filter(is_featured=True)[:8]
        context["new_arrivals"] = live.order_by("-published_at", "-created_at")[:8]
        context["best_sellers"] = live.order_by("-sold_count", "-rating_average")[:8]

        flash_sale = (
            FlashSale.objects.live().prefetch_related("items__variant__product__images").first()
        )
        context["flash_sale"] = flash_sale
        context["flash_sale_items"] = list(flash_sale.live_items()[:8]) if flash_sale else []

        context["featured_categories"] = self.get_featured_categories()
        context["top_brands"] = self.get_top_brands()

        context["testimonials"] = (
            Review.objects.approved()
            .filter(rating__gte=4)
            .exclude(comment="")
            .select_related("user", "product")
            .order_by("-helpful_count", "-created_at")[:9]
        )
        return context

    def get_top_brands(self, limit=12):
        """Brands that actually have something to sell, most-stocked first."""
        key = "home:brands:v1"
        cached = cache.get(key)
        if cached is not None:
            return cached
        brands = list(
            Brand.objects.active()
            .annotate(
                live_products=Count(
                    "products", filter=Q(products__status=Product.Status.PUBLISHED)
                )
            )
            .filter(live_products__gt=0)
            .order_by("-is_featured", "-live_products", "name")[:limit]
        )
        cache.set(key, brands, settings.CACHE_TTL_MEDIUM)
        return brands

    def get_featured_categories(self):
        """Cached tiles -- categories change far less often than page views."""
        key = "home:categories:v1"
        cached = cache.get(key)
        if cached is not None:
            return cached
        categories = list(
            Category.objects.active().filter(is_featured=True).order_by("sort_order", "name")[:8]
        )
        if not categories:
            categories = list(Category.objects.active().roots().order_by("sort_order", "name")[:8])
        cache.set(key, categories, settings.CACHE_TTL_MEDIUM)
        return categories


class StaticPageView(TemplateView):
    """About / contact / policy pages."""

    page_title = ""

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["page_title"] = self.page_title
        return context


class AboutView(StaticPageView):
    template_name = "core/about.html"
    page_title = "About us"


class ContactView(StaticPageView):
    template_name = "core/contact.html"
    page_title = "Contact us"


class PolicyView(StaticPageView):
    """Shipping / returns / privacy / terms, chosen by URL kwarg."""

    template_name = "core/policy.html"

    POLICIES = {
        "shipping": "Shipping policy",
        "returns": "Returns & refunds",
        "privacy": "Privacy policy",
        "terms": "Terms & conditions",
    }

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        slug = self.kwargs.get("slug", "terms")
        context["policy_slug"] = slug
        context["page_title"] = self.POLICIES.get(slug, "Policy")
        return context


# ---------------------------------------------------------------------------
# Error handlers
# ---------------------------------------------------------------------------
# Registered in config/urls.py. Each renders a branded page and returns the
# correct status code -- API paths get JSON instead of HTML.


def _wants_json(request):
    return request.path.startswith("/api/") or request.headers.get(
        "Accept", ""
    ).startswith("application/json")


def _error_response(request, status_code, template, title, message):
    from django.http import JsonResponse

    if _wants_json(request):
        return JsonResponse(
            {"error": {"type": title, "message": message, "status_code": status_code}},
            status=status_code,
        )
    return render(
        request,
        template,
        {"error_title": title, "error_message": message, "status_code": status_code},
        status=status_code,
    )


def bad_request(request, exception=None):
    return _error_response(
        request,
        400,
        "errors/400.html",
        "Bad request",
        "We could not understand that request.",
    )


def permission_denied(request, exception=None):
    return _error_response(
        request,
        403,
        "errors/403.html",
        "Access denied",
        "You do not have permission to view this page.",
    )


def page_not_found(request, exception=None):
    return _error_response(
        request,
        404,
        "errors/404.html",
        "Page not found",
        "The page you are looking for has moved or no longer exists.",
    )


def server_error(request):
    return _error_response(
        request,
        500,
        "errors/500.html",
        "Something went wrong",
        "Our team has been notified. Please try again in a moment.",
    )
