from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import pandas as pd

from utils.config import MAX_VISIBLE_ROWS
from utils.data_rules import normalize_folio
from utils.database import query_dataframe


@dataclass(frozen=True)
class ConstructionFilters:
    reports: tuple[int, ...] = ()
    start_date: date | str | None = None
    end_date: date | str | None = None
    suppliers: tuple[str, ...] = ()
    folio: str = ""
    observation_classes: tuple[str, ...] = ()
    support_types: tuple[str, ...] = ()
    reconciliation_statuses: tuple[str, ...] = ()
    search_text: str = ""


def _supplier_key_sql() -> str:
    """Resolve supplier identity by alias RUT, linked RUT, then name fallback."""
    return """
        COALESCE(
            a.supplier_rut,
            (
                SELECT MAX(COALESCE(d2.supplier_rut, p2.supplier_rut))
                FROM construction_cost_matches m2
                LEFT JOIN documents d2 ON d2.document_id = m2.document_id
                LEFT JOIN payments p2 ON p2.payment_id = m2.payment_id
                WHERE m2.construction_item_id = i.construction_item_id
            ),
            'NAME:' || COALESCE(i.supplier_name_reported, '')
        )
    """


def _date_value(value: date | str) -> str:
    return value.isoformat() if isinstance(value, date) else str(value)


def _in_clause(column: str, values: tuple[object, ...]) -> tuple[str, list[object]]:
    placeholders = ", ".join("?" for _ in values)
    return f"{column} IN ({placeholders})", list(values)


def build_construction_where(filters: ConstructionFilters) -> tuple[str, list[object]]:
    clauses = ["ci.is_active = 1"]
    params: list[object] = []

    if filters.reports:
        clause, values = _in_clause("i.report_no", tuple(int(value) for value in filters.reports))
        clauses.append(clause)
        params.extend(values)
    if filters.start_date:
        clauses.append("date(i.issue_date) >= date(?)")
        params.append(_date_value(filters.start_date))
    if filters.end_date:
        clauses.append("date(i.issue_date) <= date(?)")
        params.append(_date_value(filters.end_date))
    if filters.suppliers:
        clause, values = _in_clause(_supplier_key_sql(), filters.suppliers)
        clauses.append(clause)
        params.extend(values)
    if filters.folio.strip():
        clauses.append("UPPER(COALESCE(i.invoice_key, '')) LIKE UPPER(?)")
        normalized = "".join(char for char in normalize_folio(filters.folio) if char.isalnum())
        params.append(f"%{normalized}%")
    if filters.observation_classes:
        clause, values = _in_clause("i.if_observation_class", filters.observation_classes)
        clauses.append(clause)
        params.extend(values)
    if filters.support_types:
        clause, values = _in_clause("i.support_type", filters.support_types)
        clauses.append(clause)
        params.extend(values)
    if filters.reconciliation_statuses:
        clause, values = _in_clause(
            "i.reconciliation_status", filters.reconciliation_statuses
        )
        clauses.append(clause)
        params.extend(values)
    if filters.search_text.strip():
        clauses.append(
            "UPPER(COALESCE(i.description, '')) LIKE UPPER(?)"
        )
        params.append(f"%{filters.search_text.strip()}%")

    return "WHERE " + " AND ".join(f"({clause})" for clause in clauses), params


def get_active_construction_import() -> dict[str, object] | None:
    frame = query_dataframe(
        """
        SELECT construction_import_id, source_file_path, source_file_name, source_sheet,
               file_hash, file_modified_at, imported_at, row_count,
               first_report_no, last_report_no, notes
        FROM construction_imports
        WHERE is_active = 1
        """
    )
    return None if frame.empty else frame.iloc[0].to_dict()


