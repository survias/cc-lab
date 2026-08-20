from __future__ import annotations

import sqlite3
import uuid
from collections import defaultdict
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from utils.catalogs import category_label, subcategory_label
from utils.config import DATABASE_PATH, LEGACY_DATABASE2_PATH
from utils.database import query_dataframe
from utils.migrations import create_database_backup
from utils.payment_data import get_active_payments


def normalize_legacy_key(value: object) -> str:
    if value is None or pd.isna(value):
        return ""
    text = str(value).strip().upper().replace(" ", "")
    if text.endswith(".0") and text[:-2].replace("-", "").isdigit():
        text = text[:-2]
    return text.lstrip("0") or ("0" if text else "")


def base_invoice_key(value: object) -> str:
    text = normalize_legacy_key(value)
    if len(text) > 1 and text[-1] in {"A", "B"} and text[:-1].isdigit():
        return text[:-1]
    return text


def _documents() -> pd.DataFrame:
    return query_dataframe(
        """
        SELECT d.document_id, d.document_key, d.supplier_rut, d.supplier_dv,
               d.supplier_name, d.document_type, d.document_number,
               d.issue_date, d.reception_date, d.source_period,
               d.exempt_amount_clp, d.net_amount_clp,
               d.recoverable_vat_clp, d.non_recoverable_vat_clp,
               d.total_amount_clp, d.purchase_type,
               d.source_id, d.source_row, d.quality_status,
               rd.category_code AS manual_category,
               rd.subcategory_code AS manual_subcategory,
               rd.cost_treatment AS manual_treatment,
               rd.review_status AS decision_status,
               rd.notes AS decision_notes,
               rd.payment_review_status,
               rd.payment_reviewed_at,
               uf.uf_clp AS issue_uf_value,
               s.imported_at AS record_imported_at,
               COALESCE(cn.credited_amount_clp, 0) AS credited_amount_clp,
               cnd.decision_type AS credit_note_decision,
               cnd.classification AS credit_note_classification
        FROM documents d
        LEFT JOIN review_decisions rd ON rd.document_id = d.document_id
        LEFT JOIN uf_daily uf ON uf.uf_date = d.issue_date
        LEFT JOIN sources s ON s.source_id = d.source_id
        LEFT JOIN (
            SELECT invoice_document_id, SUM(allocated_amount_clp) AS credited_amount_clp
            FROM credit_note_decisions
            WHERE decision_type = 'LINKED'
            GROUP BY invoice_document_id
        ) cn ON cn.invoice_document_id = d.document_id
        LEFT JOIN credit_note_decisions cnd ON cnd.credit_note_id = d.document_id
        """
    )


def _manual_matches() -> pd.DataFrame:
    return query_dataframe(
        "SELECT document_id, payment_id, notes FROM manual_matches"
    )


def _payment_decisions() -> pd.DataFrame:
    return query_dataframe(
        """
        SELECT payment_id, category_code AS manual_category,
               subcategory_code AS manual_subcategory,
               cost_treatment AS manual_treatment,
               review_status AS decision_status,
               notes AS decision_notes
        FROM review_decisions
        WHERE payment_id IS NOT NULL
        """
    )


def _allocation_rules() -> pd.DataFrame:
    return query_dataframe(
        """
        SELECT rule_id, supplier_rut_key, item_type, document_type,
               category_code, subcategory_code, cost_treatment, notes, created_at
        FROM allocation_rules
        WHERE is_active = 1
        """
    )


def _apply_allocation_rules(
    frame: pd.DataFrame,
    rules: pd.DataFrame,
    item_type: str,
    document_type_column: str | None = None,
) -> pd.DataFrame:
    result = frame.copy()
    columns = ["RULE_ID", "RULE_CATEGORY", "RULE_SUBCATEGORY", "RULE_TREATMENT"]
    for column in columns:
        result[column] = pd.NA
    selected = rules[rules["item_type"] == item_type]
    if selected.empty:
        result["RULE_APPLIED"] = False
        return result

    selected = selected.copy()
    selected["RULE_CREATED_AT"] = pd.to_datetime(
        selected["created_at"], errors="coerce", utc=True
    )

    generic = {
        str(row.supplier_rut_key): row
        for row in selected[selected["document_type"] == 0].itertuples(index=False)
    }
    specific = {
        (str(row.supplier_rut_key), int(row.document_type)): row
        for row in selected[selected["document_type"] != 0].itertuples(index=False)
    }
    imported_dates = pd.to_datetime(
        result.get("record_imported_at"), errors="coerce", utc=True
    )
    matched_rules = []
    for position, row in enumerate(result.itertuples(index=False)):
        rut_key = str(getattr(row, "RUT_KEY"))
        matched = None
        if document_type_column is not None:
            document_type = getattr(row, document_type_column)
            if pd.notna(document_type):
                matched = specific.get((rut_key, int(document_type)))
        matched = matched or generic.get(rut_key)
        if matched is not None:
            imported_at = imported_dates.iloc[position]
            if (
                pd.isna(imported_at)
                or pd.isna(matched.RULE_CREATED_AT)
                or imported_at <= matched.RULE_CREATED_AT
            ):
                matched = None
        matched_rules.append(matched)

    result["RULE_ID"] = [rule.rule_id if rule is not None else pd.NA for rule in matched_rules]
    result["RULE_CATEGORY"] = [rule.category_code if rule is not None else pd.NA for rule in matched_rules]
    result["RULE_SUBCATEGORY"] = [rule.subcategory_code if rule is not None else pd.NA for rule in matched_rules]
    result["RULE_TREATMENT"] = [rule.cost_treatment if rule is not None else pd.NA for rule in matched_rules]
    result["RULE_APPLIED"] = result["RULE_ID"].notna()
    return result


def _cost_value(net: pd.Series, exempt: pd.Series, fallback: pd.Series) -> pd.Series:
    return net.where(net.abs() > 0, exempt.where(exempt.abs() > 0, fallback)).fillna(0)


