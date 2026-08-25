"""Customer order and checkout URLs.

The literal ``checkout/`` and ``returns/`` prefixes are declared before the
``<str:order_number>/`` catch-all so they are never swallowed by it.
"""
from django.urls import path

from . import views

app_name = "orders"

urlpatterns = [
    path("", views.OrderListView.as_view(), name="list"),
    # ---- checkout ----
    path("checkout/", views.CheckoutAddressView.as_view(), name="checkout_start"),
    path("checkout/address/", views.CheckoutAddressView.as_view(), name="checkout_address"),
    path("checkout/payment/", views.CheckoutPaymentView.as_view(), name="checkout_payment"),
    # ---- returns ----
    path("returns/", views.ReturnListView.as_view(), name="returns"),
    # ---- single order ----
    path("<str:order_number>/", views.OrderDetailView.as_view(), name="detail"),
    path(
        "<str:order_number>/confirmation/",
        views.OrderConfirmationView.as_view(),
        name="confirmation",
    ),
    path("<str:order_number>/track/", views.track_order, name="track"),
    path("<str:order_number>/cancel/", views.cancel_order, name="cancel"),
    path("<str:order_number>/reorder/", views.reorder, name="reorder"),
    path(
        "<str:order_number>/return/<int:item_id>/",
        views.request_return,
        name="request_return",
    ),
]
