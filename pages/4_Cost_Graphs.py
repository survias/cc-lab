from __future__ import annotations

import pandas as pd
import plotly.express as px
import streamlit as st

from utils.catalogs import COST_CATEGORIES, PAYMENT_STATUSES
from utils.i18n import all_label, current_currency, payment_status_label, text
from utils.legacy_data import filter_cost_control, load_cost_control
from utils.ui_helpers import (
    PLOTLY_CONFIG,
    STATUS_COLORS,
    filter_heading,
    format_clp_compact,
    format_uf,
    page_heading,
    render_kpis,
    result_summary,
    show_historical_data_error,
    style_chart,
)


page_heading(text("Análisis de costos", "Cost analysis", "成本分析"))
currency = current_currency()
net_column = "NET-UF-F" if currency == "UF" else "NET-CLP-F"
total_column = "TOTAL-UF-F" if currency == "UF" else "TOTAL-CLP-F"
format_amount = format_uf if currency == "UF" else format_clp_compact

try:
    cost_control = load_cost_control()
except Exception as exc:
    show_historical_data_error(exc)
    st.stop()

min_date = cost_control["DATE-F"].min().date()
max_date = cost_control["DATE-F"].max().date()
kpi_area = st.container()
filter_heading(text("Filtros del análisis", "Analysis filters", "分析筛选"))
with st.container():
    filters = st.columns([1.4, 1.4, 1])
    selected_categories = filters[0].multiselect(
        text("Grupos", "Groups", "组别"),
        sorted(cost_control["CATEGORY-F"].dropna().astype(int).unique()),
        format_func=lambda code: f"{code} · {COST_CATEGORIES.get(code, '')}",
        placeholder=all_label(),
    )
    selected_statuses = filters[1].multiselect(
        text("Estados", "Statuses", "状态"),
        PAYMENT_STATUSES,
        default=PAYMENT_STATUSES,
        format_func=payment_status_label,
    )
    selected_range = filters[2].date_input(
        text("Período", "Period", "期间"),
        value=(min_date, max_date),
        min_value=min_date,
        max_value=max_date,
        format="DD/MM/YYYY",
    )

start_date, end_date = selected_range if len(selected_range) == 2 else (min_date, max_date)
filtered = filter_cost_control(
    cost_control,
    start_date=start_date,
    end_date=end_date,
    categories=selected_categories,
    statuses=selected_statuses,
)
pending_count = int((filtered["REVIEW_REASON"] != "").sum())
filtered = filtered[filtered["INCLUDED_IN_COST"] & filtered["DATE-F"].notna()].copy()

if filtered.empty:
    st.info(text("Sin resultados", "No results", "无结果"))
    st.stop()

filtered["YEAR"] = filtered["DATE-F"].dt.year
filtered["MONTH"] = filtered["DATE-F"].dt.to_period("M").dt.to_timestamp()

with kpi_area:
    render_kpis(
        [
            (text(f"Costo {currency}", f"Cost {currency}", f"成本 {currency}"), format_amount(filtered[net_column].sum())),
            (text("Documentos", "Documents", "文档"), f"{len(filtered):,}".replace(",", ".")),
            (text("Centros", "Cost centers", "成本中心"), f"{filtered['SUBCATEGORY-F'].nunique():,.0f}".replace(",", ".")),
            (text("Pendientes", "Pending", "待处理"), f"{pending_count:,}".replace(",", ".")),
        ]
    )

result_summary(
    text(
        f"{filtered['DATE-F'].min():%d/%m/%Y} a {filtered['DATE-F'].max():%d/%m/%Y} · {filtered['RUT_COMPLETO'].nunique()} proveedores",
        f"{filtered['DATE-F'].min():%d/%m/%Y} to {filtered['DATE-F'].max():%d/%m/%Y} · {filtered['RUT_COMPLETO'].nunique()} suppliers",
        f"{filtered['DATE-F'].min():%Y/%m/%d} 至 {filtered['DATE-F'].max():%Y/%m/%d} · {filtered['RUT_COMPLETO'].nunique()} 家供应商",
    )
)

