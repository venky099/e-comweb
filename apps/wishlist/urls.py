"""Wishlist URLs."""
from django.urls import path

from . import views

app_name = "wishlist"

urlpatterns = [
    path("", views.WishlistView.as_view(), name="detail"),
    path("toggle/", views.toggle, name="toggle"),
    path("clear/", views.clear, name="clear"),
    path("move-all/", views.move_all_to_cart, name="move_all_to_cart"),
    path("<int:item_id>/remove/", views.remove, name="remove"),
    path("<int:item_id>/move/", views.move_to_cart, name="move_to_cart"),
]