def get_construction_filter_options() -> dict[str, object]:
    reports = query_dataframe(
        """
        SELECT DISTINCT i.report_no
        FROM construction_cost_items i
        JOIN construction_imports ci
          ON ci.construction_import_id = i.construction_import_id
        WHERE ci.is_active = 1
        ORDER BY i.report_no
        """
    )
    suppliers = query_dataframe(
        f"""
        SELECT DISTINCT
               {_supplier_key_sql()} AS supplier_key,
               COALESCE(a.canonical_name, i.supplier_name_reported) AS supplier_name,
               CASE WHEN a.supplier_rut IS NOT NULL THEN
                   a.supplier_rut || CASE WHEN COALESCE(a.supplier_dv, '') <> ''
                       THEN '-' || a.supplier_dv ELSE '' END
               ELSE COALESCE(
                   (
                       SELECT MAX(COALESCE(d2.supplier_rut, p2.supplier_rut))
                       FROM construction_cost_matches m2
                       LEFT JOIN documents d2 ON d2.document_id = m2.document_id
                       LEFT JOIN payments p2 ON p2.payment_id = m2.payment_id
                       WHERE m2.construction_item_id = i.construction_item_id
                   ), ''
               ) END AS supplier_rut
        FROM construction_cost_items i
        JOIN construction_imports ci
          ON ci.construction_import_id = i.construction_import_id
        LEFT JOIN construction_supplier_aliases a
          ON a.supplier_name_reported = i.supplier_name_reported
        WHERE ci.is_active = 1 AND i.supplier_name_reported IS NOT NULL
        ORDER BY supplier_name, supplier_key
        """
    )
    dates = query_dataframe(
        """
        SELECT MIN(date(i.issue_date)) AS min_date, MAX(date(i.issue_date)) AS max_date
        FROM construction_cost_items i
        JOIN construction_imports ci
          ON ci.construction_import_id = i.construction_import_id
        WHERE ci.is_active = 1
        """
    )
    classifications = query_dataframe(
        """
        SELECT DISTINCT i.if_observation_class, i.support_type, i.reconciliation_status
        FROM construction_cost_items i
        JOIN construction_imports ci
          ON ci.construction_import_id = i.construction_import_id
        WHERE ci.is_active = 1
        """
    )
    return {
        "reports": reports["report_no"].astype(int).tolist(),
        "suppliers": suppliers.to_dict("records"),
        "min_date": dates.at[0, "min_date"],
        "max_date": dates.at[0, "max_date"],
        "observation_classes": sorted(classifications["if_observation_class"].dropna().astype(str).unique()),
        "support_types": sorted(classifications["support_type"].dropna().astype(str).unique()),
        "reconciliation_statuses": sorted(
            classifications["reconciliation_status"].dropna().astype(str).unique()
        ),
    }


