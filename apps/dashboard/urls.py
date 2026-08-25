"""Staff dashboard URLs (mounted at /staff/)."""
from django.urls import path

from . import views

app_name = "dashboard"

urlpatterns = [
    path("", views.DashboardView.as_view(), name="index"),
    path("reports/", views.ReportsView.as_view(), name="reports"),
    path("api/stats/", views.stats_json, name="stats_json"),
    path("api/chart/<str:chart>/", views.chart_data, name="chart_data"),
    path("export/<str:report>/", views.export_report, name="export"),
]
