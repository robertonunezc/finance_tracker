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


class ReceiptExtractionValidationTests(TestCase):
    def valid_payload(self):
        return {
            "store_name": {
                "value": "amazon",
                "source_text": "AMZN MX MARKETPLACE",
                "confidence": 0.92,
            },
            "total": {
                "value": "1249.00",
                "source_text": "TOTAL 1,249.00",
                "confidence": 0.94,
            },
            "items": [
                {
                    "name": {
                        "value": "AMZN MX MARKETPLACE",
                        "source_text": "AMZN MX MARKETPLACE 1,249.00",
                        "confidence": 0.91,
                    },
                    "price": {
                        "value": "1249.00",
                        "source_text": "AMZN MX MARKETPLACE 1,249.00",
                        "confidence": 0.93,
                    },
                    "quantity": {
                        "value": 1,
                        "source_text": "AMZN MX MARKETPLACE 1,249.00",
                        "confidence": 0.95,
                    },
                    "category": {
                        "value": "electronics",
                        "source_text": "AMZN MX MARKETPLACE",
                        "confidence": 0.90,
                    },
                }
            ],
        }

    def test_valid_payload_does_not_require_review(self):
        from receipt.extraction_review import validate_receipt_extraction

        result = validate_receipt_extraction(self.valid_payload())

        self.assertFalse(result.requires_review)
        self.assertEqual(result.issues, [])

    def test_low_confidence_requires_review(self):
        from receipt.extraction_review import validate_receipt_extraction

        payload = self.valid_payload()
        payload["items"][0]["price"]["confidence"] = 0.62

        result = validate_receipt_extraction(payload)

        self.assertTrue(result.requires_review)
        self.assertEqual(result.issues[0]["code"], "low_confidence")
        self.assertEqual(result.issues[0]["path"], "items[0].price")

    def test_source_amount_mismatch_requires_review(self):
        from receipt.extraction_review import validate_receipt_extraction

        payload = self.valid_payload()
        payload["items"][0]["price"]["value"] = "12490.00"

        result = validate_receipt_extraction(payload)

        self.assertTrue(result.requires_review)
        self.assertEqual(result.issues[0]["code"], "source_amount_mismatch")
        self.assertEqual(result.issues[0]["path"], "items[0].price")

    def test_item_sum_mismatch_requires_review(self):
        from receipt.extraction_review import validate_receipt_extraction

        payload = self.valid_payload()
        payload["total"]["value"] = "1300.00"
        payload["total"]["source_text"] = "TOTAL 1300.00"

        result = validate_receipt_extraction(payload)

        self.assertTrue(result.requires_review)
        self.assertEqual(result.issues[0]["code"], "item_sum_mismatch")
        self.assertEqual(result.issues[0]["path"], "total")

    def test_missing_required_value_requires_review(self):
        from receipt.extraction_review import validate_receipt_extraction

        payload = self.valid_payload()
        payload["items"][0]["name"]["value"] = ""

        result = validate_receipt_extraction(payload)

        self.assertTrue(result.requires_review)
        self.assertEqual(result.issues[0]["code"], "missing_required_value")
        self.assertEqual(result.issues[0]["path"], "items[0].name")

    def test_source_amount_parser_handles_mexican_amount_format(self):
        from receipt.extraction_review import parse_amounts_from_source_text

        amounts = parse_amounts_from_source_text("AMZN MX MARKETPLACE  1,249.00")

        self.assertEqual(amounts, [Decimal("1249.00")])
