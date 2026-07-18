import asyncio
import hashlib
import os
import tempfile
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from django.contrib.auth import get_user_model
from django.urls import reverse
from django.test import TestCase
from django.utils import timezone

from receipt import services as receipt_services
from receipt.dataclasses import ReceiptData, ReceiptItem as ReceiptItemData
from receipt.models import Receipt, ReceiptExtractionReview, ReceiptItem


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

    def test_review_queue_has_status_updated_at_index(self):
        indexes = [tuple(index.fields) for index in ReceiptExtractionReview._meta.indexes]

        self.assertIn(("status", "-updated_at"), indexes)


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

    def test_needs_review_duplicate_reply_mentions_manual_review(self):
        from telegram_bot.process_message import reply_for_existing_receipt

        message = SimpleNamespace(reply_text=AsyncMock())
        update = SimpleNamespace(message=message)

        asyncio.run(reply_for_existing_receipt(update, "receipt-id", "needs_review", "skip_needs_review"))

        message.reply_text.assert_awaited_once()
        self.assertIn(
            "waiting for manual review",
            message.reply_text.call_args.args[0],
        )


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

    def test_missing_required_confidence_requires_review(self):
        from receipt.extraction_review import validate_receipt_extraction

        payload = self.valid_payload()
        del payload["total"]["confidence"]

        result = validate_receipt_extraction(payload)

        self.assertTrue(result.requires_review)
        self.assertEqual(result.issues[0]["code"], "missing_confidence")
        self.assertEqual(result.issues[0]["path"], "total")

    def test_absent_optional_amount_confidence_does_not_require_review(self):
        from receipt.extraction_review import validate_receipt_extraction

        payload = self.valid_payload()
        payload["subtotal"] = {"value": "", "source_text": ""}
        payload["discount"] = {"value": None, "source_text": ""}

        result = validate_receipt_extraction(payload)

        self.assertFalse(result.requires_review)

    def test_source_amount_mismatch_requires_review(self):
        from receipt.extraction_review import validate_receipt_extraction

        payload = self.valid_payload()
        payload["items"][0]["price"]["value"] = "12490.00"

        result = validate_receipt_extraction(payload)

        self.assertTrue(result.requires_review)
        self.assertEqual(result.issues[0]["code"], "source_amount_mismatch")
        self.assertEqual(result.issues[0]["path"], "items[0].price")

    def test_zero_total_requires_review(self):
        from receipt.extraction_review import validate_receipt_extraction

        payload = self.valid_payload()
        payload["total"]["value"] = "0.00"
        payload["total"]["source_text"] = "TOTAL 0.00"

        result = validate_receipt_extraction(payload)

        self.assertTrue(result.requires_review)
        self.assertEqual(result.issues[0]["code"], "invalid_amount")
        self.assertEqual(result.issues[0]["path"], "total")

    def test_zero_item_price_requires_review(self):
        from receipt.extraction_review import validate_receipt_extraction

        payload = self.valid_payload()
        payload["items"][0]["price"]["value"] = "0.00"
        payload["items"][0]["price"]["source_text"] = "ITEM 0.00"

        result = validate_receipt_extraction(payload)

        self.assertTrue(result.requires_review)
        self.assertEqual(result.issues[0]["code"], "invalid_amount")
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

    def test_missing_items_require_review(self):
        from receipt.extraction_review import validate_receipt_extraction

        payload = self.valid_payload()
        payload["items"] = []

        result = validate_receipt_extraction(payload)

        self.assertTrue(result.requires_review)
        self.assertEqual(result.issues[0]["code"], "missing_items")
        self.assertEqual(result.issues[0]["path"], "items")

    def test_fractional_quantity_requires_review(self):
        from receipt.extraction_review import validate_receipt_extraction

        payload = self.valid_payload()
        payload["items"][0]["quantity"]["value"] = "1.5"

        result = validate_receipt_extraction(payload)

        self.assertTrue(result.requires_review)
        self.assertEqual(result.issues[0]["code"], "invalid_quantity")
        self.assertEqual(result.issues[0]["path"], "items[0].quantity")

    def test_non_positive_quantity_requires_review(self):
        from receipt.extraction_review import validate_receipt_extraction

        payload = self.valid_payload()
        payload["items"][0]["quantity"]["value"] = "0"

        result = validate_receipt_extraction(payload)

        self.assertTrue(result.requires_review)
        self.assertEqual(result.issues[0]["code"], "invalid_quantity")
        self.assertEqual(result.issues[0]["path"], "items[0].quantity")

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

    def test_source_amount_parser_handles_unseparated_four_digit_amount(self):
        from receipt.extraction_review import parse_amounts_from_source_text

        amounts = parse_amounts_from_source_text("TOTAL 1249.00")

        self.assertEqual(amounts, [Decimal("1249.00")])

    def test_unseparated_source_amount_does_not_create_false_mismatch(self):
        from receipt.extraction_review import validate_receipt_extraction

        payload = self.valid_payload()
        payload["total"]["source_text"] = "TOTAL 1249.00"
        payload["items"][0]["price"]["source_text"] = "AMZN MX MARKETPLACE 1249.00"

        result = validate_receipt_extraction(payload)

        self.assertFalse(result.requires_review)

    def test_confidence_values_are_clamped_to_contract_range(self):
        from receipt.extraction_review import validate_receipt_extraction

        payload = self.valid_payload()
        payload["total"]["confidence"] = 1.5

        result = validate_receipt_extraction(payload)

        self.assertEqual(result.overall_confidence, 0.9)


