"""Navigation context (category mega-menu)."""
from django.conf import settings
from django.core.cache import cache

from .models import Category

NAV_CACHE_KEY = "nav:categories:v1"


def get_navigation_categories():
    """Root categories with their children, cached.

    The menu renders on every page, so it is built once and cached rather than
    queried per request. ``apps.catalog.signals`` busts the key on any category
    save/delete.
    """
    cached = cache.get(NAV_CACHE_KEY)
    if cached is not None:
        return cached

    roots = list(
        Category.objects.active()
        .roots()
        .prefetch_related("children")
        .order_by("sort_order", "name")[:12]
    )
    # Materialise children now so templates never hit the DB inside a loop.
    data = [
        {
            "name": root.name,
            "slug": root.slug,
            "icon_class": root.icon_class,
            "children": [
                {"name": c.name, "slug": c.slug}
                for c in sorted(root.children.all(), key=lambda c: (c.sort_order, c.name))
                if c.is_active
            ][:12],
        }
        for root in roots
    ]
    cache.set(NAV_CACHE_KEY, data, settings.CACHE_TTL_MEDIUM)
    return data


def navigation(request):
    # The admin and the API do not render the storefront chrome.
    path = request.path
    if path.startswith("/api/") or "admin" in path.split("/")[:2]:
        return {}
    return {"nav_categories": get_navigation_categories()}
