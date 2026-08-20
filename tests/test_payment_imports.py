from __future__ import annotations

import unittest

from scripts.import_payments import import_payments
from utils.catalogs import category_label, subcategory_label
from utils.config import DATABASE_PATH, PAYMENTS_SOURCE_PATH
from utils.database import query_dataframe, query_scalar
from utils.migrations import file_sha256
from utils.payment_data import get_active_payments


class PaymentImportTests(unittest.TestCase):
    @unittest.skipUnless(
        PAYMENTS_SOURCE_PATH.is_file(),
        "La comparación con H-P requiere CC_LAB_PAYMENTS_SOURCE.",
    )
    def test_active_import_matches_local_hp(self) -> None:
        frame = query_dataframe(
            "SELECT * FROM payment_imports WHERE is_active = 1 AND import_mode = 'baseline'"
        )
        self.assertEqual(len(frame), 1)
        active = frame.iloc[0].to_dict()
        self.assertEqual(active["source_hash"], file_sha256(PAYMENTS_SOURCE_PATH))
        self.assertEqual(int(active["row_count"]), 14_208)
        self.assertEqual(int(active["valid_row_count"]), 14_201)
        self.assertEqual(int(active["invalid_row_count"]), 7)
        self.assertEqual(active["last_payment_date"], "2026-06-26")

    def test_active_payment_periods_are_unique(self) -> None:
        duplicates = int(
            query_scalar(
                """
                SELECT COUNT(*) FROM (
                    SELECT source_period
                    FROM payment_imports
                    WHERE is_active = 1 AND import_mode = 'monthly'
                    GROUP BY source_period HAVING COUNT(*) > 1
                )
                """
            )
            or 0
        )
        self.assertEqual(duplicates, 0)

    def test_active_payment_totals_by_year(self) -> None:
        frame = query_dataframe(
            """
            SELECT p.payment_year, COUNT(*) AS rows, SUM(p.paid_amount_clp) AS paid_clp
            FROM payments p
            JOIN payment_imports pi ON pi.payment_import_id = p.payment_import_id
            WHERE pi.is_active = 1 AND pi.import_mode = 'baseline'
            GROUP BY p.payment_year
            ORDER BY p.payment_year
            """
        )
        self.assertEqual(frame["rows"].tolist(), [600, 2238, 2076, 2908, 4030, 2349])
        self.assertAlmostEqual(float(frame["paid_clp"].sum()), 528_662_769_489.429, places=2)

    def test_application_reads_only_active_snapshot(self) -> None:
        payments = get_active_payments()
        self.assertGreaterEqual(len(payments), 14_201)
        self.assertGreaterEqual(payments["payment_date"].max(), "2026-06-26")
        self.assertTrue(payments["payment_import_id"].notna().all())

    @unittest.skipUnless(
        PAYMENTS_SOURCE_PATH.is_file(),
        "La reimportación de H-P requiere CC_LAB_PAYMENTS_SOURCE.",
    )
    def test_import_is_idempotent(self) -> None:
        before_hash = file_sha256(PAYMENTS_SOURCE_PATH)
        before_count = int(query_scalar("SELECT COUNT(*) FROM payment_imports") or 0)
        result = import_payments(PAYMENTS_SOURCE_PATH, DATABASE_PATH)
        self.assertFalse(result.created)
        self.assertEqual(int(query_scalar("SELECT COUNT(*) FROM payment_imports") or 0), before_count)
        self.assertEqual(file_sha256(PAYMENTS_SOURCE_PATH), before_hash)

    def test_hp_cost_center_catalog_is_authoritative(self) -> None:
        self.assertEqual(category_label(1000), "EPC - SPV")
        self.assertEqual(subcategory_label(100, 105), "Expropriation & Land Acquisition")
        self.assertEqual(subcategory_label(1000, 1001), "IF Office Expenses")
        self.assertEqual(subcategory_label(1000, 1002), "Citizen Studies")
        self.assertEqual(subcategory_label(1000, 1003), "Construction Department Works")


if __name__ == "__main__":
    unittest.main()