class ReceiptExtractionApplicationTests(TestCase):
    def create_pending_receipt(self):
        return Receipt.objects.create(
            user_id="application-user",
            purchase_date=timezone.now(),
            total_amount=Decimal("0.00"),
            image_url="receipt.jpg",
            status="processing",
        )

    def valid_payload(self):
        return ReceiptExtractionValidationTests().valid_payload()

    def enriched_items(self):
        return [
            ReceiptItemData(
                name="AMZN MX MARKETPLACE",
                price=1249.00,
                quantity=1,
                category="electronics",
            )
        ]

    def test_valid_extraction_marks_receipt_completed_without_review(self):
        from receipt.extraction_review import apply_extraction_result

        receipt = self.create_pending_receipt()

        result = apply_extraction_result(str(receipt.receipt_id), self.valid_payload(), self.enriched_items())

        receipt.refresh_from_db()
        self.assertEqual(result.status, "completed")
        self.assertEqual(receipt.status, "completed")
        self.assertEqual(receipt.total_amount, Decimal("1249.00"))
        self.assertEqual(receipt.store_name, "amazon")
        self.assertEqual(ReceiptItem.objects.filter(receipt=receipt).count(), 1)
        self.assertFalse(ReceiptExtractionReview.objects.filter(receipt=receipt).exists())
        self.assertEqual(receipt.extraction_result["validation"]["requires_review"], False)

    def test_low_confidence_extraction_marks_receipt_needs_review(self):
        from receipt.extraction_review import apply_extraction_result

        receipt = self.create_pending_receipt()
        payload = self.valid_payload()
        payload["items"][0]["price"]["confidence"] = 0.62

        result = apply_extraction_result(str(receipt.receipt_id), payload, self.enriched_items())

        receipt.refresh_from_db()
        review = ReceiptExtractionReview.objects.get(receipt=receipt)
        self.assertEqual(result.status, "needs_review")
        self.assertEqual(receipt.status, "needs_review")
        self.assertEqual(review.status, "needs_review")
        self.assertEqual(review.issues[0]["code"], "low_confidence")
        self.assertEqual(review.raw_extraction["items"][0]["price"]["confidence"], 0.62)

    def test_raw_extraction_preserves_original_llm_category_before_enrichment(self):
        from receipt.extraction_review import apply_extraction_result

        receipt = self.create_pending_receipt()
        payload = self.valid_payload()
        payload["items"][0]["category"]["value"] = "other"

        apply_extraction_result(str(receipt.receipt_id), payload, self.enriched_items())

        receipt.refresh_from_db()
        self.assertEqual(receipt.extraction_result["raw_extraction"]["items"][0]["category"]["value"], "other")
        self.assertEqual(receipt.extraction_result["applied_payload"]["items"][0]["category"]["value"], "electronics")

    def test_validation_exception_falls_back_to_needs_review(self):
        from receipt.extraction_review import apply_extraction_result

        receipt = self.create_pending_receipt()

        with patch("receipt.extraction_review.validate_receipt_extraction", side_effect=RuntimeError("validator failed")):
            result = apply_extraction_result(str(receipt.receipt_id), self.valid_payload(), self.enriched_items())

        receipt.refresh_from_db()
        review = ReceiptExtractionReview.objects.get(receipt=receipt)
        self.assertEqual(result.status, "needs_review")
        self.assertEqual(receipt.status, "needs_review")
        self.assertEqual(review.issues[0]["code"], "validation_error")


