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
