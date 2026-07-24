from django.urls import path

from receipt import views

app_name = "receipt-review"

urlpatterns = [
    path("all/", views.receipt_list, name="list"),
    path("upload/", views.upload, name="upload"),
    path("<uuid:receipt_id>/reprocess/", views.reprocess_receipt, name="reprocess"),
    path("<uuid:receipt_id>/delete/", views.delete_receipt, name="delete"),
    path("", views.review_queue, name="queue"),
    path("review/<uuid:receipt_id>/source/", views.review_source, name="source"),
    path("review/<uuid:receipt_id>/", views.review_detail, name="detail"),
]
