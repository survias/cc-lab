from __future__ import annotations

from pathlib import Path

import pandas as pd
import streamlit as st

from utils.catalogs import category_label, subcategory_label
from utils.config import (
    ACTIVE_CONTRACTS_PATH,
    BIDDING_PATH,
    CONTRACT_INVOICES_PATH,
    DATABASE_PATH,
    REVENUES_PATH,
)
from utils.database import query_dataframe
from utils.payment_data import get_active_payments
from utils.reconciliation import build_cost_control
from utils.supplier_identity import supplier_key_series


LEGACY_SOURCE_PATHS = {
    "Bidding": BIDDING_PATH,
    "Ingresos": REVENUES_PATH,
    "Contratos activos": ACTIVE_CONTRACTS_PATH,
    "Facturas de contratos": CONTRACT_INVOICES_PATH,
}

PAYABLE_DOCUMENT_TYPES = {22, 33, 34, 46, 56}
SPLIT_INVOICE_SUPPLIER_RUT = "59296220"


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


def _read_csv(path: Path, **kwargs) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(f"No existe la fuente histórica: {path}")
    return pd.read_csv(path, encoding="utf-8-sig", low_memory=False, **kwargs)


def _to_numeric(series: pd.Series) -> pd.Series:
    if pd.api.types.is_numeric_dtype(series):
        return pd.to_numeric(series, errors="coerce")
    normalized = (
        series.astype(str)
        .str.strip()
        .str.replace("\u00a0", "", regex=False)
        .str.replace(".", "", regex=False)
        .str.replace(",", ".", regex=False)
    )
    return pd.to_numeric(normalized, errors="coerce")


@st.cache_data(show_spinner=False)
def _load_payments_cached(database_mtime_ns: int) -> pd.DataFrame:
    current = get_active_payments()
    payments = pd.DataFrame(
        {
            "RUT-P": current["supplier_rut"],
            "DV-P": current["supplier_dv"],
            "SUPPLIER-P": current["supplier_name"],
            "INVOICE-P": current["invoice_number_raw"],
            "DATE-P": current["payment_date"],
            "EXCENT-CLP": current["exempt_amount_clp"],
            "NET-CLP": current["net_amount_clp"],
            "VAT-CLP": current["vat_amount_clp"],
            "OTHER-TAXES-CLP": current["other_taxes_clp"],
            "GROSS-CLP": current["gross_amount_clp"],
            "PAID-CLP": current["paid_amount_clp"],
            "CAT": current["cost_center_cat"],
            "SUB-CAT": current["cost_center_sub_cat"],
            "DOCUMENT-TYPE-HINT": current["document_type_hint"],
            "SOURCE-SHEET": current["source_sheet"],
            "SOURCE-ROW": current["source_row"],
            "PAYMENT-IMPORT-ID": current["payment_import_id"],
        }
    )
    for optional_column in ("CAT", "SUB-CAT", "PAID-CLP"):
        if optional_column not in payments.columns:
            payments[optional_column] = None
    payments["DATE-P"] = pd.to_datetime(payments["DATE-P"], format="mixed", dayfirst=True, errors="coerce")
    payments["RUT_KEY"] = payments["RUT-P"].map(normalize_legacy_key)
    payments["FOLIO_KEY"] = payments["INVOICE-P"].map(normalize_legacy_key)
    payments["FOLIO_BASE"] = payments["INVOICE-P"].map(base_invoice_key)
    for column in ["EXCENT-CLP", "NET-CLP", "VAT-CLP", "GROSS-CLP", "PAID-CLP"]:
        if column in payments.columns:
            payments[column] = _to_numeric(payments[column]).fillna(0)
    return payments


def load_payments() -> pd.DataFrame:
    database_mtime_ns = DATABASE_PATH.stat().st_mtime_ns if DATABASE_PATH.exists() else 0
    return _load_payments_cached(database_mtime_ns).copy()


@st.cache_data(show_spinner=False)
def _load_cost_control_cached(database_mtime_ns: int) -> pd.DataFrame:
    return build_cost_control()


def load_cost_control() -> pd.DataFrame:
    database_mtime_ns = DATABASE_PATH.stat().st_mtime_ns if DATABASE_PATH.exists() else 0
    return _load_cost_control_cached(database_mtime_ns).copy()


