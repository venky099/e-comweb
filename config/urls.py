"""Root URL configuration.

Storefront pages live at the root, the REST API under ``/api/v1/``, and the
Django admin behind a configurable (non-default in production) path.
"""
from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path

# Overridable so production can hide the default /admin/ location.
ADMIN_URL = getattr(settings, "ADMIN_URL", "admin/")

urlpatterns = [
    path(ADMIN_URL, admin.site.urls),
    # ---- REST API layer ----
    path("api/", include("apps.api.urls")),
    # ---- Server-rendered storefront ----
    path("accounts/", include("apps.accounts.urls")),
    path("locale/", include("apps.geo.urls")),
    path("products/", include("apps.catalog.urls")),
    path("cart/", include("apps.cart.urls")),
    path("wishlist/", include("apps.wishlist.urls")),
    path("orders/", include("apps.orders.urls")),
    path("payments/", include("apps.payments.urls")),
    path("reviews/", include("apps.reviews.urls")),
    path("coupons/", include("apps.coupons.urls")),
    path("staff/", include("apps.dashboard.urls")),
    path("", include("apps.core.urls")),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)

# Custom error handlers (see apps/core/views.py).
handler400 = "apps.core.views.bad_request"
handler403 = "apps.core.views.permission_denied"
handler404 = "apps.core.views.page_not_found"
handler500 = "apps.core.views.server_error"

admin.site.site_header = f"{settings.SITE_NAME} Administration"
admin.site.site_title = f"{settings.SITE_NAME} Admin"
admin.site.index_title = "Store operations"
