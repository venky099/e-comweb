"""Payment URLs."""
from django.urls import path

from . import views

app_name = "payments"

urlpatterns = [
    path("webhook/<str:gateway_name>/", views.webhook, name="webhook"),
    path("<str:order_number>/start/", views.start_payment, name="start"),
    path("<str:order_number>/verify/", views.verify_payment, name="verify"),
    path("<str:order_number>/failed/", views.payment_failed, name="failed"),
    path("<str:order_number>/cancel/", views.cancel_payment, name="cancel"),
]
