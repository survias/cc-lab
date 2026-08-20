from __future__ import annotations

import unittest

from utils.legacy_data import (
    PAYABLE_DOCUMENT_TYPES,
    base_invoice_key,
    bidding_comparison,
    load_active_contracts,
    load_cost_control,
    load_payments,
    source_inventory,
    summarize_cost_centers,
)
from utils.payment_data import get_active_payment_import
from utils.database import query_scalar


class LegacyDataTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.cost_control = load_cost_control()

    def test_sqlite_master_sources_are_complete(self) -> None:
        documents = self.cost_control[self.cost_control["RECORD_TYPE"] == "DOCUMENT"]
        payments_only = self.cost_control[self.cost_control["RECORD_TYPE"] == "PAYMENT"]
        self.assertEqual(
            documents["DOCUMENT_ID"].nunique(),
            int(query_scalar("SELECT COUNT(*) FROM documents") or 0),
        )
        self.assertGreater(payments_only["PAYMENT_ID"].nunique(), 0)
        self.assertEqual(
            len(load_payments()),
            int(get_active_payment_import()["valid_row_count"]),
        )
        self.assertTrue(source_inventory()["Disponible"].all())
        self.assertIsNotNone(get_active_payment_import())

    def test_payment_statuses_are_reconstructed(self) -> None:
        counts = self.cost_control["PAYMENT_STATUS"].value_counts().to_dict()
        self.assertEqual(sum(counts.values()), len(self.cost_control))
        for status in ("Pagado", "No pagado", "Pago sin documento", "Nota de crédito"):
            self.assertGreater(counts.get(status, 0), 0)

    def test_ambiguous_matches_are_not_marked_paid(self) -> None:
        ambiguous = self.cost_control[
            self.cost_control["MATCH_METHOD"] == "RUT + folio con varios tipos"
        ]
        self.assertFalse(ambiguous.empty)
        self.assertTrue(
            set(ambiguous["PAYMENT_STATUS"]).issubset(
                {"Revisar cruce", "Nota de crédito", "Anulada por NC"}
            )
        )

    def test_split_invoice_rule_preserves_base_folio(self) -> None:
        self.assertEqual(base_invoice_key("38A"), "38")
        self.assertEqual(base_invoice_key("38B"), "38")
        self.assertEqual(base_invoice_key("38"), "38")

    def test_cost_centers_and_contracts_are_available(self) -> None:
        summary = summarize_cost_centers(self.cost_control)
        self.assertGreaterEqual(len(summary), 20)
        self.assertEqual(len(load_active_contracts()), 57)
        self.assertTrue(
            set(self.cost_control["ALLOCATION_SOURCE"]).issubset(
                {"H-P", "H-P distribuido", "Pendiente", "Manual"}
            )
        )

    def test_split_supplier_is_distributed_by_hp_center(self) -> None:
        split = self.cost_control[
            (self.cost_control["RUT_KEY"] == "59296220")
            & (self.cost_control["DOCUMENT_ID"] == 6734)
        ]
        self.assertEqual(set(split["SUBCATEGORY-F"].dropna().astype(int)), {201, 202})
        self.assertAlmostEqual(float(split["NET-CLP-F"].sum()), 1_322_859_995.0, places=2)

    def test_database2_is_not_an_operational_source(self) -> None:
        inventory = source_inventory()
        self.assertFalse(inventory["Fuente"].str.contains("database2", case=False).any())
        self.assertEqual(
            set(inventory.loc[inventory["Fuente"].isin(["RCV SII", "Pagos H-P"]), "Archivo"]),
            {"cc_lab.sqlite"},
        )

    def test_bidding_is_independent_from_session_state(self) -> None:
        comparison = bidding_comparison()
        self.assertFalse(comparison.empty)
        self.assertIn("COST-BID", comparison.columns)
        self.assertIn("COST-REAL", comparison.columns)
        self.assertGreater(comparison["COST-REAL"].sum(), 0)


if __name__ == "__main__":
    unittest.main()
