from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Sequence

import pandas as pd

from utils.config import MAX_VISIBLE_ROWS
from utils.data_rules import normalize_folio, normalize_rut_search
from utils.database import query_dataframe


ECONOMIC_SIGN_SQL = """
CASE
    WHEN d.document_type IN (33, 34) THEN 1
    WHEN d.document_type = 61 THEN -1
    ELSE 1
END
"""

DUPLICATE_EXISTS_SQL = """
EXISTS (
    SELECT 1
    FROM validation_issues duplicate_issue
    WHERE duplicate_issue.issue_type = 'duplicate_document_key'
      AND duplicate_issue.document_key = d.document_key
)
"""

ACTIVE_QUALITY_ISSUE_SQL = """
(
    vi.issue_area <> 'PAYMENTS_HP'
    OR vi.payment_import_id IN (
        SELECT payment_import_id FROM payment_imports WHERE is_active = 1
    )
)
"""


@dataclass(frozen=True)
class DocumentFilters:
    start_date: date | str | None = None
    end_date: date | str | None = None
    source_period: str = ""
    supplier_name: str = ""
    supplier_rut: str = ""
    document_type: int | None = None
    folio: str = ""
    quality_status: str = ""
    duplicate_conflict: str = "Todos"


@dataclass(frozen=True)
class QualityFilters:
    issue_area: str = ""
    issue_type: str = ""
    severity: str = ""
    issue_status: str = ""


def _date_value(value: date | str) -> str:
    return value.isoformat() if isinstance(value, date) else str(value)


def build_document_where(filters: DocumentFilters) -> tuple[str, list[object]]:
    clauses: list[str] = []
    params: list[object] = []

    if filters.start_date:
        clauses.append("date(d.issue_date) >= date(?)")
        params.append(_date_value(filters.start_date))
    if filters.end_date:
        clauses.append("date(d.issue_date) <= date(?)")
        params.append(_date_value(filters.end_date))
    if filters.source_period:
        clauses.append("d.source_period = ?")
        params.append(filters.source_period)
    if filters.supplier_name.strip():
        clauses.append("UPPER(COALESCE(d.supplier_name, '')) LIKE UPPER(?)")
        params.append(f"%{filters.supplier_name.strip()}%")
    if filters.supplier_rut.strip():
        clauses.append("REPLACE(REPLACE(UPPER(d.supplier_rut), '.', ''), '-', '') LIKE ?")
        params.append(f"%{normalize_rut_search(filters.supplier_rut)}%")
    if filters.document_type is not None:
        clauses.append("d.document_type = ?")
        params.append(int(filters.document_type))
    if filters.folio.strip():
        clauses.append("UPPER(d.document_number) LIKE ?")
        params.append(f"%{normalize_folio(filters.folio)}%")
    if filters.quality_status:
        clauses.append("d.quality_status = ?")
        params.append(filters.quality_status)
    if filters.duplicate_conflict == "Con conflicto":
        clauses.append(DUPLICATE_EXISTS_SQL)
    elif filters.duplicate_conflict == "Sin conflicto":
        clauses.append(f"NOT ({DUPLICATE_EXISTS_SQL})")

    if not clauses:
        return "", params
    return "WHERE " + " AND ".join(f"({clause})" for clause in clauses), params


def get_document_filter_options() -> dict[str, list]:
    periods = query_dataframe(
        "SELECT DISTINCT source_period FROM documents "
        "WHERE source_period IS NOT NULL ORDER BY source_period DESC"
    )["source_period"].astype(str).tolist()
    types = query_dataframe(
        "SELECT DISTINCT document_type FROM documents ORDER BY document_type"
    )["document_type"].astype(int).tolist()
    statuses = query_dataframe(
        "SELECT DISTINCT quality_status FROM documents "
        "WHERE quality_status IS NOT NULL ORDER BY quality_status"
    )["quality_status"].astype(str).tolist()
    dates = query_dataframe(
        "SELECT MIN(date(issue_date)) AS min_date, MAX(date(issue_date)) AS max_date FROM documents"
    )
    return {
        "periods": periods,
        "document_types": types,
        "quality_statuses": statuses,
        "min_date": dates.at[0, "min_date"],
        "max_date": dates.at[0, "max_date"],
    }


