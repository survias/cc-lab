from __future__ import annotations

import pandas as pd
import plotly.express as px
import streamlit as st

from utils.catalogs import COST_CATEGORIES, PAYMENT_STATUSES
from utils.i18n import all_label, current_currency, payment_status_label, text
from utils.legacy_data import filter_cost_control, load_cost_control, summarize_cost_centers
from utils.payment_data import get_active_payment_summary
from utils.ui_helpers import (
    PLOTLY_CONFIG,
    STATUS_COLORS,
    dataframe_to_csv_bytes,
    format_clp_compact,
    format_uf,
    page_heading,
    show_historical_data_error,
    style_chart,
)


def payment_view(frame: pd.DataFrame) -> pd.DataFrame:
    columns = [
        "RUT_COMPLETO",
        "SUPPLIER-F",
        "DOCUMENT TYPE",
        "INVOICE-F",
        "DATE-F",
        "LAST_PAYMENT_DATE",
        "NET-UF-F",
        "TOTAL-UF-F",
    ]
    display = frame[[column for column in columns if column in frame.columns]].copy()
    for column in ("DATE-F", "LAST_PAYMENT_DATE"):
        if column in display:
            display[column] = (
                pd.to_datetime(display[column], errors="coerce")
                .dt.strftime("%d-%m-%Y")
                .fillna("")
            )
    return display.rename(
        columns={
            "RUT_COMPLETO": "RUT",
            "SUPPLIER-F": text("Proveedor", "Supplier", "供应商"),
            "DOCUMENT TYPE": text("Tipo", "Type", "类型"),
            "INVOICE-F": text("Folio", "Number", "编号"),
            "DATE-F": text("Fecha", "Date", "日期"),
            "LAST_PAYMENT_DATE": text("Fecha pago", "Payment date", "付款日期"),
            "NET-UF-F": text("Neto UF", "Net UF", "净额 UF"),
            "TOTAL-UF-F": text("Total UF", "Total UF", "总额 UF"),
        }
    )


def render_documents(frame: pd.DataFrame) -> None:
    if frame.empty:
        st.info(text("Sin resultados", "No results", "无结果"))
    else:
        supplier_column = text("Proveedor", "Supplier", "供应商")
        type_column = text("Tipo", "Type", "类型")
        folio_column = text("Folio", "Number", "编号")
        date_column = text("Fecha", "Date", "日期")
        payment_date_column = text("Fecha pago", "Payment date", "付款日期")
        net_column = text("Neto UF", "Net UF", "净额 UF")
        total_column_label = text("Total UF", "Total UF", "总额 UF")
        st.dataframe(
            payment_view(frame),
            hide_index=True,
            width="stretch",
            height=510,
            column_config={
                "RUT": st.column_config.TextColumn("RUT", width=95),
                supplier_column: st.column_config.TextColumn(supplier_column, width=310),
                type_column: st.column_config.NumberColumn(type_column, format="%d", width=65),
                folio_column: st.column_config.TextColumn(folio_column, width=85),
                date_column: st.column_config.TextColumn(date_column, width=100),
                payment_date_column: st.column_config.TextColumn(payment_date_column, width=105),
                net_column: st.column_config.NumberColumn(net_column, format="%.2f UF", width=105),
                total_column_label: st.column_config.NumberColumn(total_column_label, format="%.2f UF", width=105),
            },
        )


page_heading(text("Pagos", "Payments", "付款"))
currency = current_currency()
total_column = "NET-UF-F" if currency == "UF" else "NET-CLP-F"
format_amount = format_uf if currency == "UF" else format_clp_compact

payment_summary = get_active_payment_summary()
if payment_summary:
    st.caption(
        text(
            f"SQLite · {int(payment_summary['payment_count']):,} pagos · "
            f"hasta {payment_summary['last_payment_date']}",
            f"SQLite · {int(payment_summary['payment_count']):,} payments · "
            f"through {payment_summary['last_payment_date']}",
            f"SQLite · {int(payment_summary['payment_count']):,} 笔付款 · "
            f"截至 {payment_summary['last_payment_date']}",
        ).replace(",", ".")
    )

try:
    cost_control = load_cost_control()
except Exception as exc:
    show_historical_data_error(exc)
    st.stop()

min_date = cost_control["DATE-F"].min().date()
max_date = cost_control["DATE-F"].max().date()

