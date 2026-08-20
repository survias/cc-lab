from __future__ import annotations

import pandas as pd

from utils.database import query_dataframe


def get_active_payment_import() -> dict[str, object] | None:
    frame = query_dataframe(
        """
        SELECT payment_import_id, source_file_path, source_file_name, source_hash,
               source_modified_at, imported_at, row_count, valid_row_count,
               invalid_row_count, first_payment_date, last_payment_date
        FROM payment_imports
        WHERE is_active = 1
        ORDER BY CASE import_mode WHEN 'monthly' THEN 1 ELSE 2 END,
                 source_period DESC, payment_import_id DESC
        LIMIT 1
        """
    )
    return None if frame.empty else frame.iloc[0].to_dict()


def get_active_payments() -> pd.DataFrame:
    return query_dataframe(
        """
        SELECT p.payment_id, p.payment_key, p.supplier_rut, p.supplier_dv,
               p.supplier_name, p.payment_date, p.payment_year,
               p.invoice_number_raw, p.invoice_number_base,
               p.invoice_split_suffix, p.invoice_has_split_marker,
               p.invoice_date, p.document_type_hint,
               p.exempt_amount_clp, p.net_amount_clp, p.vat_amount_clp,
               p.other_taxes_clp, p.gross_amount_clp, p.paid_amount_clp,
               p.cost_center_cat, p.cost_center_sub_cat, p.uf_value,
               p.exempt_amount_uf, p.net_amount_uf, p.vat_amount_uf,
               p.other_taxes_uf, p.gross_amount_uf, p.paid_amount_uf,
               p.description, p.payment_type, p.supplier_aux,
               p.source_sheet, p.source_row, p.quality_status,
               p.payment_import_id, pi.source_period, pi.import_mode,
               pi.imported_at AS record_imported_at,
               uf.uf_clp AS official_uf_value
        FROM payments p
        JOIN payment_imports pi
          ON pi.payment_import_id = p.payment_import_id
        LEFT JOIN uf_daily uf ON uf.uf_date = p.payment_date
        WHERE pi.is_active = 1
        ORDER BY p.payment_year, p.source_row
        """
    )


def get_active_payment_summary() -> dict[str, object]:
    frame = query_dataframe(
        """
        SELECT COUNT(*) AS payment_count,
               MIN(p.payment_date) AS first_payment_date,
               MAX(p.payment_date) AS last_payment_date,
               COALESCE(SUM(p.paid_amount_clp), 0) AS paid_amount_clp,
               COUNT(DISTINCT p.payment_import_id) AS import_count
        FROM payments p
        JOIN payment_imports pi ON pi.payment_import_id = p.payment_import_id
        WHERE pi.is_active = 1
        """
    )
    return frame.iloc[0].fillna(0).to_dict()