cost_tab, status_tab, table_tab = st.tabs(
    [
        text("Evolución", "Trend", "趋势"),
        text("Estado de pago", "Payment status", "付款状态"),
        text("Tabla", "Table", "表格"),
    ]
)

with cost_tab:
    yearly = (
        filtered.groupby(["YEAR", "CATEGORY_NAME"], dropna=False)[[net_column, total_column]]
        .sum()
        .reset_index()
    )
    columns = st.columns(2)
    net_chart = px.bar(
        yearly,
        x="YEAR",
        y=net_column,
        color="CATEGORY_NAME",
        title=text("Costo neto por año", "Net cost by year", "年度净成本"),
        labels={"YEAR": text("Año", "Year", "年份"), net_column: currency, "CATEGORY_NAME": text("Grupo", "Group", "组别")},
    )
    total_chart = px.bar(
        yearly,
        x="YEAR",
        y=total_column,
        color="CATEGORY_NAME",
        title=text("Costo total por año", "Total cost by year", "年度总成本"),
        labels={"YEAR": text("Año", "Year", "年份"), total_column: currency, "CATEGORY_NAME": text("Grupo", "Group", "组别")},
    )
    columns[0].plotly_chart(style_chart(net_chart), width="stretch", config=PLOTLY_CONFIG)
    columns[1].plotly_chart(style_chart(total_chart), width="stretch", config=PLOTLY_CONFIG)

with status_tab:
    monthly_status = (
        filtered.groupby(["MONTH", "PAYMENT_STATUS"], dropna=False)[total_column]
        .sum()
        .reset_index()
    )
    monthly_status["STATUS_LABEL"] = monthly_status["PAYMENT_STATUS"].map(payment_status_label)
    status_chart = px.line(
        monthly_status,
        x="MONTH",
        y=total_column,
        color="STATUS_LABEL",
        markers=False,
        title=text("Costo mensual por estado", "Monthly cost by status", "按状态分类的月度成本"),
        labels={"MONTH": text("Mes", "Month", "月份"), total_column: currency, "STATUS_LABEL": text("Estado", "Status", "状态")},
        color_discrete_map={payment_status_label(key): value for key, value in STATUS_COLORS.items()},
    )
    st.plotly_chart(style_chart(status_chart, height=440), width="stretch", config=PLOTLY_CONFIG)

with table_tab:
    summary = (
        filtered.groupby(
            ["YEAR", "CATEGORY-F", "CATEGORY_NAME", "SUBCATEGORY-F", "SUBCATEGORY_NAME"],
            dropna=False,
        )
        .agg(
            DOCUMENTOS=("INVOICE-F", "count"),
            NETO=(net_column, "sum"),
            TOTAL=(total_column, "sum"),
        )
        .reset_index()
        .sort_values(["YEAR", "CATEGORY-F", "SUBCATEGORY-F"])
    )
    st.dataframe(
        summary,
        hide_index=True,
        width="stretch",
        height=560,
        column_config={
            "YEAR": text("Año", "Year", "年份"),
            "CATEGORY-F": text("Grupo", "Group", "组别"),
            "CATEGORY_NAME": text("Nombre", "Name", "名称"),
            "SUBCATEGORY-F": text("Centro", "Center", "中心"),
            "SUBCATEGORY_NAME": text("Centro de costo", "Cost center", "成本中心"),
            "DOCUMENTOS": text("Docs.", "Docs", "文档"),
            "NETO": st.column_config.NumberColumn(text(f"Neto {currency}", f"Net {currency}", f"净额 {currency}"), format="%.0f UF" if currency == "UF" else "$ %.0f"),
            "TOTAL": st.column_config.NumberColumn(text(f"Total {currency}", f"Total {currency}", f"总额 {currency}"), format="%.0f UF" if currency == "UF" else "$ %.0f"),
        },
    )
