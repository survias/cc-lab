from __future__ import annotations

import io
import shutil
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

import pandas as pd

from scripts.import_construction_costs import import_construction_costs
from utils.config import CONSTRUCTION_SOURCE_PATH, DATABASE_PATH, MIGRATIONS_PATH
from utils.construction_data import (
    ConstructionFilters,
    build_construction_where,
    get_active_construction_import,
    get_construction_items,
    get_construction_metrics,
)
from utils.database import query_dataframe, query_scalar, readonly_connection
from utils.migrations import file_sha256
from utils.ui_helpers import dataframe_to_csv_bytes


EXPECTED_TOTALS = {
    "net_amount_clp": 308_496_231_065.45,
    "vat_amount_clp": 13_919_972_046.42,
    "total_amount_clp": 321_692_272_154.82,
    "net_amount_uf": 8_655_042.7798,
    "vat_amount_uf": 382_221.6194,
    "total_amount_uf": 9_018_192.9411,
}

REPORT_TOTALS = {
    1: (15, 1_480_247_173.747899, 50_017.38237974976),
    14: (137, 14_674_476_479.702013, 391_989.9995187917),
    15: (56, 9_323_919_373.0, 238_133.65826334583),
}


class ConstructionCostsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if not CONSTRUCTION_SOURCE_PATH.is_file():
            raise unittest.SkipTest(
                "La prueba de reimportación requiere CC_LAB_CONSTRUCTION_SOURCE."
            )
        cls.source_hash_before = file_sha256(CONSTRUCTION_SOURCE_PATH)
        cls.temp_dir = tempfile.TemporaryDirectory()
        cls.temp_path = Path(cls.temp_dir.name)
        cls.temp_database = cls.temp_path / "cc_lab.sqlite"
        shutil.copy2(DATABASE_PATH, cls.temp_database)
        with closing(sqlite3.connect(cls.temp_database)) as connection:
            connection.execute("DELETE FROM construction_cost_matches")
            connection.execute("DELETE FROM construction_cost_items")
            connection.execute("DELETE FROM construction_imports")
            connection.commit()
        cls.first_import = import_construction_costs(
            CONSTRUCTION_SOURCE_PATH,
            cls.temp_database,
            MIGRATIONS_PATH,
            cls.temp_path / "backups",
        )
        cls.second_import = import_construction_costs(
            CONSTRUCTION_SOURCE_PATH,
            cls.temp_database,
            MIGRATIONS_PATH,
            cls.temp_path / "backups",
        )

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temp_dir.cleanup()

    def test_hash_and_idempotency(self) -> None:
        self.assertTrue(self.first_import.created)
        self.assertFalse(self.second_import.created)
        self.assertEqual(
            self.first_import.construction_import_id,
            self.second_import.construction_import_id,
        )
        with closing(sqlite3.connect(self.temp_database)) as connection:
            imports = connection.execute("SELECT COUNT(*) FROM construction_imports").fetchone()[0]
            items = connection.execute("SELECT COUNT(*) FROM construction_cost_items").fetchone()[0]
        self.assertEqual(imports, 1)
        self.assertEqual(items, 824)

    def test_excel_source_is_not_modified(self) -> None:
        self.assertEqual(file_sha256(CONSTRUCTION_SOURCE_PATH), self.source_hash_before)

    def test_active_import_and_total_row_exclusion(self) -> None:
        active = get_active_construction_import()
        self.assertIsNotNone(active)
        self.assertEqual(int(active["row_count"]), 824)
        self.assertEqual(
            int(query_scalar("SELECT COUNT(*) FROM construction_cost_items WHERE source_row = 826")),
            0,
        )

    def test_expected_volume_unique_ids_and_reports(self) -> None:
        frame = query_dataframe(
            """
            SELECT COUNT(*) AS rows,
                   COUNT(DISTINCT external_id) AS ids,
                   COUNT(DISTINCT report_no) AS reports,
                   MIN(report_no) AS first_report,
                   MAX(report_no) AS last_report
            FROM construction_cost_items
            WHERE construction_import_id = (
                SELECT construction_import_id FROM construction_imports WHERE is_active = 1
            )
            """
        ).iloc[0]
        self.assertEqual(int(frame["rows"]), 824)
        self.assertEqual(int(frame["ids"]), 824)
        self.assertEqual(int(frame["reports"]), 15)
        self.assertEqual((int(frame["first_report"]), int(frame["last_report"])), (1, 15))

    def test_external_id_is_split_without_replacement(self) -> None:
        frame = query_dataframe(
            """
            SELECT external_id, cost_sequence_no, report_no
            FROM construction_cost_items
            WHERE external_id IN ('1-1', '52-15')
            ORDER BY report_no
            """
        )
        self.assertEqual(frame.iloc[0].to_dict(), {"external_id": "1-1", "cost_sequence_no": 1, "report_no": 1})
        self.assertEqual(frame.iloc[1].to_dict(), {"external_id": "52-15", "cost_sequence_no": 52, "report_no": 15})

    def test_expected_totals(self) -> None:
        metrics = get_construction_metrics(ConstructionFilters())
        for column, expected in EXPECTED_TOTALS.items():
            tolerance = 0.05 if column.endswith("_clp") else 0.0002
            self.assertAlmostEqual(float(metrics[column]), expected, delta=tolerance)

    def test_records_without_folio_are_preserved(self) -> None:
        metrics = get_construction_metrics(ConstructionFilters())
        self.assertEqual(int(metrics["without_folio_count"]), 86)
        defaults = query_dataframe(
            """
            SELECT COUNT(*) AS rows,
                   COUNT(DISTINCT support_type) AS support_types,
                   COUNT(DISTINCT reconciliation_status) AS reconciliation_statuses
            FROM construction_cost_items
            WHERE invoice_key IS NULL
              AND support_type = 'PENDING_CLASSIFICATION'
              AND reconciliation_status = 'PENDING_REVIEW'
            """,
            path=self.temp_database,
        ).iloc[0]
        self.assertEqual(int(defaults["rows"]), 86)
        self.assertEqual(int(defaults["support_types"]), 1)
        self.assertEqual(int(defaults["reconciliation_statuses"]), 1)

    def test_report_filters_and_metrics(self) -> None:
        observed_net_values: list[float] = []
        for report_no, (expected_rows, expected_clp, expected_uf) in REPORT_TOTALS.items():
            filters = ConstructionFilters(reports=(report_no,))
            metrics = get_construction_metrics(filters)
            self.assertEqual(int(metrics["item_count"]), expected_rows)
            self.assertAlmostEqual(float(metrics["net_amount_clp"]), expected_clp, delta=0.02)
            self.assertAlmostEqual(float(metrics["net_amount_uf"]), expected_uf, delta=0.0001)
            observed_net_values.append(float(metrics["net_amount_uf"]))
        self.assertEqual(len(set(observed_net_values)), 3)

    def test_queries_are_parameterized(self) -> None:
        malicious = "Proveedor' OR 1=1 --"
        where_sql, params = build_construction_where(
            ConstructionFilters(reports=(1, 14), suppliers=(malicious,), search_text="obra%")
        )
        self.assertNotIn(malicious, where_sql)
        self.assertNotIn("obra%", where_sql)
        self.assertIn(malicious, params)
        self.assertIn("%obra%%", params)
        self.assertGreaterEqual(where_sql.count("?"), 4)

    def test_filtered_download(self) -> None:
        items = get_construction_items(ConstructionFilters(reports=(1,)))
        exported = dataframe_to_csv_bytes(items)
        restored = pd.read_csv(io.BytesIO(exported), sep=";")
        self.assertEqual(len(restored), 15)
        self.assertEqual(set(restored["report_no"].astype(int)), {1})

    def test_database_remains_read_only_for_application(self) -> None:
        with readonly_connection() as connection:
            self.assertEqual(connection.execute("PRAGMA query_only").fetchone()[0], 1)
            with self.assertRaises(sqlite3.OperationalError):
                connection.execute("UPDATE construction_imports SET notes = 'x'")

    def test_matches_table_is_prepared_and_empty(self) -> None:
        self.assertEqual(
            int(
                query_scalar(
                    "SELECT COUNT(*) FROM construction_cost_matches",
                    path=self.temp_database,
                )
            ),
            0,
        )


if __name__ == "__main__":
    unittest.main()