class ReceiptReviewCorrectionTests(TestCase):
    def create_review_receipt(self):
        from receipt.extraction_review import apply_extraction_result

        receipt = Receipt.objects.create(
            user_id="correction-user",
            purchase_date=timezone.now(),
            total_amount=Decimal("0.00"),
            image_url="receipt.jpg",
            status="processing",
        )
        payload = ReceiptExtractionValidationTests().valid_payload()
        payload["items"][0]["price"]["confidence"] = 0.62
        apply_extraction_result(
            str(receipt.receipt_id),
            payload,
            [
                ReceiptItemData(
                    name="AMZN MX MARKETPLACE",
                    price=1249.00,
                    quantity=1,
                    category="electronics",
                )
            ],
        )
        return Receipt.objects.get(receipt_id=receipt.receipt_id)

    def post_data(self, *, total="1249.00", price="1249.00"):
        return {
            "store_name": "amazon",
            "total_amount": total,
            "subtotal_amount": "",
            "discount_amount": "",
            "item_count": "1",
            "item_0_delete": "0",
            "item_0_name": "AMZN MX MARKETPLACE",
            "item_0_price": price,
            "item_0_quantity": "1",
            "item_0_category": "electronics",
        }

    def test_approval_is_blocked_while_issues_remain(self):
        from receipt.extraction_review import approve_review

        receipt = self.create_review_receipt()

        result = approve_review(
            str(receipt.receipt_id),
            self.post_data(price="12490.00"),
            user="admin",
        )

        receipt.refresh_from_db()
        review = ReceiptExtractionReview.objects.get(receipt=receipt)
        self.assertFalse(result.approved)
        self.assertEqual(receipt.status, "needs_review")
        self.assertEqual(review.status, "needs_review")
        self.assertEqual(review.corrected_payload["items"][0]["price"]["value"], "12490.00")
        self.assertTrue(result.validation.requires_review)

    def test_approval_completes_receipt_after_valid_correction(self):
        from receipt.extraction_review import approve_review

        receipt = self.create_review_receipt()

        result = approve_review(
            str(receipt.receipt_id),
            self.post_data(price="1249.00"),
            user="admin",
        )

        receipt.refresh_from_db()
        review = ReceiptExtractionReview.objects.get(receipt=receipt)
        self.assertTrue(result.approved)
        self.assertEqual(receipt.status, "completed")
        self.assertEqual(receipt.total_amount, Decimal("1249.00"))
        self.assertEqual(review.status, "approved")
        self.assertEqual(review.approved_by, "admin")
        self.assertIsNotNone(review.approved_at)

    def test_approval_allows_corrected_amount_when_original_evidence_was_wrong(self):
        from receipt.extraction_review import approve_review

        receipt = self.create_review_receipt()
        review = ReceiptExtractionReview.objects.get(receipt=receipt)
        review.raw_extraction["items"][0]["price"]["source_text"] = "AMZN MX MARKETPLACE 12,490.00"
        review.raw_extraction["total"]["source_text"] = "TOTAL 12,490.00"
        review.save(update_fields=["raw_extraction"])

        result = approve_review(
            str(receipt.receipt_id),
            self.post_data(price="1249.00"),
            user="admin",
        )

        receipt.refresh_from_db()
        self.assertTrue(result.approved)
        self.assertEqual(receipt.status, "completed")

    def test_approval_can_add_and_remove_item_rows(self):
        from receipt.extraction_review import approve_review

        receipt = self.create_review_receipt()
        data = self.post_data(total="1249.00")
        data.update({
            "item_count": "3",
            "item_0_delete": "1",
            "item_1_delete": "0",
            "item_1_name": "Keyboard",
            "item_1_price": "600.00",
            "item_1_quantity": "1",
            "item_1_category": "electronics",
            "item_2_delete": "0",
            "item_2_name": "Mouse",
            "item_2_price": "649.00",
            "item_2_quantity": "1",
            "item_2_category": "electronics",
        })

        result = approve_review(str(receipt.receipt_id), data, user="admin")

        receipt.refresh_from_db()
        self.assertTrue(result.approved)
        self.assertEqual(receipt.items.count(), 2)
        self.assertEqual(
            list(receipt.items.order_by("id").values_list("name", flat=True)),
            ["Keyboard", "Mouse"],
        )

    def test_approval_preserves_existing_purchase_date(self):
        from receipt.extraction_review import approve_review

        receipt = self.create_review_receipt()
        original_purchase_date = timezone.now() - timezone.timedelta(days=3)
        receipt.purchase_date = original_purchase_date
        receipt.save(update_fields=["purchase_date"])

        result = approve_review(str(receipt.receipt_id), self.post_data(), user="admin")

        receipt.refresh_from_db()
        self.assertTrue(result.approved)
        self.assertEqual(receipt.purchase_date, original_purchase_date)

    def test_approval_blocks_invalid_quantity(self):
        from receipt.extraction_review import approve_review

        receipt = self.create_review_receipt()
        data = self.post_data()
        data["item_0_quantity"] = "1.5"

        result = approve_review(str(receipt.receipt_id), data, user="admin")

        receipt.refresh_from_db()
        self.assertFalse(result.approved)
        self.assertEqual(receipt.status, "needs_review")
        self.assertEqual(result.validation.issues[0]["code"], "invalid_quantity")

    def test_approval_blocks_zero_numeric_defaults(self):
        from receipt.extraction_review import approve_review

        receipt = self.create_review_receipt()
        data = self.post_data(total="0.00", price="0.00")

        result = approve_review(str(receipt.receipt_id), data, user="admin")

        receipt.refresh_from_db()
        self.assertFalse(result.approved)
        self.assertEqual(receipt.status, "needs_review")
        self.assertEqual(result.validation.issues[0]["code"], "invalid_amount")