def filter_cost_control(
    frame: pd.DataFrame,
    start_date=None,
    end_date=None,
    categories: list[int] | None = None,
    subcategories: list[int] | None = None,
    suppliers: list[str] | None = None,
    supplier_keys: list[str] | None = None,
    statuses: list[str] | None = None,
) -> pd.DataFrame:
    filtered = frame.copy()
    if start_date is not None:
        filtered = filtered[filtered["DATE-F"] >= pd.Timestamp(start_date)]
    if end_date is not None:
        filtered = filtered[filtered["DATE-F"] <= pd.Timestamp(end_date)]
    if categories:
        filtered = filtered[filtered["CATEGORY-F"].isin(categories)]
    if subcategories:
        filtered = filtered[filtered["SUBCATEGORY-F"].isin(subcategories)]
    if supplier_keys:
        filtered = filtered[
            supplier_key_series(filtered).isin(supplier_keys)
        ]
    elif suppliers:
        # Backward-compatible fallback for callers that have not migrated yet.
        filtered = filtered[filtered["SUPPLIER-F"].isin(suppliers)]
    if statuses:
        filtered = filtered[filtered["PAYMENT_STATUS"].isin(statuses)]
    return filtered


def summarize_cost_centers(frame: pd.DataFrame) -> pd.DataFrame:
    paid_mask = frame["PAID"].fillna(False)
    unpaid_mask = frame["PAYMENT_STATUS"] == "No pagado"
    review_mask = frame["PAYMENT_STATUS"] == "Revisar cruce"
    grouped = frame.groupby(
        ["CATEGORY-F", "CATEGORY_NAME", "SUBCATEGORY-F", "SUBCATEGORY_NAME"],
        dropna=False,
    )
    summary = grouped.agg(
        DOCUMENTOS=("INVOICE-F", "count"),
        COSTO_NETO_CLP=("NET-CLP-F", "sum"),
        COSTO_TOTAL_CLP=("TOTAL-CLP-F", "sum"),
        COSTO_NETO_UF=("NET-UF-F", "sum"),
        COSTO_TOTAL_UF=("TOTAL-UF-F", "sum"),
        PAGADO_CLP=("NET-CLP-F", lambda values: values[paid_mask.loc[values.index]].sum()),
        NO_PAGADO_CLP=("NET-CLP-F", lambda values: values[unpaid_mask.loc[values.index]].sum()),
        REVISAR_CLP=("NET-CLP-F", lambda values: values[review_mask.loc[values.index]].sum()),
        PAGADO_UF=("NET-UF-F", lambda values: values[paid_mask.loc[values.index]].sum()),
        NO_PAGADO_UF=("NET-UF-F", lambda values: values[unpaid_mask.loc[values.index]].sum()),
        REVISAR_UF=("NET-UF-F", lambda values: values[review_mask.loc[values.index]].sum()),
    ).reset_index()
    return summary.sort_values(["CATEGORY-F", "SUBCATEGORY-F"], na_position="last")


@st.cache_data(show_spinner=False)
def load_bidding() -> pd.DataFrame:
    bidding = _read_csv(BIDDING_PATH, sep=";", decimal=",")
    bidding.columns = ["CATEGORY-BID", "COST-BID", "DATE-BID"]
    bidding["CATEGORY-BID"] = _to_numeric(bidding["CATEGORY-BID"]).astype("Int64")
    bidding["COST-BID"] = _to_numeric(bidding["COST-BID"]).fillna(0)
    bidding["DATE-BID"] = pd.to_datetime(bidding["DATE-BID"], format="mixed", dayfirst=True, errors="coerce")
    return bidding


def bidding_comparison() -> pd.DataFrame:
    actual = load_cost_control()
    actual = actual[actual["PAID"] & actual["DATE-F"].notna()].copy()
    actual.loc[(actual["CATEGORY-F"] == 100) & (actual["SUBCATEGORY-F"] == 103), "CATEGORY-F"] = 103
    actual["MONTH"] = actual["DATE-F"].dt.to_period("M").astype(str)
    actual_grouped = actual.groupby(["CATEGORY-F", "MONTH"], dropna=False)["NET-UF-F"].sum().reset_index()
    actual_grouped.rename(columns={"CATEGORY-F": "CATEGORY", "NET-UF-F": "COST-REAL"}, inplace=True)

    bidding = load_bidding().copy()
    max_date = actual["DATE-F"].max()
    if pd.notna(max_date):
        bidding = bidding[bidding["DATE-BID"] <= max_date]
    bidding["MONTH"] = bidding["DATE-BID"].dt.to_period("M").astype(str)
    bidding_grouped = bidding.groupby(["CATEGORY-BID", "MONTH"])["COST-BID"].sum().reset_index()
    bidding_grouped.rename(columns={"CATEGORY-BID": "CATEGORY"}, inplace=True)

    comparison = bidding_grouped.merge(actual_grouped, on=["CATEGORY", "MONTH"], how="outer").fillna(0)
    comparison["CATEGORY"] = comparison["CATEGORY"].astype(int)
    comparison["CATEGORY_NAME"] = comparison["CATEGORY"].map(
        lambda value: "ITS" if value == 103 else category_label(value)
    )
    comparison["VARIANCE"] = comparison["COST-BID"] - comparison["COST-REAL"]
    return comparison.sort_values(["MONTH", "CATEGORY"])


