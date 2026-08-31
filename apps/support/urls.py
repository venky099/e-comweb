from django.urls import path

from apps.support import views

app_name = "support"

urlpatterns = [
    path("", views.TicketListView.as_view(), name="list"),
    path("new/", views.ticket_create, name="create"),
    path("<str:reference>/", views.ticket_detail, name="detail"),
]