def get_construction_items(
    filters: ConstructionFilters,
    limit: int = MAX_VISIBLE_ROWS,
) -> pd.DataFrame:
    where_sql, params = build_construction_where(filters)
    return query_dataframe(
        f"""
        WITH match_summary AS (
            SELECT m.construction_item_id,
                   COUNT(*) AS match_count,
                   GROUP_CONCAT(DISTINCT m.match_status) AS match_statuses,
                   GROUP_CONCAT(DISTINCT m.match_method) AS match_methods,
                   MAX(COALESCE(d.supplier_rut, p.supplier_rut)) AS matched_rut,
                   MAX(COALESCE(d.supplier_name, p.supplier_name)) AS matched_supplier,
                   GROUP_CONCAT(
                       DISTINCT CASE WHEN d.document_id IS NOT NULL
                           THEN d.document_type || '-' || d.document_number END
                   ) AS linked_documents,
                   GROUP_CONCAT(
                       DISTINCT CASE WHEN p.payment_id IS NOT NULL
                           THEN date(p.payment_date) END
                   ) AS linked_payment_dates,
                   COALESCE(SUM(
                       CASE WHEN m.match_status IN ('CONFIRMED', 'PARTIAL')
                           THEN m.allocated_amount_clp ELSE 0 END
                   ), 0) AS allocated_amount_clp,
                   COALESCE(SUM(
                       CASE WHEN m.match_status IN ('CONFIRMED', 'PARTIAL')
                           THEN m.allocated_amount_uf ELSE 0 END
                   ), 0) AS allocated_amount_uf,
                   COALESCE(SUM(
                       CASE WHEN m.match_status IN ('CONFIRMED', 'PARTIAL')
                                 AND d.document_id IS NOT NULL
                           THEN COALESCE(d.recoverable_vat_clp, 0)
                                * COALESCE(m.allocation_percentage, 0)
                           ELSE 0 END
                   ), 0) AS recoverable_vat_clp_raw,
                   MAX(CASE WHEN m.match_status IN ('CONFIRMED', 'PARTIAL')
                       THEN m.allocation_percentage END) AS allocation_percentage
            FROM construction_cost_matches m
            LEFT JOIN documents d ON d.document_id = m.document_id
            LEFT JOIN payments p ON p.payment_id = m.payment_id
            GROUP BY m.construction_item_id
        )
        SELECT i.construction_item_id, i.external_id, i.cost_sequence_no,
               i.report_no, i.source_row, i.issue_date,
               i.invoice_number_reported, i.invoice_key, i.description,
               i.supplier_name_reported, i.net_amount_clp, i.vat_amount_clp,
               i.total_amount_clp, i.net_amount_uf, i.vat_amount_uf,
               i.total_amount_uf, i.if_observation_raw, i.survias_response_raw,
               i.if_observation_class, i.support_type, i.reconciliation_status,
               a.supplier_rut AS linked_rut, a.canonical_name AS canonical_supplier,
               {_supplier_key_sql()} AS supplier_key,
               CASE WHEN a.supplier_rut IS NOT NULL THEN
                   a.supplier_rut || CASE WHEN COALESCE(a.supplier_dv, '') <> ''
                       THEN '-' || a.supplier_dv ELSE '' END
               ELSE COALESCE(ms.matched_rut, '') END AS supplier_rut_display,
               COALESCE(ms.match_count, 0) AS match_count, ms.match_statuses,
               ms.match_methods, ms.matched_rut, ms.matched_supplier,
               ms.linked_documents, ms.linked_payment_dates,
               COALESCE(ms.allocated_amount_clp, 0) AS allocated_amount_clp,
               COALESCE(ms.allocated_amount_uf, 0) AS allocated_amount_uf,
               MIN(
                   MAX(COALESCE(i.vat_amount_clp, 0), 0),
                   MAX(COALESCE(ms.recoverable_vat_clp_raw, 0), 0)
               ) AS recoverable_vat_clp,
               CASE WHEN COALESCE(i.vat_amount_clp, 0) > 0
                   THEN COALESCE(i.vat_amount_uf, 0) * MIN(
                       1.0,
                       MAX(COALESCE(ms.recoverable_vat_clp_raw, 0), 0)
                           / i.vat_amount_clp
                   )
                   ELSE 0 END AS recoverable_vat_uf,
               ms.allocation_percentage
        FROM construction_cost_items i
        JOIN construction_imports ci
          ON ci.construction_import_id = i.construction_import_id
        LEFT JOIN construction_supplier_aliases a
          ON a.supplier_name_reported = i.supplier_name_reported
        LEFT JOIN match_summary ms
          ON ms.construction_item_id = i.construction_item_id
        {where_sql}
        ORDER BY i.report_no, i.cost_sequence_no, i.construction_item_id
        LIMIT ?
        """,
        [*params, int(limit)],
    )


