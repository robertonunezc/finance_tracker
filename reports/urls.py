from django.urls import path

from reports import views

app_name = "reports"

urlpatterns = [
    path("", views.category_spending, name="category-spending"),
    path("categories/", views.category_spending, name="category-spending-detail"),
    path(
        "items/<uuid:receipt_id>/ticket-image/",
        views.receipt_ticket_image,
        name="receipt-ticket-image",
    ),
    path("items/", views.receipt_items, name="receipt-items"),
]