def build_cost_control() -> pd.DataFrame:
    documents = _documents()
    payments = get_active_payments()
    payment_decisions = _payment_decisions()
    manual_matches = _manual_matches()
    allocation_rules = _allocation_rules()

    for column in (
        "exempt_amount_clp", "net_amount_clp", "recoverable_vat_clp",
        "non_recoverable_vat_clp", "total_amount_clp",
    ):
        documents[column] = pd.to_numeric(documents[column], errors="coerce").fillna(0)
    documents["DATE-F"] = pd.to_datetime(documents["issue_date"], errors="coerce")
    documents["RUT_KEY"] = documents["supplier_rut"].map(normalize_legacy_key)
    documents["FOLIO_KEY"] = documents["document_number"].map(normalize_legacy_key)
    documents["FOLIO_BASE"] = documents["FOLIO_KEY"]
    split_document_supplier = documents["RUT_KEY"] == "59296220"
    documents.loc[split_document_supplier, "FOLIO_BASE"] = documents.loc[
        split_document_supplier, "document_number"
    ].map(base_invoice_key)
    documents["DOC_LOOKUP"] = documents["RUT_KEY"] + "|" + documents["FOLIO_BASE"]
    documents["DOC_KEY_COUNT"] = documents.groupby("DOC_LOOKUP")["document_id"].transform("count")
    documents["DOCUMENT_TYPE_COUNT"] = documents.groupby("DOC_LOOKUP")["document_type"].transform("nunique")
    documents = _apply_allocation_rules(
        documents, allocation_rules, "DOCUMENT", "document_type"
    )

    payments = payments.merge(payment_decisions, on="payment_id", how="left")
    payments["RUT_KEY"] = payments["supplier_rut"].map(normalize_legacy_key)
    payments["FOLIO_KEY"] = payments["invoice_number_raw"].map(normalize_legacy_key)
    payments["FOLIO_BASE"] = payments["FOLIO_KEY"]
    split_supplier = payments["RUT_KEY"] == "59296220"
    payments.loc[split_supplier, "FOLIO_BASE"] = payments.loc[
        split_supplier, "invoice_number_base"
    ].map(base_invoice_key)
    payments["DOC_LOOKUP"] = payments["RUT_KEY"] + "|" + payments["FOLIO_BASE"]
    payments = _apply_allocation_rules(payments, allocation_rules, "PAYMENT")
    payments["EFFECTIVE_CATEGORY"] = payments["manual_category"].where(
        payments["manual_category"].notna(),
        payments["RULE_CATEGORY"].where(
            payments["RULE_CATEGORY"].notna(), payments["cost_center_cat"]
        ),
    )
    payments["EFFECTIVE_SUBCATEGORY"] = payments["manual_subcategory"].where(
        payments["manual_subcategory"].notna(),
        payments["RULE_SUBCATEGORY"].where(
            payments["RULE_SUBCATEGORY"].notna(), payments["cost_center_sub_cat"]
        ),
    )
    payments["CC_KEY"] = payments.apply(
        lambda row: (
            f"{int(row['EFFECTIVE_CATEGORY'])}|{int(row['EFFECTIVE_SUBCATEGORY'])}"
            if pd.notna(row["EFFECTIVE_CATEGORY"]) and pd.notna(row["EFFECTIVE_SUBCATEGORY"])
            else ""
        ),
        axis=1,
    )

    candidates = payments[
        ["payment_id", "DOC_LOOKUP", "payment_date"]
    ].merge(
        documents[
            ["document_id", "DOC_LOOKUP", "DOC_KEY_COUNT", "issue_date"]
        ],
        on="DOC_LOOKUP",
        how="inner",
    )
    candidates["payment_date_value"] = pd.to_datetime(candidates["payment_date"], errors="coerce")
    candidates["issue_date_value"] = pd.to_datetime(candidates["issue_date"], errors="coerce")
    candidates["TEMPORAL_CANDIDATE"] = (
        candidates["issue_date_value"].notna()
        & candidates["payment_date_value"].notna()
        & (candidates["issue_date_value"] <= candidates["payment_date_value"])
    )
    candidates["TEMPORAL_COUNT"] = candidates.groupby("payment_id")[
        "TEMPORAL_CANDIDATE"
    ].transform("sum")
    automatic_links = candidates[
        (candidates["DOC_KEY_COUNT"] == 1)
        | (candidates["TEMPORAL_CANDIDATE"] & (candidates["TEMPORAL_COUNT"] == 1))
    ][["document_id", "payment_id"]]
    if not manual_matches.empty:
        manually_linked_payments = set(manual_matches["payment_id"].astype(int))
        automatic_links = automatic_links[
            ~automatic_links["payment_id"].isin(manually_linked_payments)
        ]
        links = pd.concat(
            [automatic_links.assign(IS_MANUAL=False), manual_matches[["document_id", "payment_id"]].assign(IS_MANUAL=True)],
            ignore_index=True,
        ).drop_duplicates(["document_id", "payment_id"], keep="last")
    else:
        links = automatic_links.assign(IS_MANUAL=False)

    linked_payments = links.merge(payments, on="payment_id", how="left")
    if linked_payments.empty:
        payment_summary = pd.DataFrame(columns=["document_id"])
    else:
        linked_payments["PAID_CLP_SAFE"] = pd.to_numeric(
            linked_payments["paid_amount_clp"], errors="coerce"
        ).fillna(0)
        linked_payments["OFFICIAL_UF_SAFE"] = pd.to_numeric(
            linked_payments["official_uf_value"], errors="coerce"
        ).where(lambda values: values > 0)
        linked_payments["PAID_UF_SAFE"] = (
            linked_payments["PAID_CLP_SAFE"] / linked_payments["OFFICIAL_UF_SAFE"]
        )
        payment_summary = linked_payments.groupby("document_id", as_index=False).agg(
            PAYMENT_COUNT=("payment_id", "nunique"),
            FIRST_PAYMENT_DATE=("payment_date", "min"),
            LAST_PAYMENT_DATE=("payment_date", "max"),
            PAID_CLP_SOURCE=("PAID_CLP_SAFE", "sum"),
            PAID_UF_SOURCE=("PAID_UF_SAFE", "sum"),
            HP_CATEGORY=("EFFECTIVE_CATEGORY", "first"),
            HP_SUBCATEGORY=("EFFECTIVE_SUBCATEGORY", "first"),
            HP_CC_COUNT=("CC_KEY", lambda values: values[values != ""].nunique()),
            MANUAL_MATCH_COUNT=("IS_MANUAL", "sum"),
        )
    documents = documents.merge(payment_summary, on="document_id", how="left")
    for column in ("PAYMENT_COUNT", "HP_CC_COUNT", "MANUAL_MATCH_COUNT"):
        documents[column] = documents.get(column, 0).fillna(0).astype(int)
    for column in ("PAID_CLP_SOURCE", "PAID_UF_SOURCE"):
        documents[column] = documents.get(column, 0).fillna(0)

    ambiguous_payment_keys = set(
        payments.loc[payments["FOLIO_BASE"] != "", "DOC_LOOKUP"]
    )
    has_ambiguous_payment = (
        (documents["DOC_KEY_COUNT"] > 1)
        & documents["DOC_LOOKUP"].isin(ambiguous_payment_keys)
        & (documents["PAYMENT_COUNT"] == 0)
        & (documents["MANUAL_MATCH_COUNT"] == 0)
    )
    sign = np.where(documents["document_type"] == 61, -1.0, 1.0)
    documents["EXEMPT-F"] = sign * documents["exempt_amount_clp"].abs()
    documents["NET-CLP-F"] = sign * (
        documents["net_amount_clp"].abs()
        + documents["exempt_amount_clp"].abs()
        + documents["non_recoverable_vat_clp"].abs()
    )
    documents["VAT-CLP-F"] = sign * documents["recoverable_vat_clp"].abs()
    documents["TOTAL-CLP-F"] = sign * documents["total_amount_clp"].abs()
    documents["UF-F"] = pd.to_numeric(
        documents["issue_uf_value"], errors="coerce"
    ).where(lambda values: values > 0)
    documents["NET-UF-F"] = documents["NET-CLP-F"] / documents["UF-F"]
    documents["VAT-UF-F"] = documents["VAT-CLP-F"] / documents["UF-F"]
    documents["TOTAL-UF-F"] = documents["TOTAL-CLP-F"] / documents["UF-F"]
    documents[["NET-UF-F", "VAT-UF-F", "TOTAL-UF-F"]] = documents[
        ["NET-UF-F", "VAT-UF-F", "TOTAL-UF-F"]
    ].replace([np.inf, -np.inf], np.nan)

    documents["PAYMENT_STATUS"] = "No pagado"
    manually_paid = documents["payment_review_status"] == "PAID_CONFIRMED"
    documents.loc[manually_paid, "PAYMENT_STATUS"] = "Pagado"
    documents.loc[documents["PAYMENT_COUNT"] > 0, "PAYMENT_STATUS"] = "Pagado"
    documents.loc[has_ambiguous_payment & ~manually_paid, "PAYMENT_STATUS"] = "Revisar cruce"
    documents.loc[documents["document_type"] == 61, "PAYMENT_STATUS"] = "Nota de crédito"
    documents["PAYMENT_REVIEW_STATUS"] = documents["payment_review_status"].fillna("PENDING")
    documents.loc[documents["PAYMENT_COUNT"] > 0, "PAYMENT_REVIEW_STATUS"] = "PAID_LINKED"
    documents["MATCH_METHOD"] = "Sin pago relacionado"
    documents.loc[documents["PAYMENT_COUNT"] > 0, "MATCH_METHOD"] = "RUT + folio único"
    documents.loc[documents["MANUAL_MATCH_COUNT"] > 0, "MATCH_METHOD"] = "Cruce manual"
    documents.loc[has_ambiguous_payment, "MATCH_METHOD"] = "RUT + folio con varios tipos"
    documents.loc[
        manually_paid & documents["PAYMENT_COUNT"].eq(0), "MATCH_METHOD"
    ] = "Pago confirmado manualmente"
    documents.loc[
        (documents["payment_review_status"] == "UNPAID_CONFIRMED")
        & documents["PAYMENT_COUNT"].eq(0),
        "MATCH_METHOD",
    ] = "No pagado confirmado"

    documents["CATEGORY-F"] = pd.to_numeric(documents["manual_category"], errors="coerce")
    documents["SUBCATEGORY-F"] = pd.to_numeric(documents["manual_subcategory"], errors="coerce")
    documents["ALLOCATION_SOURCE"] = np.where(
        documents["manual_category"].notna() & documents["manual_subcategory"].notna(),
        "Manual",
        "Pendiente",
    )
    rule_allocation = (
        documents["manual_category"].isna()
        & documents["manual_subcategory"].isna()
        & documents["RULE_APPLIED"]
    )
    if rule_allocation.any():
        documents.loc[rule_allocation, "CATEGORY-F"] = pd.to_numeric(
            documents.loc[rule_allocation, "RULE_CATEGORY"]
        ).astype(float)
        documents.loc[rule_allocation, "SUBCATEGORY-F"] = pd.to_numeric(
            documents.loc[rule_allocation, "RULE_SUBCATEGORY"]
        ).astype(float)
        documents.loc[rule_allocation, "ALLOCATION_SOURCE"] = "Regla"
    hp_allocation = (
        documents["CATEGORY-F"].isna()
        & documents["SUBCATEGORY-F"].isna()
        & (documents["HP_CC_COUNT"] == 1)
    )
    documents.loc[hp_allocation, "CATEGORY-F"] = pd.to_numeric(
        documents.loc[hp_allocation, "HP_CATEGORY"], errors="coerce"
    ).astype(float)
    documents.loc[hp_allocation, "SUBCATEGORY-F"] = pd.to_numeric(
        documents.loc[hp_allocation, "HP_SUBCATEGORY"], errors="coerce"
    ).astype(float)
    documents.loc[hp_allocation, "ALLOCATION_SOURCE"] = "H-P"
    documents["COST_TREATMENT"] = documents["manual_treatment"].where(
        documents["manual_treatment"].notna(), documents["RULE_TREATMENT"]
    ).fillna("COST")
    documents["INCLUDED_IN_COST"] = documents["COST_TREATMENT"] == "COST"
    documents["AMOUNT_DIFFERENCE_CLP"] = (
        documents["PAID_CLP_SOURCE"].abs() - documents["TOTAL-CLP-F"].abs()
    )
    documents["REVIEW_REASON"] = ""
    documents.loc[has_ambiguous_payment, "REVIEW_REASON"] = "RUT y folio con varios tipos documentales"
    documents.loc[
        (documents["CATEGORY-F"].isna() | documents["SUBCATEGORY-F"].isna())
        & (documents["REVIEW_REASON"] == ""),
        "REVIEW_REASON",
    ] = "Centro de costo pendiente"
    amount_difference = (
        (documents["PAYMENT_COUNT"] > 0)
        & (documents["AMOUNT_DIFFERENCE_CLP"].abs() > 1)
        & (documents["REVIEW_REASON"] == "")
    )
    documents.loc[amount_difference, "REVIEW_REASON"] = "Diferencia entre documento y pago"
    documents.loc[documents["decision_status"] == "RESOLVED", "REVIEW_REASON"] = ""
    documents["CREDITED_CLP"] = pd.to_numeric(
        documents["credited_amount_clp"], errors="coerce"
    ).fillna(0)
    documents["CREDIT_NOTE_STATUS"] = ""
    credited = documents["CREDITED_CLP"] > 0
    documents.loc[credited, "CREDIT_NOTE_STATUS"] = "Rebajada por NC"
    documents.loc[
        credited
        & (documents["CREDITED_CLP"] >= documents["TOTAL-CLP-F"].abs() - 1),
        "CREDIT_NOTE_STATUS",
    ] = "Anulada por NC"
    documents.loc[
        documents["CREDIT_NOTE_STATUS"] == "Anulada por NC",
        "PAYMENT_STATUS",
    ] = "Anulada por NC"
    documents.loc[
        (documents["document_type"] == 61) & (documents["credit_note_decision"] == "LINKED"),
        "CREDIT_NOTE_STATUS",
    ] = "Vinculada"
    documents.loc[
        (documents["document_type"] == 61) & (documents["credit_note_decision"] == "STANDALONE"),
        "CREDIT_NOTE_STATUS",
    ] = "Independiente"
    documents.loc[
        (documents["document_type"] == 61)
        & (documents["credit_note_classification"] == "GLOBAL_ADJUSTMENT_APPLIED"),
        "CREDIT_NOTE_STATUS",
    ] = "Ajuste global"

    multi_center_ids = set(
        documents.loc[
            (documents["HP_CC_COUNT"] > 1)
            & documents["manual_category"].isna()
            & ~documents["RULE_APPLIED"],
            "document_id",
        ].astype(int)
    )
    if multi_center_ids:
        allocations = (
            linked_payments[
                linked_payments["document_id"].isin(multi_center_ids)
                & linked_payments["EFFECTIVE_CATEGORY"].notna()
                & linked_payments["EFFECTIVE_SUBCATEGORY"].notna()
            ]
            .groupby(
                ["document_id", "EFFECTIVE_CATEGORY", "EFFECTIVE_SUBCATEGORY"],
                as_index=False,
            )
            .agg(
                ALLOCATION_PAYMENT_COUNT=("payment_id", "nunique"),
                ALLOCATION_PAID_CLP=("PAID_CLP_SAFE", "sum"),
                ALLOCATION_PAID_UF=("PAID_UF_SAFE", "sum"),
                ALLOCATION_FIRST_DATE=("payment_date", "min"),
                ALLOCATION_LAST_DATE=("payment_date", "max"),
            )
        )
        allocation_total = allocations.groupby("document_id")["ALLOCATION_PAID_CLP"].transform(
            lambda values: values.abs().sum()
        )
        allocation_count = allocations.groupby("document_id")["document_id"].transform("count")
        allocations["ALLOCATION_RATIO"] = np.where(
            allocation_total > 0,
            allocations["ALLOCATION_PAID_CLP"].abs() / allocation_total,
            1 / allocation_count,
        )
        regular_documents = documents[~documents["document_id"].isin(multi_center_ids)]
        split_documents = documents[
            documents["document_id"].isin(multi_center_ids)
        ].merge(allocations, on="document_id", how="inner")
        split_documents["CATEGORY-F"] = split_documents["EFFECTIVE_CATEGORY"]
        split_documents["SUBCATEGORY-F"] = split_documents["EFFECTIVE_SUBCATEGORY"]
        split_documents["ALLOCATION_SOURCE"] = "H-P distribuido"
        for column in ("EXEMPT-F", "NET-CLP-F", "VAT-CLP-F", "TOTAL-CLP-F"):
            split_documents[column] = (
                split_documents[column] * split_documents["ALLOCATION_RATIO"]
            )
        split_documents["PAID_CLP_SOURCE"] = split_documents["ALLOCATION_PAID_CLP"]
        split_documents["PAID_UF_SOURCE"] = split_documents["ALLOCATION_PAID_UF"]
        split_documents["PAYMENT_COUNT"] = split_documents["ALLOCATION_PAYMENT_COUNT"]
        split_documents["FIRST_PAYMENT_DATE"] = split_documents["ALLOCATION_FIRST_DATE"]
        split_documents["LAST_PAYMENT_DATE"] = split_documents["ALLOCATION_LAST_DATE"]
        split_documents["NET-UF-F"] = (
            split_documents["NET-CLP-F"] / split_documents["UF-F"]
        )
        split_documents["VAT-UF-F"] = (
            split_documents["VAT-CLP-F"] / split_documents["UF-F"]
        )
        split_documents["TOTAL-UF-F"] = (
            split_documents["TOTAL-CLP-F"] / split_documents["UF-F"]
        )
        split_documents["AMOUNT_DIFFERENCE_CLP"] = (
            split_documents["PAID_CLP_SOURCE"].abs()
            - split_documents["TOTAL-CLP-F"].abs()
        )
        split_documents["REVIEW_REASON"] = np.where(
            split_documents["AMOUNT_DIFFERENCE_CLP"].abs() > 1,
            "Diferencia entre documento y pago",
            "",
        )
        documents = pd.concat([regular_documents, split_documents], ignore_index=True)

    document_ledger = pd.DataFrame(
        {
            "RECORD_TYPE": "DOCUMENT",
            "DOCUMENT_ID": documents["document_id"],
            "PAYMENT_ID": pd.Series(pd.NA, index=documents.index, dtype="Int64"),
            "SOURCE_KIND": "SII",
            "RUT_KEY": documents["RUT_KEY"],
            "RUT_COMPLETO": documents["supplier_rut"] + "-" + documents["supplier_dv"].fillna(""),
            "SUPPLIER-F": documents["supplier_name"].fillna("Proveedor sin identificar"),
            "DOCUMENT TYPE": documents["document_type"].astype("Int64"),
            "INVOICE-F": documents["document_number"],
            "FOLIO_KEY": documents["FOLIO_KEY"],
            "FOLIO_BASE": documents["FOLIO_BASE"],
            "DATE-F": documents["DATE-F"],
            "YEAR-F": documents["DATE-F"].dt.year.astype("Int64"),
            "CATEGORY-F": pd.to_numeric(documents["CATEGORY-F"], errors="coerce").astype("Int64"),
            "SUBCATEGORY-F": pd.to_numeric(documents["SUBCATEGORY-F"], errors="coerce").astype("Int64"),
            "ALLOCATION_SOURCE": documents["ALLOCATION_SOURCE"],
            "EXEMPT-F": documents["EXEMPT-F"],
            "NET-CLP-F": documents["NET-CLP-F"],
            "VAT-CLP-F": documents["VAT-CLP-F"],
            "TOTAL-CLP-F": documents["TOTAL-CLP-F"],
            "UF-F": documents["UF-F"],
            "NET-UF-F": documents["NET-UF-F"],
            "VAT-UF-F": documents["VAT-UF-F"],
            "TOTAL-UF-F": documents["TOTAL-UF-F"],
            "PAYMENT_COUNT": documents["PAYMENT_COUNT"],
            "COST_ROW_COUNT": documents["DOC_KEY_COUNT"],
            "DOCUMENT_TYPE_COUNT": documents["DOCUMENT_TYPE_COUNT"],
            "FIRST_PAYMENT_DATE": pd.to_datetime(documents["FIRST_PAYMENT_DATE"], errors="coerce"),
            "LAST_PAYMENT_DATE": pd.to_datetime(documents["LAST_PAYMENT_DATE"], errors="coerce"),
            "PAID_CLP_SOURCE": documents["PAID_CLP_SOURCE"],
            "PAYMENT_STATUS": documents["PAYMENT_STATUS"],
            "PAYMENT_REVIEW_STATUS": documents["PAYMENT_REVIEW_STATUS"],
            "PAYMENT_REVIEWED_AT": documents["payment_reviewed_at"],
            "MATCH_METHOD": documents["MATCH_METHOD"],
            "COST_TREATMENT": documents["COST_TREATMENT"],
            "INCLUDED_IN_COST": documents["INCLUDED_IN_COST"],
            "REVIEW_REASON": documents["REVIEW_REASON"],
            "AMOUNT_DIFFERENCE_CLP": documents["AMOUNT_DIFFERENCE_CLP"],
            "DECISION_NOTES": documents["decision_notes"],
            "CREDITED_CLP": documents["CREDITED_CLP"],
            "CREDIT_NOTE_STATUS": documents["CREDIT_NOTE_STATUS"],
        }
    )

    matched_payment_ids = set(links["payment_id"].astype(int)) if not links.empty else set()
    payment_only = payments[~payments["payment_id"].isin(matched_payment_ids)].copy()
    for column in (
        "exempt_amount_clp", "net_amount_clp", "vat_amount_clp", "gross_amount_clp",
        "paid_amount_clp", "exempt_amount_uf", "net_amount_uf", "vat_amount_uf",
        "gross_amount_uf", "paid_amount_uf", "uf_value",
    ):
        payment_only[column] = pd.to_numeric(payment_only[column], errors="coerce").fillna(0)
    payment_only["DATE-F"] = pd.to_datetime(payment_only["payment_date"], errors="coerce")
    payment_only["CATEGORY-F"] = payment_only["manual_category"].where(
        payment_only["manual_category"].notna(),
        payment_only["RULE_CATEGORY"].where(
            payment_only["RULE_CATEGORY"].notna(), payment_only["cost_center_cat"]
        ),
    )
    payment_only["SUBCATEGORY-F"] = payment_only["manual_subcategory"].where(
        payment_only["manual_subcategory"].notna(),
        payment_only["RULE_SUBCATEGORY"].where(
            payment_only["RULE_SUBCATEGORY"].notna(), payment_only["cost_center_sub_cat"]
        ),
    )
    payment_only["COST_TREATMENT"] = payment_only["manual_treatment"].where(
        payment_only["manual_treatment"].notna(), payment_only["RULE_TREATMENT"]
    ).fillna("PENDING")
    payment_only["INCLUDED_IN_COST"] = payment_only["COST_TREATMENT"] == "COST"
    payment_only["NET-CLP-F"] = _cost_value(
        payment_only["net_amount_clp"], payment_only["exempt_amount_clp"],
        payment_only["paid_amount_clp"],
    )
    payment_only["UF-F"] = pd.to_numeric(
        payment_only["official_uf_value"], errors="coerce"
    ).where(lambda values: values > 0)
    payment_only["NET-UF-F"] = payment_only["NET-CLP-F"] / payment_only["UF-F"]
    payment_only["VAT-UF-F"] = payment_only["vat_amount_clp"] / payment_only["UF-F"]
    payment_only["TOTAL-UF-F"] = payment_only["paid_amount_clp"] / payment_only["UF-F"]
    payment_only["REVIEW_REASON"] = "Pago sin documento SII"
    payment_only.loc[payment_only["decision_status"] == "RESOLVED", "REVIEW_REASON"] = ""
    payment_only.loc[
        payment_only["RULE_APPLIED"] & (payment_only["COST_TREATMENT"] != "PENDING"),
        "REVIEW_REASON",
    ] = ""
    payment_only["ALLOCATION_SOURCE"] = np.where(
        payment_only["manual_category"].notna() & payment_only["manual_subcategory"].notna(),
        "Manual",
        np.where(
            payment_only["RULE_APPLIED"],
            "Regla",
            np.where(
                payment_only["cost_center_cat"].notna() & payment_only["cost_center_sub_cat"].notna(),
                "H-P",
                "Pendiente",
            ),
        ),
    )
    payment_ledger = pd.DataFrame(
        {
            "RECORD_TYPE": "PAYMENT",
            "DOCUMENT_ID": pd.Series(pd.NA, index=payment_only.index, dtype="Int64"),
            "PAYMENT_ID": payment_only["payment_id"].astype("Int64"),
            "SOURCE_KIND": "H-P",
            "RUT_KEY": payment_only["RUT_KEY"],
            "RUT_COMPLETO": payment_only["supplier_rut"].fillna("")
            + payment_only["supplier_dv"].fillna("").map(lambda value: f"-{value}" if value else ""),
            "SUPPLIER-F": payment_only["supplier_name"].fillna("Proveedor sin identificar"),
            "DOCUMENT TYPE": pd.Series(pd.NA, index=payment_only.index, dtype="Int64"),
            "INVOICE-F": payment_only["invoice_number_raw"],
            "FOLIO_KEY": payment_only["FOLIO_KEY"],
            "FOLIO_BASE": payment_only["FOLIO_BASE"],
            "DATE-F": payment_only["DATE-F"],
            "YEAR-F": payment_only["DATE-F"].dt.year.astype("Int64"),
            "CATEGORY-F": pd.to_numeric(payment_only["CATEGORY-F"], errors="coerce").astype("Int64"),
            "SUBCATEGORY-F": pd.to_numeric(payment_only["SUBCATEGORY-F"], errors="coerce").astype("Int64"),
            "ALLOCATION_SOURCE": payment_only["ALLOCATION_SOURCE"],
            "EXEMPT-F": payment_only["exempt_amount_clp"],
            "NET-CLP-F": payment_only["NET-CLP-F"],
            "VAT-CLP-F": payment_only["vat_amount_clp"],
            "TOTAL-CLP-F": payment_only["paid_amount_clp"],
            "UF-F": payment_only["UF-F"],
            "NET-UF-F": payment_only["NET-UF-F"],
            "VAT-UF-F": payment_only["VAT-UF-F"],
            "TOTAL-UF-F": payment_only["TOTAL-UF-F"],
            "PAYMENT_COUNT": 1,
            "COST_ROW_COUNT": 0,
            "DOCUMENT_TYPE_COUNT": 0,
            "FIRST_PAYMENT_DATE": payment_only["DATE-F"],
            "LAST_PAYMENT_DATE": payment_only["DATE-F"],
            "PAID_CLP_SOURCE": payment_only["paid_amount_clp"],
            "PAYMENT_STATUS": "Pago sin documento",
            "PAYMENT_REVIEW_STATUS": "PAYMENT_ONLY",
            "PAYMENT_REVIEWED_AT": pd.NaT,
            "MATCH_METHOD": "Sin documento SII",
            "COST_TREATMENT": payment_only["COST_TREATMENT"],
            "INCLUDED_IN_COST": payment_only["INCLUDED_IN_COST"],
            "REVIEW_REASON": payment_only["REVIEW_REASON"],
            "AMOUNT_DIFFERENCE_CLP": 0.0,
            "DECISION_NOTES": payment_only["decision_notes"],
            "CREDITED_CLP": 0.0,
            "CREDIT_NOTE_STATUS": "",
        }
    )

    ledger = pd.concat([document_ledger, payment_ledger], ignore_index=True)
    ledger["CATEGORY_NAME"] = ledger["CATEGORY-F"].map(category_label)
    ledger["SUBCATEGORY_NAME"] = ledger.apply(
        lambda row: subcategory_label(row["CATEGORY-F"], row["SUBCATEGORY-F"]), axis=1
    )
    ledger["PAID"] = (
        (ledger["PAYMENT_STATUS"] == "Pagado")
        | (ledger["PAYMENT_STATUS"] == "Anulada por NC")
        | ((ledger["RECORD_TYPE"] == "PAYMENT") & ledger["INCLUDED_IN_COST"])
        | ((ledger["DOCUMENT TYPE"] == 61) & ledger["INCLUDED_IN_COST"])
    )
    return ledger


