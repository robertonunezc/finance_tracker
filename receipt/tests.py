import hashlib
import os
import tempfile
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

from receipt import services as receipt_services
from receipt.dataclasses import ReceiptData
from receipt.models import Receipt


class ReceiptFileHashTests(TestCase):
    def test_compute_file_sha256_hashes_file_bytes(self):
        content = b"same receipt bytes"
        with tempfile.NamedTemporaryFile(delete=False) as temp_file:
            temp_file.write(content)
            temp_file_path = temp_file.name
        self.addCleanup(lambda: os.path.exists(temp_file_path) and os.unlink(temp_file_path))

        digest = receipt_services.compute_file_sha256(temp_file_path)

        self.assertEqual(digest, hashlib.sha256(content).hexdigest())

    def test_same_user_and_hash_reuses_receipt(self):
        receipt_data = ReceiptData(user_id="user-a", image_url="media/uploads/a.jpg", status="pending")

        created = receipt_services.create_receipt_with_file_hash(receipt_data, "a" * 64)
        reused = receipt_services.create_receipt_with_file_hash(receipt_data, "a" * 64)

        self.assertTrue(created.created)
        self.assertFalse(reused.created)
        self.assertEqual(created.receipt_id, reused.receipt_id)
        self.assertEqual(Receipt.objects.count(), 1)

    def test_different_users_can_use_same_hash(self):
        first = receipt_services.create_receipt_with_file_hash(
            ReceiptData(user_id="user-a", image_url="media/uploads/a.jpg", status="pending"),
            "b" * 64,
        )
        second = receipt_services.create_receipt_with_file_hash(
            ReceiptData(user_id="user-b", image_url="media/uploads/b.jpg", status="pending"),
            "b" * 64,
        )

        self.assertTrue(first.created)
        self.assertTrue(second.created)
        self.assertNotEqual(first.receipt_id, second.receipt_id)
        self.assertEqual(Receipt.objects.count(), 2)

    def test_lookup_returns_receipt_status_and_image_url(self):
        created = receipt_services.create_receipt_with_file_hash(
            ReceiptData(
                user_id="user-a",
                image_url="media/uploads/a.jpg",
                status="completed",
                total_amount=Decimal("10.00"),
            ),
            "c" * 64,
        )

        result = receipt_services.get_receipt_by_user_and_file_hash("user-a", "c" * 64)

        self.assertIsNotNone(result)
        self.assertEqual(result.receipt_id, created.receipt_id)
        self.assertEqual(result.status, "completed")
        self.assertEqual(result.image_url, "media/uploads/a.jpg")
        self.assertFalse(result.created)


class ReceiptReviewStatusTests(TestCase):
    def test_needs_review_is_valid_receipt_status(self):
        self.assertIn(
            ("needs_review", "Needs review"),
            list(Receipt._meta.get_field("status").choices),
        )
        receipt = Receipt.objects.create(
            user_id="review-user",
            purchase_date=timezone.now(),
            total_amount=Decimal("10.00"),
            image_url="receipt.jpg",
            status="needs_review",
        )

        self.assertEqual(receipt.status, "needs_review")


class ReceiptDuplicateActionTests(TestCase):
    def test_completed_duplicate_skips_processing(self):
        from telegram_bot.process_message import get_receipt_duplicate_action

        self.assertEqual(get_receipt_duplicate_action("completed"), "skip_completed")

    def test_pending_or_processing_duplicate_skips_new_task(self):
        from telegram_bot.process_message import get_receipt_duplicate_action

        self.assertEqual(get_receipt_duplicate_action("pending"), "skip_in_progress")
        self.assertEqual(get_receipt_duplicate_action("processing"), "skip_in_progress")

    def test_failed_duplicate_retries_same_receipt(self):
        from telegram_bot.process_message import get_receipt_duplicate_action

        self.assertEqual(get_receipt_duplicate_action("failed"), "retry")

    def test_needs_review_duplicate_skips_new_task(self):
        from telegram_bot.process_message import get_receipt_duplicate_action

        self.assertEqual(get_receipt_duplicate_action("needs_review"), "skip_needs_review")
