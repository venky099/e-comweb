from django.urls import path

from apps.geo import views

app_name = "geo"

urlpatterns = [
    path("country/", views.set_country, name="set_country"),
    path("currency/", views.set_currency, name="set_currency"),
]
