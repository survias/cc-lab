from __future__ import annotations

import unittest

import pandas as pd

from utils.queries import (
    DocumentFilters,
    QualityFilters,
    build_document_where,
    get_document_issues,
    get_document_metrics,
    get_documents,
    get_quality_issues,
    get_quality_summary,
    get_raw_appearances,
)
from utils.ui_helpers import dataframe_to_csv_bytes
from utils.database import query_scalar


class QueryTests(unittest.TestCase):
    def test_filters_are_parameterized(self) -> None:
        malicious_value = "ALDESA' OR 1=1 --"
        sql, params = build_document_where(DocumentFilters(supplier_name=malicious_value))
        self.assertNotIn(malicious_value, sql)
        self.assertEqual(params, [f"%{malicious_value}%"])

    def test_document_type_filter_and_sign_rules(self) -> None:
        invoices = get_documents(DocumentFilters(document_type=34), limit=20)
        credit_notes = get_documents(DocumentFilters(document_type=61), limit=20)
        self.assertTrue((invoices["total_economic_clp"] >= 0).all())
        self.assertTrue((credit_notes["total_economic_clp"] <= 0).all())

    def test_period_filter(self) -> None:
        frame = get_documents(DocumentFilters(source_period="202104"), limit=100)
        self.assertFalse(frame.empty)
        self.assertEqual(set(frame["source_period"]), {"202104"})

    def test_duplicate_identification_and_trace(self) -> None:
        frame = get_documents(DocumentFilters(duplicate_conflict="Con conflicto"), limit=10)
        self.assertFalse(frame.empty)
        self.assertEqual(set(frame["duplicate_conflict"]), {"Con conflicto"})
        key = str(frame.iloc[0]["document_key"])
        self.assertFalse(get_document_issues(key).empty)
        self.assertGreaterEqual(len(get_raw_appearances(key)), 2)

    def test_document_metrics(self) -> None:
        metrics = get_document_metrics(DocumentFilters(document_type=61))
        expected = int(
            query_scalar("SELECT COUNT(*) FROM documents WHERE document_type = 61") or 0
        )
        self.assertEqual(int(metrics["document_count"]), expected)
        self.assertLessEqual(float(metrics["total_economic_clp"]), 0)

    def test_quality_filters_and_summary(self) -> None:
        filters = QualityFilters(issue_area="SII_RCV")
        issues = get_quality_issues(filters)
        summary = get_quality_summary(filters)
        self.assertFalse(issues.empty)
        self.assertEqual(set(issues["issue_area"]), {"SII_RCV"})
        self.assertEqual(summary["issue_count"], len(issues))

    def test_csv_export_is_excel_compatible(self) -> None:
        content = dataframe_to_csv_bytes(pd.DataFrame({"RUT": ["77.057.233-8"]}))
        self.assertTrue(content.startswith(b"\xef\xbb\xbf"))
        self.assertIn(b"RUT", content)


if __name__ == "__main__":
    unittest.main()
