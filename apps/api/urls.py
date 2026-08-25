"""REST API URL configuration (mounted at /api/).

Everything lives under ``/api/v1/`` with an unversioned alias kept for the
endpoint paths named in the project spec.
"""
from django.urls import include, path
from drf_spectacular.views import (
    SpectacularAPIView,
    SpectacularRedocView,
    SpectacularSwaggerView,
)
from rest_framework.routers import DefaultRouter
from rest_framework_simplejwt.views import TokenRefreshView, TokenVerifyView

from . import views

router = DefaultRouter()
router.register("products", views.ProductViewSet, basename="product")
router.register("categories", views.CategoryViewSet, basename="category")
router.register("brands", views.BrandViewSet, basename="brand")
router.register("cart", views.CartViewSet, basename="cart")
router.register("wishlist", views.WishlistViewSet, basename="wishlist")
router.register("orders", views.OrderViewSet, basename="order")
router.register("returns", views.ReturnRequestViewSet, basename="return")
router.register("reviews", views.ReviewViewSet, basename="review")
router.register("addresses", views.AddressViewSet, basename="address")
router.register("coupons", views.CouponViewSet, basename="coupon")
router.register("customers", views.CustomerViewSet, basename="customer")
router.register("banners", views.BannerViewSet, basename="banner")
router.register("offers", views.OfferViewSet, basename="offer")
router.register("flash-sales", views.FlashSaleViewSet, basename="flashsale")

auth_patterns = [
    path("register/", views.RegisterView.as_view(), name="register"),
    path("login/", views.LoginView.as_view(), name="login"),
    path("logout/", views.LogoutView.as_view(), name="logout"),
    path("refresh/", TokenRefreshView.as_view(), name="token_refresh"),
    path("verify/", TokenVerifyView.as_view(), name="token_verify"),
    path("me/", views.MeView.as_view(), name="me"),
    path("change-password/", views.ChangePasswordView.as_view(), name="change_password"),
]

v1_patterns = [
    path("auth/", include((auth_patterns, "auth"))),
    path("dashboard/stats/", views.DashboardStatsView.as_view(), name="dashboard_stats"),
    path(
        "dashboard/charts/<str:chart>/",
        views.DashboardChartView.as_view(),
        name="dashboard_chart",
    ),
    path("", include(router.urls)),
]

app_name = "api"

urlpatterns = [
    # ---- OpenAPI schema & docs ----
    path("schema/", SpectacularAPIView.as_view(), name="schema"),
    path(
        "docs/",
        SpectacularSwaggerView.as_view(url_name="api:schema"),
        name="swagger-ui",
    ),
    path("redoc/", SpectacularRedocView.as_view(url_name="api:schema"), name="redoc"),
    # ---- versioned API ----
    path("v1/", include((v1_patterns, "v1"))),
    # ---- unversioned alias (the paths named in the spec) ----
    path("", include((v1_patterns, "default"))),
]
