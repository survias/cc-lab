from __future__ import annotations

import pandas as pd
import plotly.express as px
import streamlit as st

from utils.i18n import all_label, current_currency, text
from utils.legacy_data import load_cost_control, summarize_cost_centers
from utils.ui_helpers import (
    CHART_COLORS,
    PLOTLY_CONFIG,
    format_clp_compact,
    format_uf,
    page_heading,
    show_historical_data_error,
    style_chart,
)


page_heading("C&C Lab")

try:
    cost_control = load_cost_control()
except Exception as exc:
    show_historical_data_error(exc)
    st.stop()

latest_date = cost_control["DATE-F"].max()
years = sorted(cost_control["DATE-F"].dropna().dt.year.unique(), reverse=True)
toolbar = st.columns([1, 4])
selected_year = toolbar[0].selectbox(
    text("Período", "Period", "期间"),
    [None, *years],
    format_func=lambda value: all_label() if value is None else str(value),
)
currency = current_currency()

filtered = cost_control
if selected_year is not None:
    filtered = filtered[filtered["DATE-F"].dt.year == selected_year]

pending = filtered[filtered["REVIEW_REASON"] != ""]
confirmed = filtered[filtered["INCLUDED_IN_COST"]].copy()

paid = confirmed[confirmed["PAID"]]
unpaid = confirmed[confirmed["PAYMENT_STATUS"] == "No pagado"]

amount_column = "NET-UF-F" if currency == "UF" else "NET-CLP-F"
format_amount = format_uf if currency == "UF" else format_clp_compact

st.caption(
    text(
        f"SQLite · actualizado al {latest_date:%d/%m/%Y} · {len(filtered):,.0f} registros",
        f"SQLite · updated {latest_date:%d/%m/%Y} · {len(filtered):,.0f} records",
        f"SQLite · 更新至 {latest_date:%Y/%m/%d} · {len(filtered):,.0f} 条记录",
    ).replace(",", ".")
)

metrics = st.columns(4)
metrics[0].metric(text("Costo total", "Total cost", "总成本"), format_amount(confirmed[amount_column].sum()))
metrics[1].metric(
    text("Costo pagado", "Paid cost", "已付成本"),
    format_amount(paid[amount_column].sum()),
)
metrics[2].metric(
    text("Costo no pagado", "Unpaid cost", "未付成本"),
    format_amount(unpaid[amount_column].sum()),
)
metrics[3].metric(
    text("Pendientes", "Pending review", "待复核"),
    f"{len(pending):,.0f}".replace(",", "."),
)

chart_columns = st.columns([1.55, 1])
monthly = (
    confirmed.dropna(subset=["DATE-F"])
    .assign(MONTH=lambda frame: frame["DATE-F"].dt.to_period("M").dt.to_timestamp())
    .groupby("MONTH", as_index=False)[amount_column]
    .sum()
)
trend = px.area(
    monthly,
    x="MONTH",
    y=amount_column,
    title=text("Evolución mensual", "Monthly trend", "月度趋势"),
    labels={"MONTH": text("Mes", "Month", "月份"), amount_column: currency},
    color_discrete_sequence=[CHART_COLORS[0]],
)
trend.update_traces(line={"width": 2}, fillcolor="rgba(0,108,91,0.12)")
chart_columns[0].plotly_chart(style_chart(trend, height=330), width="stretch", config=PLOTLY_CONFIG)

centers = (
    confirmed.groupby(["SUBCATEGORY-F", "SUBCATEGORY_NAME"], dropna=False)[amount_column]
    .sum()
    .reset_index()
    .nlargest(10, amount_column)
    .sort_values(amount_column)
)
centers["CENTER"] = centers["SUBCATEGORY-F"].fillna(0).astype(int).astype(str) + " · " + centers[
    "SUBCATEGORY_NAME"
].fillna(text("Sin nombre", "Unnamed", "未命名"))
center_chart = px.bar(
    centers,
    x=amount_column,
    y="CENTER",
    orientation="h",
    title=text("Principales centros", "Top cost centers", "主要成本中心"),
    labels={amount_column: currency, "CENTER": ""},
    color_discrete_sequence=[CHART_COLORS[1]],
)
chart_columns[1].plotly_chart(
    style_chart(center_chart, height=330, horizontal_legend=False),
    width="stretch",
    config=PLOTLY_CONFIG,
)