def get_documents(filters: DocumentFilters, limit: int = MAX_VISIBLE_ROWS) -> pd.DataFrame:
    where_sql, params = build_document_where(filters)
    sql = f"""
        SELECT
            d.document_id,
            d.document_key,
            d.issue_date,
            d.reception_date,
            d.supplier_rut || CASE
                WHEN COALESCE(d.supplier_dv, '') <> '' THEN '-' || d.supplier_dv
                ELSE ''
            END AS supplier_rut_full,
            d.supplier_name,
            d.document_type,
            d.document_number,
            d.exempt_amount_clp AS exempt_original_clp,
            d.net_amount_clp AS net_original_clp,
            d.recoverable_vat_clp AS recoverable_vat_original_clp,
            d.non_recoverable_vat_clp AS non_recoverable_vat_original_clp,
            d.total_amount_clp AS total_original_clp,
            ({ECONOMIC_SIGN_SQL}) * ABS(COALESCE(d.exempt_amount_clp, 0)) AS exempt_economic_clp,
            ({ECONOMIC_SIGN_SQL}) * ABS(COALESCE(d.net_amount_clp, 0)) AS net_economic_clp,
            ({ECONOMIC_SIGN_SQL}) * ABS(COALESCE(d.recoverable_vat_clp, 0)) AS recoverable_vat_economic_clp,
            ({ECONOMIC_SIGN_SQL}) * ABS(COALESCE(d.non_recoverable_vat_clp, 0)) AS non_recoverable_vat_economic_clp,
            ({ECONOMIC_SIGN_SQL}) * ABS(COALESCE(d.total_amount_clp, 0)) AS total_economic_clp,
            d.source_period,
            s.source_file_name,
            d.source_row,
            d.quality_status,
            CASE WHEN {DUPLICATE_EXISTS_SQL} THEN 'Con conflicto' ELSE 'Sin conflicto' END
                AS duplicate_conflict
        FROM documents d
        JOIN sources s ON s.source_id = d.source_id
        {where_sql}
        ORDER BY date(d.issue_date) DESC, d.document_id DESC
        LIMIT ?
    """
    return query_dataframe(sql, [*params, int(limit)])


def get_document_metrics(filters: DocumentFilters) -> dict[str, float | int]:
    where_sql, params = build_document_where(filters)
    frame = query_dataframe(
        f"""
        SELECT
            COUNT(*) AS document_count,
            COALESCE(SUM(({ECONOMIC_SIGN_SQL}) * ABS(COALESCE(d.exempt_amount_clp, 0))), 0)
                AS exempt_economic_clp,
            COALESCE(SUM(({ECONOMIC_SIGN_SQL}) * ABS(COALESCE(d.net_amount_clp, 0))), 0)
                AS net_economic_clp,
            COALESCE(SUM(({ECONOMIC_SIGN_SQL}) * ABS(COALESCE(d.recoverable_vat_clp, 0))), 0)
                AS recoverable_vat_economic_clp,
            COALESCE(SUM(({ECONOMIC_SIGN_SQL}) * ABS(COALESCE(d.non_recoverable_vat_clp, 0))), 0)
                AS non_recoverable_vat_economic_clp,
            COALESCE(SUM(({ECONOMIC_SIGN_SQL}) * ABS(COALESCE(d.total_amount_clp, 0))), 0)
                AS total_economic_clp,
            SUM(CASE WHEN d.document_type = 61 THEN 1 ELSE 0 END) AS credit_note_count,
            SUM(CASE WHEN {DUPLICATE_EXISTS_SQL} THEN 1 ELSE 0 END) AS conflict_count
        FROM documents d
        {where_sql}
        """,
        params,
    )
    return frame.iloc[0].fillna(0).to_dict()


def get_dashboard_metrics() -> dict[str, float | int | str]:
    frame = query_dataframe(
        f"""
        SELECT
            COUNT(*) AS document_count,
            COALESCE(SUM(({ECONOMIC_SIGN_SQL}) * ABS(COALESCE(d.total_amount_clp, 0))), 0)
                AS total_economic_clp,
            SUM(CASE WHEN {DUPLICATE_EXISTS_SQL} THEN 1 ELSE 0 END) AS conflict_count,
            MIN(d.source_period) AS first_period,
            MAX(d.source_period) AS last_period,
            COUNT(DISTINCT d.supplier_rut) AS supplier_count
        FROM documents d
        """
    )
    result = frame.iloc[0].fillna(0).to_dict()
    open_issues = query_dataframe(
        f"SELECT COUNT(*) AS n FROM validation_issues vi "
        f"WHERE vi.issue_status = ? AND {ACTIVE_QUALITY_ISSUE_SQL}",
        ["open"],
    ).at[0, "n"]
    result["open_issue_count"] = int(open_issues)
    return result