def get_construction_metrics(filters: ConstructionFilters) -> dict[str, float | int]:
    where_sql, params = build_construction_where(filters)
    frame = query_dataframe(
        f"""
        SELECT COUNT(*) AS item_count,
               COALESCE(SUM(i.net_amount_clp), 0) AS net_amount_clp,
               COALESCE(SUM(i.vat_amount_clp), 0) AS vat_amount_clp,
               COALESCE(SUM(i.total_amount_clp), 0) AS total_amount_clp,
               COALESCE(SUM(i.net_amount_uf), 0) AS net_amount_uf,
               COALESCE(SUM(i.vat_amount_uf), 0) AS vat_amount_uf,
               COALESCE(SUM(i.total_amount_uf), 0) AS total_amount_uf,
               COUNT(DISTINCT {_supplier_key_sql()}) AS supplier_count,
               SUM(CASE WHEN i.invoice_key IS NULL THEN 1 ELSE 0 END) AS without_folio_count,
               SUM(CASE WHEN i.if_observation_class = 'OBSERVED' THEN 1 ELSE 0 END)
                   AS observed_count,
               SUM(CASE WHEN i.if_observation_class = 'APPROVED_EXPLICIT' THEN 1 ELSE 0 END)
                   AS approved_count,
               SUM(CASE WHEN i.reconciliation_status = 'PENDING_REVIEW' THEN 1 ELSE 0 END)
                   AS pending_reconciliation_count,
               SUM(CASE WHEN i.reconciliation_status IN
                   ('MATCHED_EXACT', 'MATCHED_PARTIAL', 'MATCHED_PAYMENT')
                   THEN 1 ELSE 0 END) AS linked_count,
               SUM(CASE WHEN i.reconciliation_status = 'MATCHED_PARTIAL'
                   THEN 1 ELSE 0 END) AS partial_count,
               SUM(CASE WHEN i.reconciliation_status = 'MATCHED_PAYMENT'
                   THEN 1 ELSE 0 END) AS payment_matched_count,
               SUM(CASE WHEN i.reconciliation_status = 'REVIEW_REQUIRED'
                   THEN 1 ELSE 0 END) AS review_required_count,
               SUM(CASE WHEN i.reconciliation_status = 'AGGREGATE_SUPPORT'
                   THEN 1 ELSE 0 END) AS aggregate_support_count,
               COALESCE(SUM(CASE WHEN i.reconciliation_status IN
                   ('MATCHED_EXACT', 'MATCHED_PARTIAL', 'MATCHED_PAYMENT')
                   THEN i.net_amount_clp ELSE 0 END), 0) AS linked_amount_clp,
               COALESCE(SUM(CASE WHEN i.reconciliation_status IN
                   ('MATCHED_EXACT', 'MATCHED_PARTIAL', 'MATCHED_PAYMENT')
                   THEN i.net_amount_uf ELSE 0 END), 0) AS linked_amount_uf,
               100.0 * SUM(CASE WHEN i.reconciliation_status IN
                   ('MATCHED_EXACT', 'MATCHED_PARTIAL', 'MATCHED_PAYMENT')
                   THEN i.net_amount_clp ELSE 0 END)
                   / NULLIF(SUM(i.net_amount_clp), 0) AS linked_amount_pct
        FROM construction_cost_items i
        JOIN construction_imports ci
          ON ci.construction_import_id = i.construction_import_id
        LEFT JOIN construction_supplier_aliases a
          ON a.supplier_name_reported = i.supplier_name_reported
        {where_sql}
        """,
        params,
    )
    return frame.iloc[0].fillna(0).to_dict()


def get_construction_report_summary(filters: ConstructionFilters) -> pd.DataFrame:
    where_sql, params = build_construction_where(filters)
    return query_dataframe(
        f"""
        SELECT i.report_no,
               COUNT(*) AS item_count,
               MIN(i.issue_date) AS first_issue_date,
               MAX(i.issue_date) AS last_issue_date,
               COUNT(DISTINCT {_supplier_key_sql()}) AS supplier_count,
               COALESCE(SUM(i.net_amount_clp), 0) AS net_amount_clp,
               COALESCE(SUM(i.vat_amount_clp), 0) AS vat_amount_clp,
               COALESCE(SUM(i.total_amount_clp), 0) AS total_amount_clp,
               COALESCE(SUM(i.net_amount_uf), 0) AS net_amount_uf,
               COALESCE(SUM(i.vat_amount_uf), 0) AS vat_amount_uf,
               COALESCE(SUM(i.total_amount_uf), 0) AS total_amount_uf,
               SUM(CASE WHEN i.invoice_key IS NULL THEN 1 ELSE 0 END) AS without_folio_count,
               SUM(CASE WHEN i.if_observation_class = 'APPROVED_EXPLICIT' THEN 1 ELSE 0 END)
                   AS approved_count,
               SUM(CASE WHEN i.if_observation_class = 'OBSERVED' THEN 1 ELSE 0 END)
                   AS observed_count,
               SUM(CASE WHEN i.reconciliation_status = 'PENDING_REVIEW' THEN 1 ELSE 0 END)
                   AS pending_reconciliation_count,
               SUM(CASE WHEN i.reconciliation_status IN
                   ('MATCHED_EXACT', 'MATCHED_PARTIAL', 'MATCHED_PAYMENT')
                   THEN 1 ELSE 0 END) AS linked_count,
               SUM(CASE WHEN i.reconciliation_status = 'MATCHED_PARTIAL'
                   THEN 1 ELSE 0 END) AS partial_count,
               SUM(CASE WHEN i.reconciliation_status = 'MATCHED_PAYMENT'
                   THEN 1 ELSE 0 END) AS payment_matched_count,
               SUM(CASE WHEN i.reconciliation_status = 'REVIEW_REQUIRED'
                   THEN 1 ELSE 0 END) AS review_required_count,
               SUM(CASE WHEN i.reconciliation_status = 'AGGREGATE_SUPPORT'
                   THEN 1 ELSE 0 END) AS aggregate_support_count,
               COALESCE(SUM(CASE WHEN i.reconciliation_status IN
                   ('MATCHED_EXACT', 'MATCHED_PARTIAL', 'MATCHED_PAYMENT')
                   THEN i.net_amount_clp ELSE 0 END), 0) AS linked_amount_clp,
               COALESCE(SUM(CASE WHEN i.reconciliation_status IN
                   ('MATCHED_EXACT', 'MATCHED_PARTIAL', 'MATCHED_PAYMENT')
                   THEN i.net_amount_uf ELSE 0 END), 0) AS linked_amount_uf,
               100.0 * SUM(CASE WHEN i.reconciliation_status IN
                   ('MATCHED_EXACT', 'MATCHED_PARTIAL', 'MATCHED_PAYMENT')
                   THEN i.net_amount_clp ELSE 0 END)
                   / NULLIF(SUM(i.net_amount_clp), 0) AS linked_amount_pct
        FROM construction_cost_items i
        JOIN construction_imports ci
          ON ci.construction_import_id = i.construction_import_id
        LEFT JOIN construction_supplier_aliases a
          ON a.supplier_name_reported = i.supplier_name_reported
        {where_sql}
        GROUP BY i.report_no
        ORDER BY i.report_no
        """,
        params,
    )


