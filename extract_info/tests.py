from decimal import Decimal
import os
import tempfile
from types import SimpleNamespace
from unittest.mock import patch

from django.test import TestCase
from pydantic import ValidationError

from extract_info.services import (
    AmountExtractionField,
    CategoryExtractionField,
    Item,
    QuantityExtractionField,
    TextExtractionField,
    Ticket,
    normalize_store_name,
    normalize_category_key,
)
from receipt.dataclasses import ReceiptItem as ReceiptItemData


class NormalizeStoreNameTests(TestCase):
    def test_removes_legal_suffixes_and_generic_prefixes(self):
        self.assertEqual(normalize_store_name("TIENDAS CHEDRAUI SA DE CV"), "chedraui")
        self.assertEqual(normalize_store_name("Chedraui"), "chedraui")
        self.assertEqual(normalize_store_name("Soriana S.A. de C.V."), "soriana")

    def test_returns_none_for_empty_values(self):
        self.assertIsNone(normalize_store_name(""))
        self.assertIsNone(normalize_store_name(None))


class NormalizeCategoryKeyTests(TestCase):
    def test_accepts_category_keys_labels_and_key_label_output(self):
        self.assertEqual(normalize_category_key("dairy"), "dairy")
        self.assertEqual(normalize_category_key("Lácteos"), "dairy")
        self.assertEqual(normalize_category_key("dairy (Lácteos)"), "dairy")
        self.assertEqual(normalize_category_key('"other"'), "other")

    def test_rejects_unknown_category_values(self):
        self.assertIsNone(normalize_category_key("not a category"))
        self.assertIsNone(normalize_category_key(""))


class TicketLineTotalSchemaTests(TestCase):
    def test_item_schema_separates_unit_price_from_line_total(self):
        ticket = Ticket(
            items=[
                Item(
                    name=TextExtractionField(value="LECHE", source_text="LECHE", confidence=0.95),
                    unit_price=AmountExtractionField(value=10.00, source_text="PRECIO 10.00", confidence=0.94),
                    line_total=AmountExtractionField(value=20.00, source_text="TOTAL 20.00", confidence=0.96),
                    quantity=QuantityExtractionField(value=2, source_text="CANT 2", confidence=0.95),
                    category=CategoryExtractionField(value="dairy", source_text="LECHE", confidence=0.8),
                )
            ],
            total=AmountExtractionField(value=20.00, source_text="TOTAL 20.00", confidence=0.95),
        )

        self.assertEqual(ticket.items[0].unit_price.value, 10.00)
        self.assertEqual(ticket.items[0].line_total.value, 20.00)

    def test_item_schema_accepts_decimal_quantity_from_cant_column(self):
        ticket = Ticket(
            items=[
                {
                    "name": {"value": "AGUACATE KG", "source_text": "AGUACATE KG", "confidence": 0.95},
                    "unit_price": {"value": 39.80, "source_text": "PRECIO 39.80", "confidence": 0.94},
                    "line_total": {"value": 21.69, "source_text": "TOTAL 21.69", "confidence": 0.96},
                    "quantity": {"value": 0.545, "source_text": "CANT 0.545", "confidence": 0.95},
                    "category": {"value": "produce", "source_text": "AGUACATE KG", "confidence": 0.8},
                }
            ],
            total=AmountExtractionField(value=21.69, source_text="TOTAL 21.69", confidence=0.95),
        )

        self.assertEqual(ticket.items[0].quantity.value, 0.545)
        self.assertEqual(ticket.items[0].category.value, "produce")

    def test_item_category_schema_rejects_unknown_values(self):
        with self.assertRaises(ValidationError):
            CategoryExtractionField(value="OTROS", source_text="LECHE", confidence=0.8)