def get_historical_cost_center_proposals(
    ledger: pd.DataFrame | None = None,
    legacy_path: Path = LEGACY_DATABASE2_PATH,
    database_path: Path = DATABASE_PATH,
) -> pd.DataFrame:
    """Return exact old-database matches for current cost-center pendings."""
    columns = [
        "DOCUMENT_ID", "SUPPLIER-F", "RUT_COMPLETO", "DOCUMENT TYPE", "INVOICE-F",
        "DATE-F", "TOTAL-CLP-F", "OLD_CATEGORY", "OLD_SUBCATEGORY", "OLD_CENTER",
        "PROPOSAL_STATUS", "OLD_MATCH_KEY",
    ]
    path = Path(legacy_path).expanduser()
    if not path.exists():
        return pd.DataFrame(columns=columns)
    if ledger is None:
        ledger = build_cost_control()
    pending = ledger[
        (ledger["RECORD_TYPE"] == "DOCUMENT")
        & (ledger["DOCUMENT TYPE"] != 61)
        & (ledger["REVIEW_REASON"] == "Centro de costo pendiente")
    ].copy()
    if pending.empty:
        return pd.DataFrame(columns=columns)

    legacy = pd.read_csv(path, sep=";", dtype=str, encoding="utf-8-sig")
    required = {"RUT-F", "DOCUMENT TYPE", "INVOICE-F", "CATEGORY-F", "SUBCATEGORY-F"}
    if not required.issubset(legacy.columns):
        raise ValueError("database2.csv no contiene las columnas históricas esperadas.")
    legacy = legacy.rename(
        columns={
            "RUT-F": "RUT_F", "DOCUMENT TYPE": "DOCUMENT_TYPE",
            "INVOICE-F": "INVOICE_F", "CATEGORY-F": "CATEGORY_F",
            "SUBCATEGORY-F": "SUBCATEGORY_F",
        }
    )

    legacy_by_key: dict[tuple[str, str, str], list[tuple[int, int]]] = defaultdict(list)
    for row in legacy.itertuples(index=False):
        category = normalize_legacy_key(row.CATEGORY_F)
        subcategory = normalize_legacy_key(row.SUBCATEGORY_F)
        if not category or not subcategory:
            continue
        key = (
            normalize_legacy_key(row.RUT_F),
            normalize_legacy_key(row.DOCUMENT_TYPE),
            base_invoice_key(row.INVOICE_F),
        )
        try:
            pair = (int(category), int(subcategory))
        except (TypeError, ValueError):
            continue
        if pair not in legacy_by_key[key]:
            legacy_by_key[key].append(pair)

    centers = query_dataframe(
        """
        SELECT category_code, subcategory_code, category_name, subcategory_name
        FROM cost_centers WHERE is_active = 1
        """,
        path=database_path,
    )
    center_lookup = {
        (int(row.category_code), int(row.subcategory_code)):
        f"{int(row.category_code)} · {row.category_name} / {int(row.subcategory_code)} · {row.subcategory_name}"
        for row in centers.itertuples(index=False)
    }
    proposals = []
    for _, row in pending.iterrows():
        key = (
            normalize_legacy_key(row["RUT_KEY"]),
            normalize_legacy_key(row["DOCUMENT TYPE"]),
            base_invoice_key(row["INVOICE-F"]),
        )
        pairs = legacy_by_key.get(key, [])
        if len(pairs) != 1:
            continue
        category, subcategory = pairs[0]
        proposals.append(
            {
                "DOCUMENT_ID": int(row["DOCUMENT_ID"]),
                "SUPPLIER-F": row["SUPPLIER-F"],
                "RUT_COMPLETO": row["RUT_COMPLETO"],
                "DOCUMENT TYPE": int(row["DOCUMENT TYPE"]),
                "INVOICE-F": row["INVOICE-F"],
                "DATE-F": row["DATE-F"],
                "TOTAL-CLP-F": float(row["TOTAL-CLP-F"] or 0),
                "OLD_CATEGORY": category,
                "OLD_SUBCATEGORY": subcategory,
                "OLD_CENTER": center_lookup.get((category, subcategory), "Centro histórico no vigente"),
                "PROPOSAL_STATUS": "APROBABLE" if (category, subcategory) in center_lookup else "CENTRO_NO_VIGENTE",
                "OLD_MATCH_KEY": f"{key[0]} | tipo {key[1]} | folio {key[2]}",
            }
        )
    return pd.DataFrame(proposals, columns=columns).sort_values(
        ["PROPOSAL_STATUS", "OLD_CATEGORY", "OLD_SUBCATEGORY", "SUPPLIER-F", "INVOICE-F"]
    ).reset_index(drop=True)


