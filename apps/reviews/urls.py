"""Review URLs."""
from django.urls import path

from . import views

app_name = "reviews"

urlpatterns = [
    path("mine/", views.MyReviewsView.as_view(), name="mine"),
    path("<int:pk>/helpful/", views.mark_helpful, name="helpful"),
    path("<int:pk>/delete/", views.delete_review, name="delete"),
    path("product/<slug:slug>/", views.ProductReviewListView.as_view(), name="product"),
    path("product/<slug:slug>/write/", views.write_review, name="write"),
]
