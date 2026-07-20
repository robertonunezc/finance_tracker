from django.urls import path

from receipt import views

app_name = "receipt-review"

urlpatterns = [
    path("upload/", views.upload, name="upload"),
    path("", views.review_queue, name="queue"),
    path("review/<uuid:receipt_id>/source/", views.review_source, name="source"),
    path("review/<uuid:receipt_id>/", views.review_detail, name="detail"),
]
