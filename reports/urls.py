from django.urls import path

from reports import views

app_name = "reports"

urlpatterns = [
    path("", views.category_spending, name="category-spending"),
    path("categories/", views.category_spending, name="category-spending-detail"),
]