def get_construction_supplier_summary(filters: ConstructionFilters) -> pd.DataFrame:
    where_sql, params = build_construction_where(filters)
    return query_dataframe(
        f"""
        SELECT {_supplier_key_sql()} AS supplier_key,
               COALESCE(a.canonical_name, i.supplier_name_reported) AS supplier_name_reported,
               COUNT(*) AS item_count,
               COUNT(DISTINCT i.report_no) AS report_count,
               GROUP_CONCAT(DISTINCT i.report_no) AS reports,
               COALESCE(SUM(i.net_amount_clp), 0) AS net_amount_clp,
               COALESCE(SUM(i.vat_amount_clp), 0) AS vat_amount_clp,
               COALESCE(SUM(i.total_amount_clp), 0) AS total_amount_clp,
               COALESCE(SUM(i.net_amount_uf), 0) AS net_amount_uf,
               COALESCE(SUM(i.vat_amount_uf), 0) AS vat_amount_uf,
               COALESCE(SUM(i.total_amount_uf), 0) AS total_amount_uf,
               SUM(CASE WHEN i.invoice_key IS NULL THEN 1 ELSE 0 END) AS without_folio_count,
               SUM(CASE WHEN i.if_observation_class = 'APPROVED_EXPLICIT' THEN 1 ELSE 0 END)
                   AS approved_count,
               SUM(CASE WHEN i.if_observation_class = 'OBSERVED' THEN 1 ELSE 0 END)
                   AS observed_count,
               SUM(CASE WHEN i.if_observation_class = 'NO_OBSERVATION' THEN 1 ELSE 0 END)
                   AS no_observation_count,
               SUM(CASE WHEN i.reconciliation_status IN
                   ('MATCHED_EXACT', 'MATCHED_PARTIAL', 'MATCHED_PAYMENT')
                   THEN 1 ELSE 0 END) AS linked_count,
               SUM(CASE WHEN i.reconciliation_status = 'REVIEW_REQUIRED'
                   THEN 1 ELSE 0 END) AS review_required_count,
               SUM(CASE WHEN i.reconciliation_status = 'AGGREGATE_SUPPORT'
                   THEN 1 ELSE 0 END) AS aggregate_support_count,
               SUM(CASE WHEN i.reconciliation_status = 'PENDING_REVIEW'
                   THEN 1 ELSE 0 END) AS pending_reconciliation_count
        FROM construction_cost_items i
        JOIN construction_imports ci
          ON ci.construction_import_id = i.construction_import_id
        LEFT JOIN construction_supplier_aliases a
          ON a.supplier_name_reported = i.supplier_name_reported
        {where_sql}
        GROUP BY {_supplier_key_sql()}, COALESCE(a.canonical_name, i.supplier_name_reported)
        ORDER BY net_amount_uf DESC, supplier_name_reported
        """,
        params,
    )