def get_cost_centers() -> pd.DataFrame:
    return query_dataframe(
        """
        SELECT category_code, subcategory_code, category_name, subcategory_name
        FROM cost_centers
        WHERE is_active = 1
        ORDER BY category_code, subcategory_code
        """
    )


def save_review_decision(
    item_type: str,
    record_id: int,
    category_code: int | None,
    subcategory_code: int | None,
    cost_treatment: str,
    notes: str,
    database_path: Path = DATABASE_PATH,
) -> None:
    item_type = item_type.upper()
    if item_type not in {"DOCUMENT", "PAYMENT"}:
        raise ValueError("Tipo de registro no válido.")
    if cost_treatment not in {"COST", "NON_COST", "PENDING"}:
        raise ValueError("Tratamiento de costo no válido.")
    if (category_code is None) != (subcategory_code is None):
        raise ValueError("Selecciona grupo y centro de costo.")

    # Pandas entrega enteros NumPy desde los selectores. SQLite los interpreta
    # como datos binarios, por lo que deben convertirse antes de validar la FK.
    category_code = int(category_code) if category_code is not None else None
    subcategory_code = int(subcategory_code) if subcategory_code is not None else None

    id_column = "document_id" if item_type == "DOCUMENT" else "payment_id"
    other_column = "payment_id" if item_type == "DOCUMENT" else "document_id"
    review_status = "PENDING" if cost_treatment == "PENDING" else "RESOLVED"
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with closing(sqlite3.connect(database_path)) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 5000")
        existing = connection.execute(
            f"SELECT decision_id FROM review_decisions WHERE {id_column} = ?", (record_id,)
        ).fetchone()
        if existing:
            connection.execute(
                """
                UPDATE review_decisions
                SET category_code = ?, subcategory_code = ?, cost_treatment = ?,
                    review_status = ?, notes = ?, updated_at = ?
                WHERE decision_id = ?
                """,
                (
                    category_code, subcategory_code, cost_treatment, review_status,
                    notes.strip() or None, now, existing[0],
                ),
            )
        else:
            connection.execute(
                f"""
                INSERT INTO review_decisions(
                    item_type, {id_column}, {other_column}, category_code,
                    subcategory_code, cost_treatment, review_status, notes,
                    created_at, updated_at
                ) VALUES (?, ?, NULL, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    item_type, record_id, category_code, subcategory_code,
                    cost_treatment, review_status, notes.strip() or None, now, now,
                ),
            )
        connection.commit()


def _validate_bulk_decision(
    item_type: str,
    record_ids: list[int] | tuple[int, ...],
    category_code: int | None,
    subcategory_code: int | None,
    cost_treatment: str,
) -> tuple[str, list[int], int | None, int | None]:
    item_type = item_type.upper()
    if item_type not in {"DOCUMENT", "PAYMENT"}:
        raise ValueError("Tipo de registro no válido.")
    if cost_treatment not in {"COST", "NON_COST", "PENDING"}:
        raise ValueError("Tratamiento de costo no válido.")
    if (category_code is None) != (subcategory_code is None):
        raise ValueError("Selecciona grupo y centro de costo.")
    clean_ids = sorted({int(record_id) for record_id in record_ids})
    if not clean_ids:
        raise ValueError("No hay registros seleccionados.")
    category_code = int(category_code) if category_code is not None else None
    subcategory_code = int(subcategory_code) if subcategory_code is not None else None
    return item_type, clean_ids, category_code, subcategory_code


def save_bulk_review_decisions(
    item_type: str,
    record_ids: list[int] | tuple[int, ...],
    category_code: int | None,
    subcategory_code: int | None,
    cost_treatment: str,
    notes: str,
    action_type: str,
    total_amount_clp: float,
    supplier_rut_key: str = "",
    create_rule: bool = False,
    rule_document_type: int = 0,
    database_path: Path = DATABASE_PATH,
) -> str:
    item_type, clean_ids, category_code, subcategory_code = _validate_bulk_decision(
        item_type, record_ids, category_code, subcategory_code, cost_treatment
    )
    action_type = action_type.upper()
    if action_type not in {"SUPPLIER", "SELECTION"}:
        raise ValueError("Tipo de acción masiva no válido.")
    supplier_rut_key = normalize_legacy_key(supplier_rut_key)
    if create_rule and not supplier_rut_key:
        raise ValueError("La regla requiere un RUT de proveedor.")
    if create_rule and cost_treatment == "PENDING":
        raise ValueError("Una regla futura no puede dejar el tratamiento pendiente.")
    if create_rule and (category_code is None or subcategory_code is None):
        raise ValueError("La regla requiere un centro de costo.")
    rule_document_type = int(rule_document_type or 0) if item_type == "DOCUMENT" else 0

    create_database_backup(database_path, database_path.parent / "backups", "bulk_review")
    batch_id = uuid.uuid4().hex
    id_column = "document_id" if item_type == "DOCUMENT" else "payment_id"
    other_column = "payment_id" if item_type == "DOCUMENT" else "document_id"
    review_status = "PENDING" if cost_treatment == "PENDING" else "RESOLVED"
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")

    with closing(sqlite3.connect(database_path)) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 5000")
        try:
            connection.execute("BEGIN IMMEDIATE")
            center_exists = connection.execute(
                """
                SELECT 1 FROM cost_centers
                WHERE category_code = ? AND subcategory_code = ? AND is_active = 1
                """,
                (category_code, subcategory_code),
            ).fetchone()
            if category_code is not None and center_exists is None:
                raise ValueError("El centro de costo seleccionado no existe.")

            existing: dict[int, tuple] = {}
            for start in range(0, len(clean_ids), 800):
                chunk = clean_ids[start : start + 800]
                placeholders = ",".join("?" for _ in chunk)
                rows = connection.execute(
                    f"""
                    SELECT {id_column}, decision_id, category_code, subcategory_code,
                           cost_treatment, review_status, notes
                    FROM review_decisions
                    WHERE {id_column} IN ({placeholders})
                    """,
                    chunk,
                ).fetchall()
                existing.update({int(row[0]): row[1:] for row in rows})

            replaced_rule_id = None
            rule_id = None
            if create_rule:
                replaced = connection.execute(
                    """
                    SELECT rule_id FROM allocation_rules
                    WHERE supplier_rut_key = ? AND item_type = ?
                      AND document_type = ? AND is_active = 1
                    """,
                    (supplier_rut_key, item_type, rule_document_type),
                ).fetchone()
                if replaced:
                    replaced_rule_id = int(replaced[0])
                    connection.execute(
                        "UPDATE allocation_rules SET is_active = 0, updated_at = ? WHERE rule_id = ?",
                        (now, replaced_rule_id),
                    )
                cursor = connection.execute(
                    """
                    INSERT INTO allocation_rules(
                        supplier_rut_key, item_type, document_type, category_code,
                        subcategory_code, cost_treatment, notes, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        supplier_rut_key, item_type, rule_document_type, category_code,
                        subcategory_code, cost_treatment, notes.strip() or None, now, now,
                    ),
                )
                rule_id = int(cursor.lastrowid)

            connection.execute(
                """
                INSERT INTO reconciliation_batches(
                    batch_id, action_type, item_type, supplier_rut_key, record_count,
                    total_amount_clp, category_code, subcategory_code, cost_treatment,
                    notes, rule_id, replaced_rule_id, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    batch_id, action_type, item_type, supplier_rut_key or None,
                    len(clean_ids), float(total_amount_clp or 0), category_code,
                    subcategory_code, cost_treatment, notes.strip() or None,
                    rule_id, replaced_rule_id, now,
                ),
            )

            history_rows = []
            for record_id in clean_ids:
                previous = existing.get(record_id)
                history_rows.append(
                    (
                        batch_id, item_type, record_id, int(previous is not None),
                        *(previous[1:] if previous is not None else (None, None, None, None, None)),
                    )
                )
            connection.executemany(
                """
                INSERT INTO reconciliation_batch_items(
                    batch_id, item_type, record_id, previous_decision_exists,
                    previous_category_code, previous_subcategory_code,
                    previous_cost_treatment, previous_review_status, previous_notes
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                history_rows,
            )

            update_rows = [
                (
                    category_code, subcategory_code, cost_treatment, review_status,
                    notes.strip() or None, now, existing[record_id][0], record_id,
                )
                for record_id in clean_ids if record_id in existing
            ]
            if update_rows:
                connection.executemany(
                    f"""
                    UPDATE review_decisions
                    SET category_code = ?, subcategory_code = ?, cost_treatment = ?,
                        review_status = ?, notes = ?, updated_at = ?
                    WHERE decision_id = ? AND {id_column} = ?
                    """,
                    update_rows,
                )
            insert_rows = [
                (
                    item_type, record_id, category_code, subcategory_code,
                    cost_treatment, review_status, notes.strip() or None, now, now,
                )
                for record_id in clean_ids if record_id not in existing
            ]
            if insert_rows:
                connection.executemany(
                    f"""
                    INSERT INTO review_decisions(
                        item_type, {id_column}, {other_column}, category_code,
                        subcategory_code, cost_treatment, review_status, notes,
                        created_at, updated_at
                    ) VALUES (?, ?, NULL, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    insert_rows,
                )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
    return batch_id


def get_reconciliation_batches(database_path: Path = DATABASE_PATH) -> pd.DataFrame:
    return query_dataframe(
        """
        SELECT b.batch_id, b.created_at, b.action_type, b.item_type,
               b.supplier_rut_key, b.record_count, b.total_amount_clp,
               b.category_code, b.subcategory_code, c.subcategory_name,
               b.cost_treatment, b.notes, b.rule_id, b.reversed_at
        FROM reconciliation_batches b
        LEFT JOIN cost_centers c
          ON c.category_code = b.category_code
         AND c.subcategory_code = b.subcategory_code
        ORDER BY b.created_at DESC
        """,
        path=database_path,
    )


def get_allocation_rules(database_path: Path = DATABASE_PATH) -> pd.DataFrame:
    return query_dataframe(
        """
        SELECT r.rule_id, r.supplier_rut_key, r.item_type, r.document_type,
               r.category_code, r.subcategory_code, c.subcategory_name,
               r.cost_treatment, r.notes, r.created_at
        FROM allocation_rules r
        JOIN cost_centers c
          ON c.category_code = r.category_code
         AND c.subcategory_code = r.subcategory_code
        WHERE r.is_active = 1
        ORDER BY r.supplier_rut_key, r.item_type, r.document_type
        """,
        path=database_path,
    )


def reverse_reconciliation_batch(
    batch_id: str,
    database_path: Path = DATABASE_PATH,
) -> int:
    create_database_backup(database_path, database_path.parent / "backups", "reverse_review")
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with closing(sqlite3.connect(database_path)) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 5000")
        try:
            connection.execute("BEGIN IMMEDIATE")
            batch = connection.execute(
                """
                SELECT item_type, rule_id, replaced_rule_id, reversed_at
                FROM reconciliation_batches WHERE batch_id = ?
                """,
                (batch_id,),
            ).fetchone()
            if batch is None:
                raise ValueError("La acción masiva no existe.")
            if batch[3] is not None:
                raise ValueError("La acción masiva ya fue revertida.")
            item_type, rule_id, replaced_rule_id, _ = batch
            id_column = "document_id" if item_type == "DOCUMENT" else "payment_id"
            items = connection.execute(
                """
                SELECT record_id, previous_decision_exists, previous_category_code,
                       previous_subcategory_code, previous_cost_treatment,
                       previous_review_status, previous_notes
                FROM reconciliation_batch_items WHERE batch_id = ?
                """,
                (batch_id,),
            ).fetchall()
            for item in items:
                record_id, existed, category, subcategory, treatment, status, notes = item
                if existed:
                    connection.execute(
                        f"""
                        UPDATE review_decisions
                        SET category_code = ?, subcategory_code = ?, cost_treatment = ?,
                            review_status = ?, notes = ?, updated_at = ?
                        WHERE {id_column} = ?
                        """,
                        (category, subcategory, treatment, status, notes, now, record_id),
                    )
                else:
                    connection.execute(
                        f"DELETE FROM review_decisions WHERE {id_column} = ?",
                        (record_id,),
                    )
            if rule_id is not None:
                connection.execute(
                    "UPDATE allocation_rules SET is_active = 0, updated_at = ? WHERE rule_id = ?",
                    (now, rule_id),
                )
            if replaced_rule_id is not None:
                connection.execute(
                    "UPDATE allocation_rules SET is_active = 1, updated_at = ? WHERE rule_id = ?",
                    (now, replaced_rule_id),
                )
            connection.execute(
                "UPDATE reconciliation_batches SET reversed_at = ? WHERE batch_id = ?",
                (now, batch_id),
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
    return len(items)


def deactivate_allocation_rule(
    rule_id: int,
    database_path: Path = DATABASE_PATH,
) -> None:
    create_database_backup(database_path, database_path.parent / "backups", "disable_rule")
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with closing(sqlite3.connect(database_path)) as connection:
        connection.execute("PRAGMA busy_timeout = 5000")
        connection.execute(
            "UPDATE allocation_rules SET is_active = 0, updated_at = ? WHERE rule_id = ?",
            (now, int(rule_id)),
        )
        connection.commit()


def save_manual_match(
    document_id: int,
    payment_id: int,
    notes: str = "",
    database_path: Path = DATABASE_PATH,
) -> None:
    with closing(sqlite3.connect(database_path)) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 5000")
        connection.execute(
            """
            INSERT OR IGNORE INTO manual_matches(document_id, payment_id, notes)
            VALUES (?, ?, ?)
            """,
            (document_id, payment_id, notes.strip() or None),
        )
        connection.commit()


def save_manual_matches_bulk(
    matches: list[tuple[int, int]],
    notes: str = "",
    database_path: Path = DATABASE_PATH,
) -> int:
    clean_matches = sorted({(int(document_id), int(payment_id)) for document_id, payment_id in matches})
    if not clean_matches:
        raise ValueError("No hay cruces seleccionados.")
    create_database_backup(database_path, database_path.parent / "backups", "payment_match")
    with closing(sqlite3.connect(database_path)) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 5000")
        before = connection.total_changes
        connection.executemany(
            """
            INSERT OR IGNORE INTO manual_matches(document_id, payment_id, notes)
            VALUES (?, ?, ?)
            """,
            [(document_id, payment_id, notes.strip() or None) for document_id, payment_id in clean_matches],
        )
        inserted = connection.total_changes - before
        connection.commit()
    return inserted


def save_payment_confirmation(
    document_ids: list[int] | tuple[int, ...],
    payment_status: str,
    notes: str = "",
    database_path: Path = DATABASE_PATH,
) -> int:
    payment_status = payment_status.upper()
    if payment_status not in {"PAID_CONFIRMED", "UNPAID_CONFIRMED"}:
        raise ValueError("Estado de pago no válido.")
    clean_ids = sorted({int(document_id) for document_id in document_ids})
    if not clean_ids:
        raise ValueError("No hay documentos seleccionados.")
    create_database_backup(database_path, database_path.parent / "backups", "payment_review")
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    note = notes.strip()
    with closing(sqlite3.connect(database_path)) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 5000")
        try:
            connection.execute("BEGIN IMMEDIATE")
            existing_ids: set[int] = set()
            for start in range(0, len(clean_ids), 800):
                chunk = clean_ids[start : start + 800]
                placeholders = ",".join("?" for _ in chunk)
                rows = connection.execute(
                    f"SELECT document_id FROM review_decisions WHERE document_id IN ({placeholders})",
                    chunk,
                ).fetchall()
                existing_ids.update(int(row[0]) for row in rows)
            connection.executemany(
                """
                UPDATE review_decisions
                SET payment_review_status = ?, payment_reviewed_at = ?,
                    notes = CASE WHEN ? = '' THEN notes ELSE ? END, updated_at = ?
                WHERE document_id = ?
                """,
                [
                    (payment_status, now, note, note, now, document_id)
                    for document_id in clean_ids if document_id in existing_ids
                ],
            )
            connection.executemany(
                """
                INSERT INTO review_decisions(
                    item_type, document_id, payment_id, category_code, subcategory_code,
                    cost_treatment, review_status, notes, payment_review_status,
                    payment_reviewed_at, created_at, updated_at
                ) VALUES ('DOCUMENT', ?, NULL, NULL, NULL, 'COST', 'PENDING', ?,
                          ?, ?, ?, ?)
                """,
                [
                    (document_id, note or None, payment_status, now, now, now)
                    for document_id in clean_ids if document_id not in existing_ids
                ],
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
    return len(clean_ids)


def save_paid_confirmation(
    document_ids: list[int] | tuple[int, ...],
    notes: str = "",
    database_path: Path = DATABASE_PATH,
) -> int:
    return save_payment_confirmation(
        document_ids, "PAID_CONFIRMED", notes, database_path
    )


def save_unpaid_confirmation(
    document_ids: list[int] | tuple[int, ...],
    notes: str = "",
    database_path: Path = DATABASE_PATH,
) -> int:
    return save_payment_confirmation(
        document_ids, "UNPAID_CONFIRMED", notes, database_path
    )


def get_credit_note_decisions(database_path: Path = DATABASE_PATH) -> pd.DataFrame:
    return query_dataframe(
        """
        SELECT d.document_id AS credit_note_id, d.supplier_name, d.supplier_rut,
               d.supplier_dv, d.document_number, d.issue_date,
               ABS(d.total_amount_clp) AS amount_clp,
               c.decision_type, c.invoice_document_id, c.allocated_amount_clp,
               c.notes, c.updated_at, c.classification, c.match_method,
               xr.xml_reference_count, xr.matched_reference_count,
               xr.reference_codes, xr.reference_classifications,
               i.document_number AS invoice_number, i.issue_date AS invoice_date,
               ABS(i.total_amount_clp) AS invoice_amount_clp
        FROM documents d
        LEFT JOIN credit_note_decisions c ON c.credit_note_id = d.document_id
        LEFT JOIN documents i ON i.document_id = c.invoice_document_id
        LEFT JOIN (
            SELECT credit_note_document_id,
                   COUNT(*) AS xml_reference_count,
                   SUM(CASE WHEN matched_document_id IS NOT NULL THEN 1 ELSE 0 END) AS matched_reference_count,
                   GROUP_CONCAT(DISTINCT reference_code) AS reference_codes,
                   GROUP_CONCAT(DISTINCT classification) AS reference_classifications
            FROM credit_note_xml_references
            GROUP BY credit_note_document_id
        ) xr ON xr.credit_note_document_id = d.document_id
        WHERE d.document_type = 61
        ORDER BY CASE WHEN c.decision_type IS NULL THEN 0 ELSE 1 END,
                 d.issue_date DESC, d.document_id DESC
        """,
        path=database_path,
    )


def get_credit_note_candidates(
    credit_note_id: int,
    database_path: Path = DATABASE_PATH,
) -> pd.DataFrame:
    return query_dataframe(
        """
        SELECT i.document_id AS invoice_document_id, i.supplier_name,
               i.document_type, i.document_number, i.issue_date,
               ABS(i.total_amount_clp) AS amount_clp,
               ABS(ABS(i.total_amount_clp) - ABS(n.total_amount_clp)) AS amount_difference_clp
        FROM documents n
        JOIN documents i
          ON i.supplier_rut = n.supplier_rut
         AND COALESCE(i.supplier_dv, '') = COALESCE(n.supplier_dv, '')
         AND i.document_type <> 61
         AND i.issue_date <= n.issue_date
        WHERE n.document_id = ? AND n.document_type = 61
        ORDER BY amount_difference_clp, i.issue_date DESC
        LIMIT 100
        """,
        params=(int(credit_note_id),),
        path=database_path,
    )


def save_credit_note_decision(
    credit_note_id: int,
    decision_type: str,
    invoice_document_id: int | None = None,
    notes: str = "",
    database_path: Path = DATABASE_PATH,
) -> None:
    decision_type = decision_type.upper()
    if decision_type not in {"LINKED", "STANDALONE"}:
        raise ValueError("Decisión de nota de crédito no válida.")
    if decision_type == "LINKED" and invoice_document_id is None:
        raise ValueError("Selecciona la factura que será rebajada o anulada.")
    if decision_type == "STANDALONE":
        invoice_document_id = None
    create_database_backup(database_path, database_path.parent / "backups", "credit_note")
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with closing(sqlite3.connect(database_path)) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 5000")
        credit_note = connection.execute(
            "SELECT supplier_rut, supplier_dv, ABS(total_amount_clp) FROM documents WHERE document_id = ? AND document_type = 61",
            (int(credit_note_id),),
        ).fetchone()
        if credit_note is None:
            raise ValueError("La nota de crédito no existe.")
        allocated_amount = 0.0
        if invoice_document_id is not None:
            invoice = connection.execute(
                "SELECT supplier_rut, supplier_dv FROM documents WHERE document_id = ? AND document_type <> 61",
                (int(invoice_document_id),),
            ).fetchone()
            if invoice is None or invoice[:2] != credit_note[:2]:
                raise ValueError("La factura debe pertenecer al mismo proveedor.")
            allocated_amount = float(credit_note[2] or 0)
        connection.execute(
            """
            INSERT INTO credit_note_decisions(
                credit_note_id, decision_type, invoice_document_id,
                allocated_amount_clp, notes, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(credit_note_id) DO UPDATE SET
                decision_type = excluded.decision_type,
                invoice_document_id = excluded.invoice_document_id,
                allocated_amount_clp = excluded.allocated_amount_clp,
                notes = excluded.notes,
                updated_at = excluded.updated_at
            """,
            (
                int(credit_note_id), decision_type, invoice_document_id,
                allocated_amount, notes.strip() or None, now, now,
            ),
        )
        connection.commit()


def apply_global_credit_note_adjustments(
    category_code: int = 400,
    subcategory_code: int = 402,
    database_path: Path = DATABASE_PATH,
    backup_dir: Path | None = None,
) -> int:
    """Apply XML global adjustments as negative cost without creating a payment."""
    create_database_backup(
        database_path,
        backup_dir or database_path.parent / "backups",
        "global_credit_note_adjustments",
    )
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with closing(sqlite3.connect(database_path)) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 5000")
        rows = connection.execute(
            """
            SELECT d.document_id, ABS(d.total_amount_clp), MIN(r.reference_id)
            FROM documents d
            JOIN credit_note_xml_references r
              ON r.credit_note_document_id = d.document_id
             AND r.classification = 'GLOBAL_ADJUSTMENT'
            LEFT JOIN credit_note_decisions c
              ON c.credit_note_id = d.document_id
            WHERE d.document_type = 61
              AND c.credit_note_id IS NULL
            GROUP BY d.document_id
            """
        ).fetchall()
        for document_id, amount, reference_id in rows:
            connection.execute(
                """
                INSERT INTO credit_note_decisions(
                    credit_note_id, decision_type, invoice_document_id,
                    allocated_amount_clp, notes, created_at, updated_at,
                    source_reference_id, classification, match_method
                ) VALUES (?, 'STANDALONE', NULL, ?, ?, ?, ?, ?, 'GLOBAL_ADJUSTMENT_APPLIED', 'XML_GLOBAL_ADJUSTMENT')
                """,
                (
                    int(document_id),
                    float(amount or 0),
                    "Ajuste global aplicado como descuento al costo; sin pago bancario asociado.",
                    now,
                    now,
                    int(reference_id),
                ),
            )
            updated = connection.execute(
                """
                UPDATE review_decisions
                SET category_code = COALESCE(category_code, ?),
                    subcategory_code = COALESCE(subcategory_code, ?),
                    cost_treatment = 'COST',
                    review_status = 'RESOLVED',
                    notes = COALESCE(notes || ' | ', '') || ?,
                    updated_at = ?
                WHERE document_id = ?
                """,
                (
                    int(category_code),
                    int(subcategory_code),
                    "Ajuste global XML aplicado al costo.",
                    now,
                    int(document_id),
                ),
            )
            if updated.rowcount == 0:
                connection.execute(
                    """
                    INSERT INTO review_decisions(
                        item_type, document_id, category_code, subcategory_code,
                        cost_treatment, review_status, notes, created_at, updated_at
                    ) VALUES ('DOCUMENT', ?, ?, ?, 'COST', 'RESOLVED', ?, ?, ?)
                    """,
                    (
                        int(document_id),
                        int(category_code),
                        int(subcategory_code),
                        "Ajuste global XML aplicado al costo.",
                        now,
                        now,
                    ),
                )
        connection.commit()
    return len(rows)


def get_import_history() -> tuple[pd.DataFrame, pd.DataFrame]:
    sii = query_dataframe(
        """
        SELECT source_period, source_file_name, imported_at, file_hash
        FROM sources
        WHERE source_area = 'SII' AND source_type = 'RCV_COMPRA'
        ORDER BY source_period DESC
        """
    )
    payments = query_dataframe(
        """
        SELECT source_period, source_file_name, import_mode, imported_at,
               valid_row_count, invalid_row_count, first_payment_date,
               last_payment_date, source_hash
        FROM payment_imports
        WHERE is_active = 1
        ORDER BY CASE import_mode WHEN 'monthly' THEN 1 ELSE 2 END,
                 source_period DESC
        """
    )
    return sii, payments