with st.expander(text("Filtros", "Filters", "筛选"), expanded=True):
    first_filter_row = st.columns([1, 1, 1.1, 1.3])
    selected_categories = first_filter_row[0].multiselect(
        text("Grupos", "Groups", "组别"),
        sorted(cost_control["CATEGORY-F"].dropna().astype(int).unique()),
        format_func=lambda code: f"{code} · {COST_CATEGORIES.get(code, '')}",
        placeholder=all_label(),
    )
    subcategory_source = cost_control
    if selected_categories:
        subcategory_source = subcategory_source[subcategory_source["CATEGORY-F"].isin(selected_categories)]
    subcategory_lookup = (
        subcategory_source[["SUBCATEGORY-F", "SUBCATEGORY_NAME"]]
        .dropna()
        .drop_duplicates()
        .sort_values("SUBCATEGORY-F")
    )
    subcategory_names = dict(
        zip(subcategory_lookup["SUBCATEGORY-F"].astype(int), subcategory_lookup["SUBCATEGORY_NAME"])
    )
    selected_subcategories = first_filter_row[1].multiselect(
        text("Centros", "Cost centers", "成本中心"),
        list(subcategory_names),
        format_func=lambda code: f"{code} · {subcategory_names[code]}",
        placeholder=all_label(),
    )
    supplier_source = subcategory_source
    if selected_subcategories:
        supplier_source = supplier_source[supplier_source["SUBCATEGORY-F"].isin(selected_subcategories)]
    selected_suppliers = first_filter_row[2].multiselect(
        text("Proveedores", "Suppliers", "供应商"),
        sorted(supplier_source["SUPPLIER-F"].dropna().unique()),
        placeholder=all_label(),
    )
    with first_filter_row[3]:
        apply_date_filter = st.toggle(text("Fecha", "Date", "日期"))
        selected_range = st.date_input(
            text("Período", "Period", "期间"),
            value=(min_date, max_date),
            min_value=min_date,
            max_value=max_date,
            format="DD/MM/YYYY",
            disabled=not apply_date_filter,
            label_visibility="collapsed",
        )
    selected_statuses = st.multiselect(
        text("Estados", "Statuses", "状态"),
        PAYMENT_STATUSES,
        default=PAYMENT_STATUSES,
        format_func=payment_status_label,
    )

start_date, end_date = (
    selected_range if apply_date_filter and len(selected_range) == 2 else (None, None)
)
filtered = filter_cost_control(
    cost_control,
    start_date=start_date,
    end_date=end_date,
    categories=selected_categories,
    subcategories=selected_subcategories,
    suppliers=selected_suppliers,
    statuses=selected_statuses,
)

paid_total = filtered[filtered["PAID"]]
paid = filtered[
    (filtered["RECORD_TYPE"] == "DOCUMENT")
    & (filtered["DOCUMENT TYPE"] != 61)
    & (filtered["PAYMENT_STATUS"] == "Pagado")
]
unpaid = filtered[filtered["PAYMENT_STATUS"] == "No pagado"]
review = filtered[filtered["PAYMENT_STATUS"] == "Revisar cruce"]
payment_only = filtered[filtered["PAYMENT_STATUS"] == "Pago sin documento"]
credit_notes = filtered[
    filtered["PAYMENT_STATUS"].isin(["Nota de crédito", "Anulada por NC"])
]
pending = filtered[filtered["REVIEW_REASON"] != ""]

metrics = st.columns(4)
metrics[0].metric(text("Pagado total", "Total paid", "已支付总额"), format_amount(paid_total[total_column].sum()))
metrics[1].metric(text("No pagado", "Unpaid", "未支付"), format_amount(unpaid[total_column].sum()))
metrics[2].metric(text("Pagos sin documento", "Payments without document", "无凭证付款"), format_amount(payment_only[total_column].sum()))
metrics[3].metric(text("Pendientes", "Pending", "待处理"), f"{len(pending):,}".replace(",", "."))

summary_tab, paid_tab, unpaid_tab, payment_only_tab, review_tab, credit_tab, provider_tab = st.tabs(
    [
        text("Resumen", "Overview", "概览"),
        text("Pagados", "Paid", "已支付"),
        text("No pagados", "Unpaid", "未支付"),
        text("Sin documento", "Without document", "无凭证"),
        text("Revisar", "Review", "复核"),
        text("Notas de crédito", "Credit notes", "贷项通知单"),
        text("Proveedor", "Supplier", "供应商"),
    ]
)

