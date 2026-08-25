"""Coupon URLs."""
from django.urls import path

from . import views

app_name = "coupons"

urlpatterns = [
    path("apply/", views.apply_coupon, name="apply"),
    path("remove/", views.remove_coupon, name="remove"),
    path("available/", views.available_coupons, name="available"),
]
