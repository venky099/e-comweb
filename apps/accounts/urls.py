"""Authentication and customer account URLs."""
from django.urls import path

from . import views

app_name = "accounts"

urlpatterns = [
    # ---- auth ----
    path("register/", views.RegisterView.as_view(), name="register"),
    path("login/", views.CustomerLoginView.as_view(), name="login"),
    path("logout/", views.CustomerLogoutView.as_view(), name="logout"),
    # ---- password reset ----
    path(
        "password-reset/",
        views.CustomerPasswordResetView.as_view(),
        name="password_reset",
    ),
    path(
        "password-reset/sent/",
        views.CustomerPasswordResetDoneView.as_view(),
        name="password_reset_done",
    ),
    path(
        "password-reset/<uidb64>/<token>/",
        views.CustomerPasswordResetConfirmView.as_view(),
        name="password_reset_confirm",
    ),
    path(
        "password-reset/done/",
        views.CustomerPasswordResetCompleteView.as_view(),
        name="password_reset_complete",
    ),
    # ---- account area ----
    path("", views.AccountDashboardView.as_view(), name="dashboard"),
    path("profile/", views.ProfileUpdateView.as_view(), name="profile"),
    path("change-password/", views.change_password, name="change_password"),
    # ---- addresses ----
    path("addresses/", views.AddressListView.as_view(), name="address_list"),
    path("addresses/new/", views.AddressCreateView.as_view(), name="address_create"),
    path("addresses/<int:pk>/edit/", views.AddressUpdateView.as_view(), name="address_edit"),
    path("addresses/<int:pk>/delete/", views.AddressDeleteView.as_view(), name="address_delete"),
    path("addresses/<int:pk>/default/", views.set_default_address, name="address_set_default"),
]
