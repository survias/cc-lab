from __future__ import annotations

import streamlit as st

from utils.catalogs import COST_CATEGORIES
from utils.i18n import all_label, current_currency, text
from utils.legacy_data import load_active_contracts, load_contract_invoices
from utils.ui_helpers import format_clp_compact, format_uf, page_heading, show_historical_data_error
from utils.uf_data import get_current_uf_rate


page_heading(text("Contratos", "Contracts", "合同"))
currency = current_currency()
conversion_factor = get_current_uf_rate() if currency == "CLP" else 1
format_amount = format_clp_compact if currency == "CLP" else format_uf
number_format = "$ %.0f" if currency == "CLP" else "%.2f UF"

try:
    contracts = load_active_contracts()
    contract_invoices = load_contract_invoices()
except Exception as exc:
    show_historical_data_error(exc)
    st.stop()

with st.expander(text("Filtros", "Filters", "筛选"), expanded=True):
    filters = st.columns(3)
    selected_categories = filters[0].multiselect(
        text("Grupos", "Groups", "组别"),
        sorted(contracts["CATEGORY-F"].dropna().astype(int).unique()),
        format_func=lambda code: f"{code} · {COST_CATEGORIES.get(code, '')}",
        placeholder=all_label(),
    )
    subcategory_source = contracts
    if selected_categories:
        subcategory_source = subcategory_source[subcategory_source["CATEGORY-F"].isin(selected_categories)]
    subcategory_options = (
        subcategory_source[["SUBCATEGORY-F", "SUBCATEGORY_NAME"]]
        .dropna()
        .drop_duplicates()
        .sort_values("SUBCATEGORY-F")
    )
    subcategory_names = dict(
        zip(subcategory_options["SUBCATEGORY-F"].astype(int), subcategory_options["SUBCATEGORY_NAME"])
    )
    selected_subcategories = filters[1].multiselect(
        text("Centros", "Cost centers", "成本中心"),
        list(subcategory_names),
        format_func=lambda code: f"{code} · {subcategory_names[code]}",
        placeholder=all_label(),
    )
    supplier_source = contracts
    if selected_categories:
        supplier_source = supplier_source[supplier_source["CATEGORY-F"].isin(selected_categories)]
    selected_supplier = filters[2].selectbox(
        text("Proveedor", "Supplier", "供应商"),
        [None, *sorted(supplier_source["SUPPLIER-F"].dropna().unique())],
        format_func=lambda value: all_label() if value is None else value,
    )

filtered = contracts.copy()
if selected_categories:
    filtered = filtered[filtered["CATEGORY-F"].isin(selected_categories)]
if selected_subcategories:
    filtered = filtered[filtered["SUBCATEGORY-F"].isin(selected_subcategories)]
if selected_supplier is not None:
    filtered = filtered[filtered["SUPPLIER-F"] == selected_supplier]

additional_columns = ["AD1", "AD2", "AD3", "AD4", "AD5"]
filtered = filtered.copy()
filtered[["CON", *additional_columns]] *= conversion_factor
metrics = st.columns(4)
metrics[0].metric(text("Contratos", "Contracts", "合同"), f"{len(filtered):,}".replace(",", "."))
metrics[1].metric(text("Proveedores", "Suppliers", "供应商"), filtered["SUPPLIER-F"].nunique())
metrics[2].metric(text("Monto base", "Base amount", "基础金额"), format_amount(filtered["CON"].sum()))
metrics[3].metric(text("Adicionales", "Additions", "附加金额"), format_amount(filtered[additional_columns].sum().sum()))

contracts_tab, invoices_tab = st.tabs(
    [
        text("Contratos", "Contracts", "合同"),
        text("Documentos", "Documents", "文档"),
    ]
)

with contracts_tab:
    display_columns = [
        "SUPPLIER-F",
        "RUT-F",
        "CONTRACT-F",
        "CATEGORY-F",
        "SUBCATEGORY-F",
        "SUBCATEGORY_NAME",
        "CON",
        *additional_columns,
        "STATUS",
    ]
    st.dataframe(
        filtered[[column for column in display_columns if column in filtered.columns]],
        hide_index=True,
        width="stretch",
        height=550,
        column_config={
            "SUPPLIER-F": text("Proveedor", "Supplier", "供应商"),
            "RUT-F": "RUT",
            "CONTRACT-F": text("Contrato", "Contract", "合同"),
            "CATEGORY-F": text("Grupo", "Group", "组别"),
            "SUBCATEGORY-F": text("Centro", "Center", "中心"),
            "SUBCATEGORY_NAME": text("Centro de costo", "Cost center", "成本中心"),
            "CON": st.column_config.NumberColumn(text(f"Base {currency}", f"Base {currency}", f"基础 {currency}"), format=number_format),
            "AD1": st.column_config.NumberColumn(text("Adicional 1", "Addition 1", "附加 1"), format=number_format),
            "AD2": st.column_config.NumberColumn(text("Adicional 2", "Addition 2", "附加 2"), format=number_format),
            "AD3": st.column_config.NumberColumn(text("Adicional 3", "Addition 3", "附加 3"), format=number_format),
            "AD4": st.column_config.NumberColumn(text("Adicional 4", "Addition 4", "附加 4"), format=number_format),
            "AD5": st.column_config.NumberColumn(text("Adicional 5", "Addition 5", "附加 5"), format=number_format),
            "STATUS": text("Estado", "Status", "状态"),
        },
    )

with invoices_tab:
    contract_ids = filtered["CONTRACT-F"].dropna().astype(str).unique()
    linked = contract_invoices[contract_invoices["CONTRACT"].astype(str).isin(contract_ids)].copy()
    invoice_metrics = st.columns(3)
    invoice_metrics[0].metric(text("Documentos", "Documents", "文档"), f"{len(linked):,}".replace(",", "."))
    invoice_metrics[1].metric(text("Contratos vinculados", "Linked contracts", "关联合同"), linked["CONTRACT"].nunique())
    invoice_metrics[2].metric(text("Proveedores", "Suppliers", "供应商"), linked["RUT_KEY"].nunique())
    if linked.empty:
        st.info(text("Sin resultados", "No results", "无结果"))
    else:
        st.dataframe(linked, hide_index=True, width="stretch", height=530)