class TicketExtractionRetryTests(TestCase):
    def validation_error(self):
        try:
            Ticket.model_validate(
                {
                    "items": [
                        {
                            "name": {
                                "value": "LECHE",
                                "source_text": "LECHE",
                                "confidence": 1.2,
                            }
                        }
                    ]
                }
            )
        except ValidationError as exc:
            return exc
        self.fail("Expected invalid ticket payload to raise ValidationError")

    def ticket(self):
        return Ticket(
            items=[
                Item(
                    name=TextExtractionField(value="LECHE", source_text="LECHE", confidence=0.95),
                    unit_price=AmountExtractionField(value=42.5, source_text="$42.50", confidence=0.95),
                    line_total=AmountExtractionField(value=42.5, source_text="$42.50", confidence=0.95),
                    quantity=QuantityExtractionField(value=1, source_text="1", confidence=0.95),
                    category=CategoryExtractionField(value="groceries", source_text="LECHE", confidence=0.8),
                )
            ],
            total=AmountExtractionField(value=42.5, source_text="$42.50", confidence=0.95),
        )

    def response(self, ticket=None, refusal=None):
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(
                        parsed=ticket,
                        refusal=refusal,
                    )
                )
            ]
        )

    @patch("extract_info.services.client.chat.completions.parse")
    def test_extract_receipt_text_retries_validation_error_with_repair_instruction(self, parse):
        from extract_info.services import extract_receipt_text

        parsed_ticket = self.ticket()
        parse.side_effect = [self.validation_error(), self.response(parsed_ticket)]

        with tempfile.NamedTemporaryFile(suffix=".jpg") as image_file:
            image_file.write(b"receipt image bytes")
            image_file.flush()

            result = extract_receipt_text(image_file.name)

        self.assertEqual(result, parsed_ticket)
        self.assertEqual(parse.call_count, 2)
        first_messages = parse.call_args_list[0].kwargs["messages"]
        retry_messages = parse.call_args_list[1].kwargs["messages"]
        self.assertEqual(len(retry_messages), len(first_messages) + 1)
        self.assertIn(
            "Previous response failed schema validation",
            retry_messages[-1]["content"][0]["text"],
        )

    @patch("extract_info.services.client.chat.completions.parse")
    def test_extract_receipt_text_does_not_retry_model_refusal(self, parse):
        from extract_info.services import extract_receipt_text

        parse.return_value = self.response(ticket=None, refusal="cannot process image")

        with tempfile.NamedTemporaryFile(suffix=".jpg") as image_file:
            image_file.write(b"receipt image bytes")
            image_file.flush()

            with self.assertRaises(ValueError):
                extract_receipt_text(image_file.name)

        self.assertEqual(parse.call_count, 1)


class ReceiptTaskFailureTests(TestCase):
    @patch("extract_info.tasks.receipt_services.update_receipt")
    def test_mark_receipt_failed_uses_valid_failed_status(self, update_receipt):
        from extract_info.tasks import mark_receipt_failed

        mark_receipt_failed("receipt-id")

        update_receipt.assert_called_once_with("receipt-id", status="failed")