def get_document_issues(document_key: str) -> pd.DataFrame:
    return query_dataframe(
        """
        SELECT issue_id, issue_area, issue_type, severity, issue_description,
               issue_status, source_id, source_row, detected_at
        FROM validation_issues
        WHERE document_key = ?
        ORDER BY CASE severity WHEN 'error' THEN 1 WHEN 'warning' THEN 2 ELSE 3 END,
                 issue_id
        """,
        [document_key],
    )


def get_raw_appearances(document_key: str) -> pd.DataFrame:
    source_rows = query_dataframe(
        """
        SELECT d.source_id, d.source_row
        FROM documents d
        WHERE d.document_key = ?
        UNION
        SELECT vi.source_id, vi.source_row
        FROM validation_issues vi
        WHERE vi.document_key = ? AND vi.source_id IS NOT NULL AND vi.source_row IS NOT NULL
        """,
        [document_key, document_key],
    )
    if source_rows.empty:
        return pd.DataFrame()

    conditions: list[str] = []
    params: list[object] = []
    for row in source_rows.itertuples(index=False):
        conditions.append("(r.source_id = ? AND r.source_row = ?)")
        params.extend([int(row.source_id), int(row.source_row)])

    return query_dataframe(
        f"""
        SELECT r.raw_id, s.source_file_name, s.source_period, r.source_row,
               r.tipo_doc, r.rut_proveedor, r.razon_social, r.folio,
               r.fecha_docto, r.fecha_recepcion, r.monto_exento, r.monto_neto,
               r.monto_iva_recuperable, r.monto_iva_no_recuperable, r.monto_total
        FROM sii_rcv_raw r
        JOIN sources s ON s.source_id = r.source_id
        WHERE {' OR '.join(conditions)}
        ORDER BY r.source_id, r.source_row
        """,
        params,
    )


def build_quality_where(filters: QualityFilters) -> tuple[str, list[object]]:
    mapping = {
        "issue_area": filters.issue_area,
        "issue_type": filters.issue_type,
        "severity": filters.severity,
        "issue_status": filters.issue_status,
    }
    clauses: list[str] = [ACTIVE_QUALITY_ISSUE_SQL]
    params: list[object] = []
    for column, value in mapping.items():
        if value:
            clauses.append(f"vi.{column} = ?")
            params.append(value)
    return ("WHERE " + " AND ".join(clauses), params) if clauses else ("", params)


def get_quality_options() -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    for column in ("issue_area", "issue_type", "severity", "issue_status"):
        frame = query_dataframe(
            f"SELECT DISTINCT vi.{column} FROM validation_issues vi "
            f"WHERE vi.{column} IS NOT NULL AND {ACTIVE_QUALITY_ISSUE_SQL} "
            f"ORDER BY vi.{column}"
        )
        result[column] = frame[column].astype(str).tolist()
    return result


def get_quality_issues(filters: QualityFilters) -> pd.DataFrame:
    where_sql, params = build_quality_where(filters)
    return query_dataframe(
        f"""
        SELECT vi.issue_id, vi.issue_area, vi.issue_type, vi.severity,
               vi.issue_description, vi.issue_status, vi.document_key,
               vi.payment_key, s.source_file_name, vi.source_row,
               vi.detected_at, vi.resolved_at, vi.resolution_notes
        FROM validation_issues vi
        LEFT JOIN sources s ON s.source_id = vi.source_id
        {where_sql}
        ORDER BY CASE vi.severity WHEN 'error' THEN 1 WHEN 'warning' THEN 2 ELSE 3 END,
                 vi.issue_id DESC
        """,
        params,
    )


def get_quality_summary(filters: QualityFilters) -> dict[str, int]:
    where_sql, params = build_quality_where(filters)
    frame = query_dataframe(
        f"""
        SELECT COUNT(*) AS issue_count,
               SUM(CASE WHEN vi.severity = 'error' THEN 1 ELSE 0 END) AS error_count,
               SUM(CASE WHEN vi.severity = 'warning' THEN 1 ELSE 0 END) AS warning_count,
               SUM(CASE WHEN vi.issue_status = 'open' THEN 1 ELSE 0 END) AS open_count
        FROM validation_issues vi
        {where_sql}
        """,
        params,
    )
    return {key: int(value or 0) for key, value in frame.iloc[0].to_dict().items()}
