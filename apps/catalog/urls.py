"""Catalog URLs."""
from django.urls import path

from . import views

app_name = "catalog"

urlpatterns = [
    path("", views.ProductListView.as_view(), name="product_list"),
    path("search/", views.SearchView.as_view(), name="search"),
    path("search/suggestions/", views.search_autocomplete, name="search_suggestions"),
    path("brands/", views.BrandListView.as_view(), name="brand_list"),
    path("category/<slug:slug>/", views.CategoryDetailView.as_view(), name="category"),
    path("variant/<int:pk>/stock/", views.variant_stock, name="variant_stock"),
    path("<slug:slug>/", views.ProductDetailView.as_view(), name="product_detail"),
]