class ProcessFileTaskReviewIntegrationTests(TestCase):
    def ticket(self, *, category=None):
        item = SimpleNamespace(
            name={
                "value": "AMZN MX MARKETPLACE",
                "source_text": "AMZN MX MARKETPLACE 1,249.00",
                "confidence": 0.91,
            },
            unit_price={
                "value": 1249.00,
                "source_text": "AMZN MX MARKETPLACE 1,249.00",
                "confidence": 0.93,
            },
            line_total={
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
        if category is not None:
            item.category = {
                "value": category,
                "source_text": "AMZN MX MARKETPLACE",
                "confidence": 0.88,
            }
        return SimpleNamespace(items=[item])

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
                    unit_price=1249.00,
                    line_total=1249.00,
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
    def test_process_file_task_preserves_decimal_quantity_from_cant_column(
        self,
        update_receipt,
        extract_receipt_text,
        find_nearest_category,
        apply_extraction_result,
        notify_delay,
    ):
        from extract_info.tasks import process_file_task

        extract_receipt_text.return_value = SimpleNamespace(
            items=[
                SimpleNamespace(
                    name={
                        "value": "AGUACATE KG",
                        "source_text": "AGUACATE KG",
                        "confidence": 0.91,
                    },
                    unit_price={
                        "value": 39.80,
                        "source_text": "PRECIO 39.80",
                        "confidence": 0.93,
                    },
                    line_total={
                        "value": 21.69,
                        "source_text": "TOTAL 21.69",
                        "confidence": 0.93,
                    },
                    quantity={
                        "value": 0.545,
                        "source_text": "CANT 0.545",
                        "confidence": 0.95,
                    },
                    category={
                        "value": "produce",
                        "source_text": "AGUACATE KG",
                        "confidence": 0.90,
                    },
                )
            ]
        )
        find_nearest_category.return_value = ("produce", [0.1, 0.2])
        apply_extraction_result.return_value = self.application_result("completed")

        result = process_file_task.run("receipt-id", "missing.jpg", "image")

        self.assertTrue(result)
        _, kwargs = apply_extraction_result.call_args
        self.assertEqual(kwargs["items"][0].quantity, Decimal("0.545"))
        self.assertEqual(kwargs["items"][0].line_total, 21.69)
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

        extract_receipt_text.return_value = self.ticket(category="electronics")
        apply_extraction_result.return_value = self.application_result("needs_review")

        result = process_file_task.run("receipt-id", "missing.jpg", "image")

        self.assertTrue(result)
        apply_extraction_result.assert_called_once()
        _, kwargs = apply_extraction_result.call_args
        self.assertEqual(kwargs["ticket"], extract_receipt_text.return_value)
        self.assertEqual(kwargs["items"][0].category, "electronics")
        self.assertEqual(kwargs["items"][0].category_confidence, 0.88)
        notify_delay.assert_called_once_with("receipt-id")

    @patch("extract_info.tasks.notify_receipt_processed_task.delay")
    @patch("extract_info.tasks.extraction_review.apply_extraction_result")
    @patch("extract_info.tasks.extract_info_service.categorize_item")
    @patch("extract_info.tasks.extract_info_service.find_nearest_category")
    @patch("extract_info.tasks.extract_info_service.extract_receipt_text")
    @patch("extract_info.tasks.receipt_services.update_receipt")
    def test_process_file_task_does_not_let_historical_other_override_extracted_category(
        self,
        update_receipt,
        extract_receipt_text,
        find_nearest_category,
        categorize_item,
        apply_extraction_result,
        notify_delay,
    ):
        from extract_info.tasks import process_file_task

        extract_receipt_text.return_value = self.ticket(category="dairy")
        find_nearest_category.return_value = ("other", [0.1, 0.2])
        apply_extraction_result.return_value = self.application_result("completed")

        result = process_file_task.run("receipt-id", "missing.jpg", "image")

        self.assertTrue(result)
        categorize_item.assert_not_called()
        _, kwargs = apply_extraction_result.call_args
        self.assertEqual(kwargs["items"][0].category, "dairy")
        self.assertEqual(kwargs["items"][0].category_confidence, 0.88)
        self.assertEqual(kwargs["items"][0].embedding, [0.1, 0.2])
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
    def test_failure_marks_receipt_failed_and_schedules_notification_without_task_retry(
        self,
        update_receipt,
        extract_receipt_text,
        notify_delay,
    ):
        from extract_info.tasks import process_file_task

        with self.assertRaises(RuntimeError):
            process_file_task.run("receipt-id", "missing.jpg", "image")

        update_receipt.assert_any_call("receipt-id", status="processing")
        update_receipt.assert_any_call("receipt-id", status="failed")
        notify_delay.assert_called_once_with("receipt-id")
        self.assertEqual(getattr(process_file_task, "autoretry_for", ()), ())
