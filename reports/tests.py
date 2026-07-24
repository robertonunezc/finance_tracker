import os
from decimal import Decimal
from unittest.mock import Mock, patch

from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from receipt.models import Receipt, ReceiptItem


class ReceiptItemsLineTotalReportTests(TestCase):
    def test_reports_use_stored_line_total_without_multiplying_quantity(self):
        receipt = Receipt.objects.create(
            user_id="report-user",
            purchase_date=timezone.now(),
            total_amount=Decimal("20.00"),
            image_url="receipt.jpg",
            status="completed",
            store_name="Corner Market",
        )
        ReceiptItem.objects.create(
            receipt=receipt,
            name="Milk",
            unit_price=Decimal("10.00"),
            line_total=Decimal("20.00"),
            quantity=2,
            category="groceries",
        )

        from reports.services import CategorySpendingService, ReceiptItemsService

        item_report = ReceiptItemsService.build_report({})
        category_report = CategorySpendingService.build_report({})

        self.assertEqual(item_report.rows[0].unit_price, Decimal("10.00"))
        self.assertEqual(item_report.rows[0].line_total, Decimal("20.00"))
        self.assertEqual(item_report.total_amount, Decimal("20.00"))
        self.assertEqual(category_report.grand_total, Decimal("20.00"))


class ReceiptItemsTicketImageTests(TestCase):
    def create_user(self):
        return get_user_model().objects.create_user(
            username="report-user",
            password="password",
        )

    def create_completed_receipt(self, *, image_url="media/uploads/report-source.jpg"):
        receipt = Receipt.objects.create(
            user_id="report-user",
            purchase_date=timezone.now(),
            total_amount=Decimal("12.50"),
            image_url=image_url,
            status="completed",
            store_name="Corner Market",
        )
        ReceiptItem.objects.create(
            receipt=receipt,
            name="Milk",
            unit_price=Decimal("12.50"),
            line_total=Decimal("12.50"),
            quantity=1,
            category="groceries",
        )
        return receipt

    def ticket_image_path(self, receipt):
        return f"/reports/items/{receipt.receipt_id}/ticket-image/"

    def test_receipt_items_requires_login(self):
        response = self.client.get(reverse("reports:receipt-items"))

        self.assertEqual(response.status_code, 302)
        self.assertIn("/admin/login/", response["Location"])

    def test_receipt_items_renders_protected_ticket_image_link(self):
        receipt = self.create_completed_receipt()
        self.client.force_login(self.create_user())

        response = self.client.get(reverse("reports:receipt-items"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "<th>Ticket</th>", html=True)
        self.assertContains(response, self.ticket_image_path(receipt))
        self.assertContains(response, 'target="_blank"')
        self.assertNotContains(response, receipt.image_url)

    def test_ticket_image_requires_login(self):
        receipt = self.create_completed_receipt()

        response = self.client.get(self.ticket_image_path(receipt))

        self.assertEqual(response.status_code, 302)
        self.assertIn("/admin/login/", response["Location"])

    def test_logged_in_user_can_load_local_ticket_image(self):
        receipt = self.create_completed_receipt()
        source_path = settings.MEDIA_ROOT / "uploads" / "report-source.jpg"
        os.makedirs(source_path.parent, exist_ok=True)
        with open(source_path, "wb") as source_file:
            source_file.write(b"jpeg-bytes")
        self.addCleanup(lambda: source_path.exists() and source_path.unlink())
        self.client.force_login(self.create_user())

        response = self.client.get(self.ticket_image_path(receipt))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(b"".join(response.streaming_content), b"jpeg-bytes")
        self.assertEqual(response["Content-Type"], "image/jpeg")

    def test_inactive_receipt_cannot_load_ticket_image(self):
        receipt = self.create_completed_receipt()
        receipt.is_active = False
        receipt.save(update_fields=["is_active"])
        source_path = settings.MEDIA_ROOT / "uploads" / "report-source.jpg"
        os.makedirs(source_path.parent, exist_ok=True)
        with open(source_path, "wb") as source_file:
            source_file.write(b"jpeg-bytes")
        self.addCleanup(lambda: source_path.exists() and source_path.unlink())
        self.client.force_login(self.create_user())

        response = self.client.get(self.ticket_image_path(receipt))

        self.assertEqual(response.status_code, 404)

    @override_settings(MEDIA_ROOT=settings.BASE_DIR / "alternate-media")
    def test_logged_in_user_can_load_project_relative_media_path(self):
        receipt = self.create_completed_receipt(
            image_url="media/uploads/AgACAgEAAxkBAAIBg2paRiHo30TbnrHAAr93d-FZc9G6AAIBC2sbeYPQTsTsx0Hu3FcHAQADAgADeQADPQQ.jpg"
        )
        source_path = settings.BASE_DIR / receipt.image_url
        os.makedirs(source_path.parent, exist_ok=True)
        with open(source_path, "wb") as source_file:
            source_file.write(b"project-relative-jpeg")
        self.addCleanup(lambda: source_path.exists() and source_path.unlink())
        self.client.force_login(self.create_user())

        response = self.client.get(self.ticket_image_path(receipt))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(b"".join(response.streaming_content), b"project-relative-jpeg")
        self.assertEqual(response["Content-Type"], "image/jpeg")

    @patch("reports.views.requests.get")
    def test_logged_in_user_can_load_remote_ticket_image_through_app(self, get):
        receipt = self.create_completed_receipt(
            image_url="https://storage.example.com/uploads/tickets/report-source.jpg"
        )
        remote_response = Mock()
        remote_response.headers = {
            "Content-Type": "image/jpeg",
            "Content-Length": "10",
        }
        remote_response.iter_content.return_value = [b"jpeg-", b"bytes"]
        get.return_value = remote_response
        self.client.force_login(self.create_user())

        response = self.client.get(self.ticket_image_path(receipt))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(b"".join(response.streaming_content), b"jpeg-bytes")
        self.assertEqual(response["Content-Type"], "image/jpeg")
        get.assert_called_once_with(receipt.image_url, stream=True, timeout=15)
