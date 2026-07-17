from unittest.mock import patch

from django.test import TestCase

from extract_info.services import normalize_store_name


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
