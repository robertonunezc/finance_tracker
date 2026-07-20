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

        mark_receipt_failed("receipt-id")

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

    def test_process_file_task_module_does_not_import_telegram_client(self):
        from extract_info import tasks

        self.assertFalse(hasattr(tasks, "Bot"))

    @patch("extract_info.tasks.notify_receipt_processed_task.delay")
    @patch("extract_info.tasks.extraction_review.apply_extraction_result")
    @patch("extract_info.tasks.extract_info_service.find_nearest_category")
    @patch("extract_info.tasks.extract_info_service.extract_receipt_text")
    @patch("extract_info.tasks.receipt_services.update_receipt")
    def test_process_file_task_applies_completed_extraction_and_schedules_notification(
        self,
        update_receipt,
        extract_receipt_text,
        find_nearest_category,
        apply_extraction_result,
        notify_delay,
    ):
        from extract_info.tasks import process_file_task

        extract_receipt_text.return_value = self.ticket()
        find_nearest_category.return_value = ("electronics", [0.1, 0.2])
        apply_extraction_result.return_value = self.application_result("completed")

        result = process_file_task.run("receipt-id", "missing.jpg", "image")

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
        notify_delay.assert_called_once_with("receipt-id")

    @patch("extract_info.tasks.notify_receipt_processed_task.delay")
    @patch("extract_info.tasks.extraction_review.apply_extraction_result")
    @patch("extract_info.tasks.extract_info_service.find_nearest_category")
    @patch("extract_info.tasks.extract_info_service.extract_receipt_text")
    @patch("extract_info.tasks.receipt_services.update_receipt")
    def test_process_file_task_schedules_needs_review_notification(
        self,
        update_receipt,
        extract_receipt_text,
        find_nearest_category,
        apply_extraction_result,
        notify_delay,
    ):
        from extract_info.tasks import process_file_task

        extract_receipt_text.return_value = self.ticket()
        find_nearest_category.return_value = ("electronics", [0.1, 0.2])
        apply_extraction_result.return_value = self.application_result("needs_review")

        result = process_file_task.run("receipt-id", "missing.jpg", "image")

        self.assertTrue(result)
        notify_delay.assert_called_once_with("receipt-id")

    @patch("extract_info.tasks.notify_receipt_processed_task.delay")
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
        notify_delay,
    ):
        from extract_info.tasks import process_file_task

        extract_receipt_text.return_value = self.ticket()
        apply_extraction_result.return_value = self.application_result("needs_review")

        result = process_file_task.run("receipt-id", "missing.jpg", "image")

        self.assertTrue(result)
        apply_extraction_result.assert_called_once()
        _, kwargs = apply_extraction_result.call_args
        self.assertEqual(kwargs["ticket"], extract_receipt_text.return_value)
        self.assertEqual(kwargs["items"][0].category, "other")
        self.assertEqual(kwargs["items"][0].category_confidence, 0.0)
        notify_delay.assert_called_once_with("receipt-id")

    def test_cleanup_policy_preserves_local_uploads(self):
        from extract_info.tasks import should_cleanup_processed_file

        self.assertFalse(should_cleanup_processed_file("media/uploads/receipt.jpg"))

    def test_cleanup_policy_allows_temp_files(self):
        from extract_info.tasks import should_cleanup_processed_file

        self.assertTrue(should_cleanup_processed_file(os.path.join(tempfile.gettempdir(), "receipt.jpg")))

    @patch("extract_info.tasks.notify_receipt_processed_task.delay")
    @patch("extract_info.tasks.os.unlink")
    @patch("extract_info.tasks.os.path.exists", return_value=True)
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
        path_exists,
        unlink,
        notify_delay,
    ):
        from extract_info.tasks import process_file_task

        extract_receipt_text.return_value = self.ticket()
        find_nearest_category.return_value = ("electronics", [0.1, 0.2])
        apply_extraction_result.return_value = self.application_result("needs_review")

        result = process_file_task.run("receipt-id", "media/uploads/receipt.jpg", "image")

        self.assertTrue(result)
        unlink.assert_not_called()
        notify_delay.assert_called_once_with("receipt-id")

    @patch("extract_info.tasks.notify_receipt_processed_task.delay")
    @patch("extract_info.tasks.extract_info_service.extract_receipt_text", side_effect=RuntimeError("extract failed"))
    @patch("extract_info.tasks.receipt_services.update_receipt")
    def test_exhausted_retry_marks_receipt_failed_and_schedules_notification(
        self,
        update_receipt,
        extract_receipt_text,
        notify_delay,
    ):
        from extract_info.tasks import process_file_task

        original_retries = process_file_task.request.retries
        process_file_task.request.retries = process_file_task.max_retries
        try:
            with self.assertRaises(RuntimeError):
                process_file_task.run("receipt-id", "missing.jpg", "image")
        finally:
            process_file_task.request.retries = original_retries

        update_receipt.assert_any_call("receipt-id", status="processing")
        update_receipt.assert_any_call("receipt-id", status="failed")
        notify_delay.assert_called_once_with("receipt-id")