with summary_tab:
    summary_columns = st.columns([0.72, 2])
    status_summary = (
        filtered.groupby("PAYMENT_STATUS", dropna=False)
        .size()
        .rename("DOCUMENTOS")
        .reset_index()
    )
    status_summary["LABEL"] = status_summary["PAYMENT_STATUS"].map(payment_status_label)
    status_chart = px.pie(
        status_summary,
        names="LABEL",
        values="DOCUMENTOS",
        title=text("Documentos por estado", "Documents by status", "按状态分类的文档"),
        hole=0.62,
        color="PAYMENT_STATUS",
        color_discrete_map=STATUS_COLORS,
    )
    status_chart.update_traces(textinfo="percent", hovertemplate="%{label}<br>%{value:,.0f}<extra></extra>")
    summary_columns[0].plotly_chart(
        style_chart(status_chart, height=370, horizontal_legend=False),
        width="stretch",
        config=PLOTLY_CONFIG,
    )
    suffix = "UF" if currency == "UF" else "CLP"
    number_format = "%.0f UF" if currency == "UF" else "$ %.0f"
    summary = summarize_cost_centers(filtered)[
        ["CATEGORY-F", "SUBCATEGORY-F", "SUBCATEGORY_NAME", "DOCUMENTOS", f"COSTO_NETO_{suffix}", f"PAGADO_{suffix}", f"NO_PAGADO_{suffix}", f"REVISAR_{suffix}"]
    ]
    summary_columns[1].dataframe(
        summary,
        hide_index=True,
        width="stretch",
        height=370,
        column_config={
            "CATEGORY-F": text("Grupo", "Group", "组别"),
            "SUBCATEGORY-F": text("Centro", "Center", "中心"),
            "SUBCATEGORY_NAME": text("Centro de costo", "Cost center", "成本中心"),
            "DOCUMENTOS": text("Docs.", "Docs", "文档"),
            f"COSTO_NETO_{suffix}": st.column_config.NumberColumn(text("Neto", "Net", "净额"), format=number_format),
            f"PAGADO_{suffix}": st.column_config.NumberColumn(text("Pagado", "Paid", "已支付"), format=number_format),
            f"NO_PAGADO_{suffix}": st.column_config.NumberColumn(text("No pagado", "Unpaid", "未支付"), format=number_format),
            f"REVISAR_{suffix}": st.column_config.NumberColumn(text("Revisar", "Review", "复核"), format=number_format),
        },
    )

with paid_tab:
    render_documents(paid)

with unpaid_tab:
    render_documents(unpaid)

with payment_only_tab:
    render_documents(payment_only)

with review_tab:
    render_documents(pending)

with credit_tab:
    render_documents(credit_notes)

with provider_tab:
    provider_options = sorted(filtered["SUPPLIER-F"].dropna().unique())
    if not provider_options:
        st.info(text("Sin resultados", "No results", "无结果"))
    else:
        provider = st.selectbox(text("Proveedor", "Supplier", "供应商"), provider_options)
        provider_data = filtered[filtered["SUPPLIER-F"] == provider]
        provider_metrics = st.columns(4)
        provider_metrics[0].metric(text("Documentos", "Documents", "文档"), len(provider_data))
        provider_metrics[1].metric(
            text("Pagado", "Paid", "已支付"),
            format_amount(provider_data.loc[provider_data["PAID"], total_column].sum()),
        )
        provider_metrics[2].metric(
            text("No pagado", "Unpaid", "未支付"),
            format_amount(provider_data.loc[provider_data["PAYMENT_STATUS"] == "No pagado", total_column].sum()),
        )
        provider_metrics[3].metric(
            text("Revisar", "Review", "复核"),
            len(provider_data[provider_data["PAYMENT_STATUS"] == "Revisar cruce"]),
        )
        render_documents(provider_data)

st.download_button(
    text("Descargar", "Download", "下载"),
    data=dataframe_to_csv_bytes(payment_view(filtered)),
    file_name="cc_lab_pagos_y_saldos.csv",
    mime="text/csv",
    icon=":material/download:",
)
