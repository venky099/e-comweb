from django.urls import path

from apps.invoices import views

app_name = "invoices"

urlpatterns = [
    path("<str:number>/", views.invoice_detail, name="detail"),
    path("<str:number>/download/", views.invoice_pdf, name="download"),
]
