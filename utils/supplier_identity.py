from __future__ import annotations

import re
import unicodedata

import pandas as pd


def normalize_supplier_name(value: object) -> str:
    """Return a stable fallback key only for records without a RUT."""
    normalized = unicodedata.normalize("NFKD", str(value or "").upper())
    normalized = "".join(char for char in normalized if not unicodedata.combining(char))
    return " ".join(re.sub(r"[^A-Z0-9]+", " ", normalized).split())


def supplier_key_series(
    frame: pd.DataFrame,
    *,
    rut_key_column: str = "RUT_KEY",
    name_column: str = "SUPPLIER-F",
) -> pd.Series:
    """Use the normalized RUT as identity and a name key only when RUT is absent."""
    rut = frame[rut_key_column].astype("string").fillna("").str.strip()
    names = frame[name_column].astype("string").fillna("").str.strip()
    fallback = names.map(normalize_supplier_name)
    return rut.where(rut.ne(""), "NAME:" + fallback)


def supplier_filter_options(
    frame: pd.DataFrame,
    *,
    rut_key_column: str = "RUT_KEY",
    rut_display_column: str = "RUT_COMPLETO",
    name_column: str = "SUPPLIER-F",
    label_func=None,
) -> pd.DataFrame:
    """Build unique selector values while keeping the RUT as the actual value."""
    source = frame[[rut_key_column, rut_display_column, name_column]].copy()
    source["KEY"] = supplier_key_series(
        source,
        rut_key_column=rut_key_column,
        name_column=name_column,
    )
    source["NAME"] = source[name_column].astype("string").fillna("").str.strip()
    source["RUT"] = source[rut_display_column].astype("string").fillna("").str.strip()
    source["LABEL"] = source["NAME"]
    if label_func is not None:
        source["LABEL"] = source["NAME"].map(label_func)
    source["LABEL"] = source["LABEL"].where(source["LABEL"].ne(""), source["NAME"])
    source["LABEL"] = source["LABEL"] + source["RUT"].map(
        lambda value: f" · {value}" if value else ""
    )
    return (
        source[["KEY", "LABEL", "NAME", "RUT"]]
        .drop_duplicates("KEY")
        .sort_values(["LABEL", "KEY"], kind="stable")
        .reset_index(drop=True)
    )
