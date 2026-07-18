from decimal import Decimal
import os
import tempfile
from types import SimpleNamespace
from unittest.mock import patch

from django.test import TestCase

from extract_info.services import normalize_store_name
from receipt.dataclasses import ReceiptItem as ReceiptItemData


class NormalizeStoreNameTests(TestCase):
    def test_removes_legal_suffixes_and_generic_prefixes(self):
        self.assertEqual(normalize_store_name("TIENDAS CHEDRAUI SA DE CV"), "chedraui")
        self.assertEqual(normalize_store_name("Chedraui"), "chedraui")
        self.assertEqual(normalize_store_name("Soriana S.A. de C.V."), "soriana")

    def test_returns_none_for_empty_values(self):
        self.assertIsNone(normalize_store_name(""))
        self.assertIsNone(normalize_store_name(None))


class ReceiptTaskFailureTests(TestCase):
    @patch("extract_info.tasks.receipt_services.update_receipt")
    def test_mark_receipt_failed_uses_valid_failed_status(self, update_receipt):
        from extract_info.tasks import mark_receipt_failed

        mark_receipt_failed("receipt-id", bot_token=None, chat_id=None)

        update_receipt.assert_called_once_with("receipt-id", status="failed")


class ProcessFileTaskReviewIntegrationTests(TestCase):
    def ticket(self):
        return SimpleNamespace(
            items=[
                SimpleNamespace(
                    name={
                        "value": "AMZN MX MARKETPLACE",
                        "source_text": "AMZN MX MARKETPLACE 1,249.00",
                        "confidence": 0.91,
                    },
                    price={
                        "value": 1249.00,
                        "source_text": "AMZN MX MARKETPLACE 1,249.00",
                        "confidence": 0.93,
                    },
                    quantity={
                        "value": 1,
                        "source_text": "AMZN MX MARKETPLACE 1,249.00",
                        "confidence": 0.95,
                    },
                )
            ]
        )

    def application_result(self, status):
        return SimpleNamespace(
            status=status,
            total_amount=Decimal("1249.00"),
            validation=SimpleNamespace(issues=[{"code": "low_confidence"}] if status == "needs_review" else []),
        )

    @patch("extract_info.tasks.asyncio.run")
    @patch("extract_info.tasks.Bot")
    @patch("extract_info.tasks.os.getenv", return_value="bot-token")
    @patch("extract_info.tasks.extraction_review.apply_extraction_result")
    @patch("extract_info.tasks.extract_info_service.find_nearest_category")
    @patch("extract_info.tasks.extract_info_service.extract_receipt_text")
    @patch("extract_info.tasks.receipt_services.update_receipt")
    def test_process_file_task_applies_completed_extraction_and_sends_success_message(
        self,
        update_receipt,
        extract_receipt_text,
        find_nearest_category,
        apply_extraction_result,
        getenv,
        bot_class,
        run_async,
    ):
        from extract_info.tasks import process_file_task

        extract_receipt_text.return_value = self.ticket()
        find_nearest_category.return_value = ("electronics", [0.1, 0.2])
        apply_extraction_result.return_value = self.application_result("completed")
        bot_class.return_value.send_message.return_value = "send-message"

        result = process_file_task.run("receipt-id", "missing.jpg", 123, "image")

        self.assertTrue(result)
        update_receipt.assert_called_once_with("receipt-id", status="processing")
        apply_extraction_result.assert_called_once()
        _, kwargs = apply_extraction_result.call_args
        self.assertEqual(kwargs["receipt_id"], "receipt-id")
        self.assertEqual(kwargs["ticket"], extract_receipt_text.return_value)
        self.assertEqual(
            kwargs["items"],
            [
                ReceiptItemData(
                    name="AMZN MX MARKETPLACE",
                    price=1249.00,
                    quantity=1,
                    category="electronics",
                    embedding=[0.1, 0.2],
                )
            ],
        )
        self.assertIn("processed successfully", bot_class.return_value.send_message.call_args.kwargs["text"])
        run_async.assert_called_once()

    @patch("extract_info.tasks.asyncio.run")
    @patch("extract_info.tasks.Bot")
    @patch("extract_info.tasks.os.getenv", return_value="bot-token")
    @patch("extract_info.tasks.extraction_review.apply_extraction_result")
    @patch("extract_info.tasks.extract_info_service.find_nearest_category")
    @patch("extract_info.tasks.extract_info_service.extract_receipt_text")
    @patch("extract_info.tasks.receipt_services.update_receipt")
    def test_process_file_task_sends_needs_review_message(
        self,
        update_receipt,
        extract_receipt_text,
        find_nearest_category,
        apply_extraction_result,
        getenv,
        bot_class,
        run_async,
    ):
        from extract_info.tasks import process_file_task

        extract_receipt_text.return_value = self.ticket()
        find_nearest_category.return_value = ("electronics", [0.1, 0.2])
        apply_extraction_result.return_value = self.application_result("needs_review")
        bot_class.return_value.send_message.return_value = "send-message"

        result = process_file_task.run("receipt-id", "missing.jpg", 123, "image")

        self.assertTrue(result)
        self.assertIn("needs manual review", bot_class.return_value.send_message.call_args.kwargs["text"])
        self.assertIn("Issues: 1", bot_class.return_value.send_message.call_args.kwargs["text"])

    @patch("extract_info.tasks.asyncio.run")
    @patch("extract_info.tasks.Bot")
    @patch("extract_info.tasks.os.getenv", return_value="bot-token")
    @patch("extract_info.tasks.extraction_review.apply_extraction_result")
    @patch("extract_info.tasks.extract_info_service.find_nearest_category", side_effect=RuntimeError("embedding failed"))
    @patch("extract_info.tasks.extract_info_service.extract_receipt_text")
    @patch("extract_info.tasks.receipt_services.update_receipt")
    def test_process_file_task_routes_enrichment_failure_to_review(
        self,
        update_receipt,
        extract_receipt_text,
        find_nearest_category,
        apply_extraction_result,
        getenv,
        bot_class,
        run_async,
    ):
        from extract_info.tasks import process_file_task

        extract_receipt_text.return_value = self.ticket()
        apply_extraction_result.return_value = self.application_result("needs_review")
        bot_class.return_value.send_message.return_value = "send-message"

        result = process_file_task.run("receipt-id", "missing.jpg", 123, "image")

        self.assertTrue(result)
        apply_extraction_result.assert_called_once()
        _, kwargs = apply_extraction_result.call_args
        self.assertEqual(kwargs["ticket"], extract_receipt_text.return_value)
        self.assertEqual(kwargs["items"][0].category, "other")
        self.assertEqual(kwargs["items"][0].category_confidence, 0.0)
        self.assertIn("needs manual review", bot_class.return_value.send_message.call_args.kwargs["text"])

    def test_cleanup_policy_preserves_local_uploads(self):
        from extract_info.tasks import should_cleanup_processed_file

        self.assertFalse(should_cleanup_processed_file("media/uploads/receipt.jpg"))

    def test_cleanup_policy_allows_temp_files(self):
        from extract_info.tasks import should_cleanup_processed_file

        self.assertTrue(should_cleanup_processed_file(os.path.join(tempfile.gettempdir(), "receipt.jpg")))

    @patch("extract_info.tasks.asyncio.run")
    @patch("extract_info.tasks.Bot")
    @patch("extract_info.tasks.os.unlink")
    @patch("extract_info.tasks.os.path.exists", return_value=True)
    @patch("extract_info.tasks.os.getenv", return_value="bot-token")
    @patch("extract_info.tasks.extraction_review.apply_extraction_result")
    @patch("extract_info.tasks.extract_info_service.find_nearest_category")
    @patch("extract_info.tasks.extract_info_service.extract_receipt_text")
    @patch("extract_info.tasks.receipt_services.update_receipt")
    def test_process_file_task_preserves_local_upload_after_review_routing(
        self,
        update_receipt,
        extract_receipt_text,
        find_nearest_category,
        apply_extraction_result,
        getenv,
        path_exists,
        unlink,
        bot_class,
        run_async,
    ):
        from extract_info.tasks import process_file_task

        extract_receipt_text.return_value = self.ticket()
        find_nearest_category.return_value = ("electronics", [0.1, 0.2])
        apply_extraction_result.return_value = self.application_result("needs_review")
        bot_class.return_value.send_message.return_value = "send-message"

        result = process_file_task.run("receipt-id", "media/uploads/receipt.jpg", 123, "image")

        self.assertTrue(result)
        unlink.assert_not_called()