@st.cache_data(show_spinner=False)
def load_revenues() -> pd.DataFrame:
    revenues = _read_csv(REVENUES_PATH, sep=";", decimal=",")
    revenues = revenues.iloc[:, :3]
    revenues.columns = ["DATE-R", "RECEIVED-R", "ACCRUED-R"]
    revenues["DATE-R"] = pd.to_datetime(revenues["DATE-R"], format="mixed", dayfirst=True, errors="coerce")
    revenues["MONTH"] = revenues["DATE-R"].dt.to_period("M").astype(str)
    revenues["RECEIVED-R"] = _to_numeric(revenues["RECEIVED-R"]).fillna(0)
    revenues["ACCRUED-R"] = _to_numeric(revenues["ACCRUED-R"]).fillna(0)
    return revenues


@st.cache_data(show_spinner=False)
def load_active_contracts() -> pd.DataFrame:
    contracts = _read_csv(ACTIVE_CONTRACTS_PATH, sep=";")
    contracts.columns = contracts.columns.str.strip()
    contracts["RUT_KEY"] = contracts["RUT-F"].map(normalize_legacy_key)
    contracts["CATEGORY_NAME"] = contracts["CATEGORY-F"].map(category_label)
    contracts["SUBCATEGORY_NAME"] = contracts.apply(
        lambda row: subcategory_label(row["CATEGORY-F"], row["SUBCATEGORY-F"]), axis=1
    )
    for column in ["CON", "AD1", "AD2", "AD3", "AD4", "AD5"]:
        contracts[column] = _to_numeric(contracts[column])
    return contracts


@st.cache_data(show_spinner=False)
def load_contract_invoices() -> pd.DataFrame:
    invoices = _read_csv(CONTRACT_INVOICES_PATH, sep=";")
    invoices.columns = invoices.columns.str.strip()
    invoices = invoices.loc[:, ~invoices.columns.str.startswith("Unnamed")]
    invoices["RUT_KEY"] = invoices["RUT-F"].map(normalize_legacy_key)
    invoices["FOLIO_KEY"] = invoices["INVOICE-F"].map(normalize_legacy_key)
    return invoices


def source_inventory() -> pd.DataFrame:
    metrics = query_dataframe(
        """
        SELECT 'RCV SII' AS Fuente, 'cc_lab.sqlite' AS Archivo,
               COUNT(*) AS Registros FROM documents
        UNION ALL
        SELECT 'Pagos H-P', 'cc_lab.sqlite', COUNT(*)
        FROM payments p
        JOIN payment_imports pi ON pi.payment_import_id = p.payment_import_id
        WHERE pi.is_active = 1
        UNION ALL
        SELECT 'Construcción', 'cc_lab.sqlite', COUNT(*)
        FROM construction_cost_items
        UNION ALL
        SELECT 'UF diaria', 'cc_lab.sqlite', COUNT(*)
        FROM uf_daily
        """
    )
    metrics["Disponible"] = DATABASE_PATH.exists()
    metrics["Tamaño MB"] = round(DATABASE_PATH.stat().st_size / 1_048_576, 2)
    rows = metrics.to_dict("records")
    for name, path in LEGACY_SOURCE_PATHS.items():
        rows.append(
            {
                "Fuente": f"{name} (referencia)",
                "Archivo": path.name,
                "Disponible": path.exists(),
                "Registros": None,
                "Tamaño MB": round(path.stat().st_size / 1_048_576, 2) if path.exists() else 0,
            }
        )
    return pd.DataFrame(rows)
