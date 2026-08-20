from __future__ import annotations

import unittest

from utils.data_rules import economic_effect, normalize_folio, normalize_rut_search


class DataRulesTests(unittest.TestCase):
    def test_invoice_33_is_positive(self) -> None:
        self.assertEqual(economic_effect(33, -100), 100)

    def test_exempt_invoice_34_is_positive(self) -> None:
        self.assertEqual(economic_effect(34, -100), 100)

    def test_credit_note_61_is_negative(self) -> None:
        self.assertEqual(economic_effect(61, 100), -100)

    def test_unclassified_type_preserves_original_sign(self) -> None:
        self.assertEqual(economic_effect(56, -100), -100)

    def test_rut_accepts_body_or_full_value(self) -> None:
        self.assertEqual(normalize_rut_search("77.057.233-8"), "77057233")
        self.assertEqual(normalize_rut_search("77057233"), "77057233")

    def test_folio_removes_excel_decimal_suffix(self) -> None:
        self.assertEqual(normalize_folio("38.0"), "38")


if __name__ == "__main__":
    unittest.main()
