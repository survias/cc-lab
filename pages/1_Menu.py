from __future__ import annotations

import pandas as pd
import plotly.express as px
import streamlit as st

from utils.catalogs import COST_CATEGORIES
from utils.i18n import all_label, current_currency, payment_status_label, text
from utils.legacy_data import load_cost_control, summarize_cost_centers
from utils.ui_helpers import (
    CHART_COLORS,
    PLOTLY_CONFIG,
    STATUS_COLORS,
    executive_table_style,
    filter_heading,
    format_amount_cell,
    format_clp_compact,
    format_uf,
    page_heading,
    render_kpis,
    result_summary,
    short_business_name,
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
currency = current_currency()

kpi_area = st.container()
filter_area = st.container()
with filter_area:
    filter_heading(text("Filtros del resumen", "Overview filters", "概览筛选"))
    toolbar = st.columns([1, 2.2])
    selected_year = toolbar[0].selectbox(
        text("Período", "Period", "期间"),
        [None, *years],
        format_func=lambda value: all_label() if value is None else str(value),
    )
    selected_categories = toolbar[1].multiselect(
        text("Grupos", "Groups", "组别"),
        sorted(cost_control["CATEGORY-F"].dropna().astype(int).unique()),
        format_func=lambda code: f"{code} · {COST_CATEGORIES.get(code, '')}",
        placeholder=all_label(),
    )

filtered = cost_control
if selected_year is not None:
    filtered = filtered[filtered["DATE-F"].dt.year == selected_year]
if selected_categories:
    filtered = filtered[filtered["CATEGORY-F"].isin(selected_categories)]

pending = filtered[filtered["REVIEW_REASON"] != ""]
confirmed = filtered[filtered["INCLUDED_IN_COST"]].copy()

paid = confirmed[confirmed["PAID"]]
unpaid = confirmed[confirmed["PAYMENT_STATUS"] == "No pagado"]

amount_column = "NET-UF-F" if currency == "UF" else "NET-CLP-F"
format_amount = format_uf if currency == "UF" else format_clp_compact

with kpi_area:
    st.caption(
        text(
            f"SQLite · actualizado al {latest_date:%d/%m/%Y}",
            f"SQLite · updated {latest_date:%d/%m/%Y}",
            f"SQLite · 更新至 {latest_date:%Y/%m/%d}",
        )
    )
    render_kpis(
        [
            (text("Costo total", "Total cost", "总成本"), format_amount(confirmed[amount_column].sum())),
            (text("Costo pagado", "Paid cost", "已付成本"), format_amount(paid[amount_column].sum())),
            (text("Costo no pagado", "Unpaid cost", "未付成本"), format_amount(unpaid[amount_column].sum())),
            (text("Pendientes", "Pending review", "待复核"), f"{len(pending):,.0f}".replace(",", ".")),
        ]
    )

result_summary(
    text(
        f"{len(filtered):,.0f} registros · {confirmed['RUT_COMPLETO'].nunique()} proveedores · {confirmed['SUBCATEGORY-F'].nunique()} centros",
        f"{len(filtered):,.0f} records · {confirmed['RUT_COMPLETO'].nunique()} suppliers · {confirmed['SUBCATEGORY-F'].nunique()} centers",
        f"{len(filtered):,.0f} 条记录 · {confirmed['RUT_COMPLETO'].nunique()} 家供应商 · {confirmed['SUBCATEGORY-F'].nunique()} 个中心",
    ).replace(",", ".")
)

if confirmed.empty:
    st.info(text("Sin resultados", "No results", "无结果"))
    st.stop()

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

analysis_columns = st.columns([1, 1])
groups = (
    confirmed.groupby(["CATEGORY-F", "CATEGORY_NAME"], dropna=False)[amount_column]
    .sum()
    .reset_index()
    .sort_values(amount_column)
)
groups["GROUP"] = groups["CATEGORY-F"].fillna(0).astype(int).astype(str) + " · " + groups[
    "CATEGORY_NAME"
].fillna(text("Sin grupo", "No group", "无分组"))
group_chart = px.bar(
    groups,
    x=amount_column,
    y="GROUP",
    orientation="h",
    title=text("Costo por grupo", "Cost by group", "按组别划分的成本"),
    labels={amount_column: currency, "GROUP": ""},
    color_discrete_sequence=[CHART_COLORS[0]],
)
analysis_columns[0].plotly_chart(
    style_chart(group_chart, height=330, horizontal_legend=False),
    width="stretch",
    config=PLOTLY_CONFIG,
)

payment_mix = (
    confirmed.groupby("PAYMENT_STATUS", dropna=False)[amount_column]
    .sum()
    .reset_index()
)
payment_mix[amount_column] = payment_mix[amount_column].abs()
payment_mix = payment_mix[payment_mix[amount_column] > 0]
payment_mix["STATUS"] = payment_mix["PAYMENT_STATUS"].map(payment_status_label)
payment_chart = px.pie(
    payment_mix,
    names="STATUS",
    values=amount_column,
    hole=0.62,
    title=text("Composición de pago", "Payment composition", "付款构成"),
    color="STATUS",
    color_discrete_map={payment_status_label(key): value for key, value in STATUS_COLORS.items()},
)
payment_chart.update_traces(textinfo="percent", hovertemplate="%{label}<br>%{percent}<extra></extra>")
analysis_columns[1].plotly_chart(
    style_chart(payment_chart, height=330, horizontal_legend=False),
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
    center_summary["GRUPO"] = center_summary["CATEGORY-F"].fillna(0).astype(int).astype(str) + " · " + center_summary["CATEGORY_NAME"].fillna("")
    center_summary["CENTRO"] = center_summary["SUBCATEGORY-F"].fillna(0).astype(int).astype(str) + " · " + center_summary["SUBCATEGORY_NAME"].fillna("")
    center_summary = center_summary[
        [
            "GRUPO",
            "CENTRO",
            "DOCUMENTOS",
            net_column,
            f"PAGADO_{suffix}",
            f"NO_PAGADO_{suffix}",
            f"REVISAR_{suffix}",
        ]
    ]
    st.dataframe(
        executive_table_style(
            center_summary.rename(
                columns={
                    "GRUPO": text("Grupo", "Group", "组别"),
                    "CENTRO": text("Centro de costo", "Cost center", "成本中心"),
                    "DOCUMENTOS": text("Docs.", "Docs", "文档"),
                    net_column: text("Costo neto", "Net cost", "净成本"),
                    f"PAGADO_{suffix}": text("Pagado", "Paid", "已支付"),
                    f"NO_PAGADO_{suffix}": text("No pagado", "Unpaid", "未支付"),
                    f"REVISAR_{suffix}": text("Por revisar", "To review", "待复核"),
                }
            ),
            formats={
                text("Costo neto", "Net cost", "净成本"): format_amount_cell,
                text("Pagado", "Paid", "已支付"): format_amount_cell,
                text("No pagado", "Unpaid", "未支付"): format_amount_cell,
                text("Por revisar", "To review", "待复核"): format_amount_cell,
            },
            center_columns=[text("Docs.", "Docs", "文档")],
            left_columns=[text("Grupo", "Group", "组别"), text("Centro de costo", "Cost center", "成本中心")],
        ),
        hide_index=True,
        width="stretch",
        height=410,
    )

with provider_tab:
    providers = (
        confirmed.groupby(["RUT_COMPLETO", "SUPPLIER-F"], dropna=False)
        .agg(DOCUMENTOS=("INVOICE-F", "count"), NETO_CLP=("NET-CLP-F", "sum"), NETO_UF=("NET-UF-F", "sum"))
        .reset_index()
        .sort_values(f"NETO_{currency}", ascending=False)
    )
    providers["SUPPLIER-F"] = providers["SUPPLIER-F"].map(short_business_name)
    providers = providers[["RUT_COMPLETO", "SUPPLIER-F", "DOCUMENTOS", f"NETO_{currency}"]]
    provider_display = providers.rename(
        columns={
            "RUT_COMPLETO": "RUT",
            "SUPPLIER-F": text("Proveedor", "Supplier", "供应商"),
            "DOCUMENTOS": text("Docs.", "Docs", "文档"),
            f"NETO_{currency}": text("Costo neto", "Net cost", "净成本"),
        }
    )
    st.dataframe(
        executive_table_style(
            provider_display,
            formats={text("Costo neto", "Net cost", "净成本"): format_amount_cell},
            center_columns=["RUT", text("Docs.", "Docs", "文档")],
            left_columns=[text("Proveedor", "Supplier", "供应商")],
        ),
        hide_index=True,
        width="stretch",
        height=410,
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
    pending_display[text("Proveedor", "Supplier", "供应商")] = pending_display[text("Proveedor", "Supplier", "供应商")].map(short_business_name)
    st.dataframe(
        executive_table_style(
            pending_display,
            formats={text(f"Total {currency}", f"Total {currency}", f"总额 {currency}"): format_amount_cell},
            center_columns=["RUT", text("Tipo", "Type", "类型"), text("Folio", "Number", "编号"), text("Fecha", "Date", "日期")],
            left_columns=[text("Proveedor", "Supplier", "供应商"), text("Motivo", "Reason", "原因")],
        ),
        hide_index=True,
        width="stretch",
        height=410,
    )