center_tab, provider_tab, pending_tab = st.tabs(
    [
        text("Centros", "Cost centers", "成本中心"),
        text("Proveedores", "Suppliers", "供应商"),
        text("Pendientes", "Outstanding", "待处理"),
    ]
)

with center_tab:
    suffix = "UF" if currency == "UF" else "CLP"
    net_column = f"COSTO_NETO_{suffix}"
    center_summary = summarize_cost_centers(confirmed).sort_values(net_column, ascending=False)
    center_summary = center_summary[
        [
            "CATEGORY-F",
            "CATEGORY_NAME",
            "SUBCATEGORY-F",
            "SUBCATEGORY_NAME",
            "DOCUMENTOS",
            net_column,
            f"PAGADO_{suffix}",
            f"NO_PAGADO_{suffix}",
            f"REVISAR_{suffix}",
        ]
    ]
    number_format = "%.0f UF" if currency == "UF" else "$ %.0f"
    st.dataframe(
        center_summary,
        hide_index=True,
        width="stretch",
        height=410,
        column_config={
            "CATEGORY-F": text("Grupo", "Group", "组别"),
            "CATEGORY_NAME": text("Nombre", "Name", "名称"),
            "SUBCATEGORY-F": text("Centro", "Center", "中心"),
            "SUBCATEGORY_NAME": text("Centro de costo", "Cost center", "成本中心"),
            "DOCUMENTOS": text("Docs.", "Docs", "文档"),
            net_column: st.column_config.NumberColumn(text(f"Neto {currency}", f"Net {currency}", f"净额 {currency}"), format=number_format),
            f"PAGADO_{suffix}": st.column_config.NumberColumn(text("Pagado", "Paid", "已支付"), format=number_format),
            f"NO_PAGADO_{suffix}": st.column_config.NumberColumn(text("No pagado", "Unpaid", "未支付"), format=number_format),
            f"REVISAR_{suffix}": st.column_config.NumberColumn(text("Revisar", "Review", "复核"), format=number_format),
        },
    )

with provider_tab:
    providers = (
        confirmed.groupby(["RUT_COMPLETO", "SUPPLIER-F"], dropna=False)
        .agg(DOCUMENTOS=("INVOICE-F", "count"), NETO_CLP=("NET-CLP-F", "sum"), NETO_UF=("NET-UF-F", "sum"))
        .reset_index()
        .sort_values(f"NETO_{currency}", ascending=False)
    )
    providers = providers[["RUT_COMPLETO", "SUPPLIER-F", "DOCUMENTOS", f"NETO_{currency}"]]
    st.dataframe(
        providers,
        hide_index=True,
        width="stretch",
        height=410,
        column_config={
            "RUT_COMPLETO": "RUT",
            "SUPPLIER-F": text("Proveedor", "Supplier", "供应商"),
            "DOCUMENTOS": text("Docs.", "Docs", "文档"),
            f"NETO_{currency}": st.column_config.NumberColumn(
                text(f"Neto {currency}", f"Net {currency}", f"净额 {currency}"),
                format="%.0f UF" if currency == "UF" else "$ %.0f",
            ),
        },
    )

with pending_tab:
    pending_amount = "TOTAL-UF-F" if currency == "UF" else "TOTAL-CLP-F"
    pending_display = pending.sort_values(pending_amount, ascending=False)[
        ["REVIEW_REASON", "SOURCE_KIND", "SUPPLIER-F", "RUT_COMPLETO", "DOCUMENT TYPE", "INVOICE-F", "DATE-F", "SUBCATEGORY_NAME", pending_amount]
    ].rename(
        columns={
            "REVIEW_REASON": text("Motivo", "Reason", "原因"),
            "SOURCE_KIND": text("Origen", "Source", "来源"),
            "SUPPLIER-F": text("Proveedor", "Supplier", "供应商"),
            "RUT_COMPLETO": "RUT",
            "DOCUMENT TYPE": text("Tipo", "Type", "类型"),
            "INVOICE-F": text("Folio", "Number", "编号"),
            "DATE-F": text("Fecha", "Date", "日期"),
            "SUBCATEGORY_NAME": text("Centro", "Center", "中心"),
            pending_amount: text(f"Total {currency}", f"Total {currency}", f"总额 {currency}"),
        }
    )
    st.dataframe(pending_display, hide_index=True, width="stretch", height=410)