class ReceiptReviewViewTests(TestCase):
    def create_staff_user(self):
        return get_user_model().objects.create_user(
            username="staff",
            password="password",
            is_staff=True,
        )

    def create_regular_user(self):
        return get_user_model().objects.create_user(
            username="regular",
            password="password",
            is_staff=False,
        )

    def create_review_receipt(self):
        return ReceiptReviewCorrectionTests().create_review_receipt()

    def post_data(self):
        return {
            **ReceiptReviewCorrectionTests().post_data(price="1249.00"),
            "action": "approve",
        }

    def test_queue_requires_login(self):
        response = self.client.get(reverse("receipt-review:queue"))

        self.assertEqual(response.status_code, 302)
        self.assertIn("/admin/login/", response["Location"])

    def test_queue_requires_staff(self):
        self.client.force_login(self.create_regular_user())

        response = self.client.get(reverse("receipt-review:queue"))

        self.assertEqual(response.status_code, 302)
        self.assertIn("/admin/login/", response["Location"])

    def test_staff_can_load_review_queue(self):
        self.create_review_receipt()
        self.client.force_login(self.create_staff_user())

        response = self.client.get(reverse("receipt-review:queue"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Receipt reviews")
        self.assertContains(response, "AMZN MX MARKETPLACE")

    def test_detail_exposes_item_add_remove_controls(self):
        receipt = self.create_review_receipt()
        self.client.force_login(self.create_staff_user())

        response = self.client.get(reverse("receipt-review:detail", args=[receipt.receipt_id]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-add-item')
        self.assertContains(response, 'name="item_0_delete"')
        self.assertContains(response, 'data-remove-item')

    def test_detail_renders_field_level_issue_badges(self):
        receipt = self.create_review_receipt()
        self.client.force_login(self.create_staff_user())

        response = self.client.get(reverse("receipt-review:detail", args=[receipt.receipt_id]))

        self.assertContains(response, 'data-field-issues="items[0].price"')
        self.assertContains(response, "low_confidence")

    def test_detail_preserves_missing_numeric_extraction_values_as_blank(self):
        from receipt.extraction_review import apply_extraction_result

        receipt = Receipt.objects.create(
            user_id="missing-numeric-user",
            purchase_date=timezone.now(),
            total_amount=Decimal("0.00"),
            image_url="receipt.jpg",
            status="processing",
        )
        payload = ReceiptExtractionValidationTests().valid_payload()
        payload["total"]["value"] = None
        payload["items"][0]["price"]["value"] = None
        apply_extraction_result(str(receipt.receipt_id), payload, items=None)
        self.client.force_login(self.create_staff_user())

        response = self.client.get(reverse("receipt-review:detail", args=[receipt.receipt_id]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'name="total_amount" type="number" step="0.01" value=""')
        self.assertContains(response, 'name="item_0_price" type="number" step="0.01" value=""')

    def test_staff_can_approve_corrected_receipt(self):
        receipt = self.create_review_receipt()
        self.client.force_login(self.create_staff_user())

        response = self.client.post(
            reverse("receipt-review:detail", args=[receipt.receipt_id]),
            self.post_data(),
        )

        receipt.refresh_from_db()
        self.assertEqual(response.status_code, 302)
        self.assertEqual(receipt.status, "completed")


class ReceiptReviewReportExclusionTests(TestCase):
    def create_receipt_with_item(self, *, status: str, price: float):
        receipt = Receipt.objects.create(
            user_id=f"report-{status}",
            purchase_date=timezone.now(),
            total_amount=Decimal(str(price)),
            image_url=f"{status}.jpg",
            status=status,
            store_name="amazon",
        )
        ReceiptItem.objects.create(
            receipt=receipt,
            name=f"{status} item",
            price=price,
            quantity=1,
            category="electronics",
        )
        return receipt

    def test_reports_exclude_needs_review_receipts(self):
        from reports.services import CategorySpendingService, ReceiptItemsService

        self.create_receipt_with_item(status="completed", price=10.00)
        self.create_receipt_with_item(status="needs_review", price=99.00)

        item_report = ReceiptItemsService.build_report({})
        category_report = CategorySpendingService.build_report({})

        self.assertEqual(item_report.item_count, 1)
        self.assertEqual(item_report.total_amount, Decimal("10.00"))
        self.assertEqual(category_report.grand_total, Decimal("10.00"))
