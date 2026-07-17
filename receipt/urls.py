from django.urls import path

from receipt import views

app_name = "receipt-review"

urlpatterns = [
    path("", views.review_queue, name="queue"),
    path("<uuid:receipt_id>/", views.review_detail, name="detail"),
]
