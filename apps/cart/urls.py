"""Cart URLs."""
from django.urls import path

from . import views

app_name = "cart"

urlpatterns = [
    path("", views.CartDetailView.as_view(), name="detail"),
    path("add/", views.add, name="add"),
    path("mini/", views.mini_cart, name="mini"),
    path("clear/", views.clear, name="clear"),
    path("<int:item_id>/update/", views.update, name="update"),
    path("<int:item_id>/increment/", views.increment, name="increment"),
    path("<int:item_id>/decrement/", views.decrement, name="decrement"),
    path("<int:item_id>/remove/", views.remove, name="remove"),
]
