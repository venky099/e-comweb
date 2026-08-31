from django.urls import path

from apps.cms import views

app_name = "cms"

urlpatterns = [
    path("blog/", views.PostListView.as_view(), name="post_list"),
    path("blog/category/<slug:slug>/", views.PostCategoryView.as_view(), name="category"),
    path("blog/<slug:slug>/", views.PostDetailView.as_view(), name="post"),
    # Last: a bare slug would otherwise swallow the blog URLs above.
    path("p/<slug:slug>/", views.PageDetailView.as_view(), name="page"),
]
