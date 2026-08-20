from __future__ import annotations

import shutil
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

import numpy as np

from utils.config import DATABASE_PATH
from utils.migrations import apply_pending_migrations
from utils.reconciliation import (
    reverse_reconciliation_batch,
    save_bulk_review_decisions,
    save_credit_note_decision,
    save_manual_match,
    save_manual_matches_bulk,
    save_paid_confirmation,
    save_review_decision,
    save_unpaid_confirmation,
)


class ReconciliationDecisionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.database = Path(self.temp_dir.name) / "cc_lab.sqlite"
        shutil.copy2(DATABASE_PATH, self.database)
        apply_pending_migrations(database_path=self.database)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_decision_can_be_created_and_updated(self) -> None:
        save_review_decision(
            "DOCUMENT", 1, 300, 301, "COST", "Primera revisión", self.database
        )
        save_review_decision(
            "DOCUMENT", 1, 300, 302, "COST", "Centro corregido", self.database
        )
        with closing(sqlite3.connect(self.database)) as connection:
            row = connection.execute(
                """
                SELECT category_code, subcategory_code, cost_treatment, notes
                FROM review_decisions WHERE document_id = 1
                """
            ).fetchone()
        self.assertEqual(row, (300, 302, "COST", "Centro corregido"))

    def test_manual_match_is_idempotent(self) -> None:
        with closing(sqlite3.connect(self.database)) as connection:
            payment_id = connection.execute("SELECT MIN(payment_id) FROM payments").fetchone()[0]
        save_manual_match(1, payment_id, "Cruce revisado", self.database)
        save_manual_match(1, payment_id, "Cruce revisado", self.database)
        with closing(sqlite3.connect(self.database)) as connection:
            count = connection.execute(
                "SELECT COUNT(*) FROM manual_matches WHERE document_id = 1 AND payment_id = ?",
                (payment_id,),
            ).fetchone()[0]
        self.assertEqual(count, 1)

    def test_bulk_payment_match_and_unpaid_confirmation_are_saved(self) -> None:
        with closing(sqlite3.connect(self.database)) as connection:
            payment_ids = [row[0] for row in connection.execute("SELECT payment_id FROM payments LIMIT 2")]
        inserted = save_manual_matches_bulk(
            [(1, payment_ids[0]), (2, payment_ids[1])], "Cruce masivo", self.database
        )
        save_unpaid_confirmation([3], "Confirmado en revisión", self.database)
        save_paid_confirmation([4], "Pago histórico confirmado", self.database)
        with closing(sqlite3.connect(self.database)) as connection:
            match_count = connection.execute(
                "SELECT COUNT(*) FROM manual_matches WHERE document_id IN (1, 2)"
            ).fetchone()[0]
            unpaid = connection.execute(
                "SELECT payment_review_status, payment_reviewed_at, notes FROM review_decisions WHERE document_id = 3"
            ).fetchone()
            paid = connection.execute(
                "SELECT payment_review_status, notes FROM review_decisions WHERE document_id = 4"
            ).fetchone()
        self.assertEqual((inserted, match_count), (2, 2))
        self.assertEqual(unpaid[0], "UNPAID_CONFIRMED")
        self.assertIsNotNone(unpaid[1])
        self.assertEqual(unpaid[2], "Confirmado en revisión")
        self.assertEqual(paid, ("PAID_CONFIRMED", "Pago histórico confirmado"))

    def test_credit_note_can_be_linked_to_same_supplier_invoice(self) -> None:
        with closing(sqlite3.connect(self.database)) as connection:
            pair = connection.execute(
                """
                SELECT n.document_id, i.document_id
                FROM documents n
                JOIN documents i
                  ON i.supplier_rut = n.supplier_rut
                 AND COALESCE(i.supplier_dv, '') = COALESCE(n.supplier_dv, '')
                 AND i.document_type <> 61
                 AND i.issue_date <= n.issue_date
                WHERE n.document_type = 61
                LIMIT 1
                """
            ).fetchone()
        self.assertIsNotNone(pair)
        save_credit_note_decision(pair[0], "LINKED", pair[1], "Anulación revisada", self.database)
        with closing(sqlite3.connect(self.database)) as connection:
            decision = connection.execute(
                "SELECT decision_type, invoice_document_id, allocated_amount_clp FROM credit_note_decisions WHERE credit_note_id = ?",
                (pair[0],),
            ).fetchone()
        self.assertEqual(decision[:2], ("LINKED", pair[1]))
        self.assertGreaterEqual(decision[2], 0)

    def test_decision_accepts_codes_from_pandas_selectors(self) -> None:
        save_review_decision(
            "DOCUMENT",
            1,
            np.int64(400),
            np.int64(402),
            "COST",
            "Selección desde la interfaz",
            self.database,
        )
        with closing(sqlite3.connect(self.database)) as connection:
            row = connection.execute(
                """
                SELECT category_code, subcategory_code
                FROM review_decisions WHERE document_id = 1
                """
            ).fetchone()
        self.assertEqual(row, (400, 402))

    def test_bulk_decision_rule_and_reversal_are_audited(self) -> None:
        batch_id = save_bulk_review_decisions(
            "DOCUMENT",
            [1, 2],
            300,
            301,
            "COST",
            "Asignación de prueba",
            "SUPPLIER",
            1_000_000,
            supplier_rut_key="76123456",
            create_rule=True,
            database_path=self.database,
        )
        with closing(sqlite3.connect(self.database)) as connection:
            decision_count = connection.execute(
                "SELECT COUNT(*) FROM review_decisions WHERE document_id IN (1, 2)"
            ).fetchone()[0]
            rule_count = connection.execute(
                "SELECT COUNT(*) FROM allocation_rules WHERE is_active = 1"
            ).fetchone()[0]
            batch_count = connection.execute(
                "SELECT COUNT(*) FROM reconciliation_batch_items WHERE batch_id = ?",
                (batch_id,),
            ).fetchone()[0]
        self.assertEqual((decision_count, rule_count, batch_count), (2, 1, 2))

        self.assertEqual(reverse_reconciliation_batch(batch_id, self.database), 2)
        with closing(sqlite3.connect(self.database)) as connection:
            decision_count = connection.execute(
                "SELECT COUNT(*) FROM review_decisions WHERE document_id IN (1, 2)"
            ).fetchone()[0]
            active_rule_count = connection.execute(
                "SELECT COUNT(*) FROM allocation_rules WHERE is_active = 1"
            ).fetchone()[0]
            reversed_at = connection.execute(
                "SELECT reversed_at FROM reconciliation_batches WHERE batch_id = ?",
                (batch_id,),
            ).fetchone()[0]
        self.assertEqual(decision_count, 0)
        self.assertEqual(active_rule_count, 0)
        self.assertIsNotNone(reversed_at)


if __name__ == "__main__":
    unittest.main()
