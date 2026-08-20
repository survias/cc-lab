from __future__ import annotations

import pandas as pd
import plotly.express as px
import streamlit as st

from utils.budget_report import (
    CATEGORY_NAMES,
    available_actual_months,
    budget_comparison,
    build_actual_records,
    build_budget_workbook,
)
from utils.config import BUDGET_TEMPLATE_PATH, DATABASE_PATH
from utils.i18n import current_currency, text
from utils.legacy_data import load_cost_control
from utils.ui_helpers import (
    CHART_COLORS,
    PLOTLY_CONFIG,
    filter_heading,
    format_clp_compact,
    format_uf,
    page_heading,
    render_kpis,
    result_summary,
    short_business_name,
    show_historical_data_error,
    style_chart,
)
from utils.uf_data import get_current_uf_rate


page_heading(text("Budget y costos", "Budget & costs", "预算与成本"))
currency = current_currency()
conversion_factor = get_current_uf_rate() if currency == "CLP" else 1.0
format_amount = format_clp_compact if currency == "CLP" else format_uf


def metric_amount(value: float) -> str:
    if currency == "CLP":
        return format_clp_compact(value)
    if abs(value) >= 1_000_000:
        compact = f"{value / 1_000_000:,.2f}".replace(",", "_").replace(".", ",").replace("_", ".")
        return f"{compact} MM UF"
    return format_uf(value)

try:
    ledger = load_cost_control()
    periods = available_actual_months(ledger)
except Exception as exc:
    show_historical_data_error(exc)
    st.stop()

if not periods:
    st.info(text("Sin costos pagados.", "No paid costs.", "没有已付成本。"))
    st.stop()

kpi_area = st.container()
filter_heading(text("Filtros del análisis", "Analysis filters", "分析筛选"))
period_labels = {period.strftime("%m/%Y"): period for period in periods}
selector_column, _ = st.columns([1, 2.2], vertical_alignment="bottom")
with selector_column:
    selected_label = st.selectbox(
        text("Período", "Period", "期间"),
        list(period_labels),
        index=len(period_labels) - 1,
    )
selected_period = period_labels[selected_label]
database_mtime = DATABASE_PATH.stat().st_mtime_ns
template_mtime = BUDGET_TEMPLATE_PATH.stat().st_mtime_ns
export_key = (
    database_mtime,
    template_mtime,
    selected_period.strftime("%Y-%m"),
)

try:
    comparison = budget_comparison(ledger, selected_period)
except Exception as exc:
    show_historical_data_error(exc)
    st.stop()

category_codes = set(comparison["REPORT_CATEGORY"].astype(int))
category_options = {
    code: name for code, name in CATEGORY_NAMES.items() if code in category_codes
}
selected_category = st.selectbox(
    text("Centros", "Cost centers", "成本中心"),
    [None, *category_options],
    index=0,
    format_func=lambda code: text("Todos", "All", "全部") if code is None else f"{code} · {category_options[code]}",
)
selected_categories = list(category_options) if selected_category is None else [selected_category]
selection_label = (
    text("Todos los centros", "All cost centers", "所有成本中心")
    if selected_category is None
    else f"{selected_category} · {category_options[selected_category]}"
)
filtered = comparison[comparison["REPORT_CATEGORY"].isin(selected_categories)].copy()
if filtered.empty:
    st.info(text("Sin resultados", "No results", "无结果"))
    st.stop()

for column in ("BUDGET_UF", "ACTUAL_UF", "BALANCE_UF"):
    filtered[column] *= conversion_factor

total_budget = filtered["BUDGET_UF"].sum()
total_actual = filtered["ACTUAL_UF"].sum()
balance = total_budget - total_actual
execution = total_actual / total_budget if total_budget else 0

with kpi_area:
    render_kpis(
        [
            (text("Budget", "Budget", "预算"), metric_amount(total_budget)),
            (text("Costo real", "Actual cost", "实际成本"), metric_amount(total_actual)),
            (text("Saldo", "Balance", "余额"), metric_amount(balance)),
            (text("Ejecución", "Execution", "执行率"), f"{execution:.1%}"),
        ]
    )

result_summary(
    text(
        f"Corte {selected_label} · {selection_label}",
        f"Through {selected_label} · {selection_label}",
        f"截至 {selected_label} · {selection_label}",
    )
)

trend_tab, centers_tab, suppliers_tab = st.tabs(
    [
        text("Evolución", "Trend", "趋势"),
        text("Centros", "Cost centers", "成本中心"),
        text("Proveedores", "Suppliers", "供应商"),
    ]
)

with trend_tab:
    monthly = (
        filtered.groupby("MONTH")[["BUDGET_UF", "ACTUAL_UF"]]
        .sum()
        .reset_index()
        .sort_values("MONTH")
    )
    monthly["BALANCE"] = monthly["BUDGET_UF"] - monthly["ACTUAL_UF"]
    monthly["EXECUTION"] = monthly["ACTUAL_UF"].div(
        monthly["BUDGET_UF"].replace(0, pd.NA)
    )
    labels = {
        "BUDGET_UF": text("Budget", "Budget", "预算"),
        "ACTUAL_UF": text("Costo real", "Actual cost", "实际成本"),
    }
    chart_data = monthly.melt(
        id_vars="MONTH",
        value_vars=["BUDGET_UF", "ACTUAL_UF"],
        var_name="SERIES_KEY",
        value_name=currency,
    )
    chart_data["SERIES"] = chart_data["SERIES_KEY"].map(labels)
    chart = px.line(
        chart_data,
        x="MONTH",
        y=currency,
        color="SERIES",
        labels={"MONTH": text("Mes", "Month", "月份"), "SERIES": ""},
        color_discrete_sequence=[CHART_COLORS[1], CHART_COLORS[3]],
    )
    st.plotly_chart(
        style_chart(chart, height=380), width="stretch", config=PLOTLY_CONFIG
    )
    monthly_table = monthly.rename(
        columns={
            "MONTH": text("Mes", "Month", "月份"),
            "BUDGET_UF": text(f"Budget {currency}", f"Budget {currency}", f"预算 {currency}"),
            "ACTUAL_UF": text(f"Real {currency}", f"Actual {currency}", f"实际 {currency}"),
            "BALANCE": text(f"Saldo {currency}", f"Balance {currency}", f"余额 {currency}"),
            "EXECUTION": text("Ejecución", "Execution", "执行率"),
        }
    )
    for amount_column in monthly_table.columns[1:4]:
        monthly_table[amount_column] = monthly_table[amount_column].map(format_amount)
    st.dataframe(
        monthly_table,
        hide_index=True,
        width="stretch",
        height=360,
        column_config={
            monthly_table.columns[0]: st.column_config.DateColumn(
                monthly_table.columns[0], format="MM/YYYY"
            ),
            monthly_table.columns[4]: st.column_config.ProgressColumn(
                monthly_table.columns[4], format="percent", min_value=0, max_value=1
            ),
        },
    )

with centers_tab:
    center_summary = (
        filtered.groupby(["REPORT_CATEGORY", "CATEGORY_NAME"])[
            ["BUDGET_UF", "ACTUAL_UF"]
        ]
        .sum()
        .reset_index()
        .sort_values("REPORT_CATEGORY")
    )
    center_summary["BALANCE"] = center_summary["BUDGET_UF"] - center_summary["ACTUAL_UF"]
    center_summary["EXECUTION"] = center_summary["ACTUAL_UF"].div(
        center_summary["BUDGET_UF"].replace(0, pd.NA)
    )
    center_chart = px.bar(
        center_summary,
        x="CATEGORY_NAME",
        y=["BUDGET_UF", "ACTUAL_UF"],
        barmode="group",
        labels={"CATEGORY_NAME": text("Centro", "Center", "中心"), "value": currency},
        color_discrete_sequence=[CHART_COLORS[1], CHART_COLORS[3]],
    )
    center_chart.for_each_trace(
        lambda trace: trace.update(
            name={
                "BUDGET_UF": text("Budget", "Budget", "预算"),
                "ACTUAL_UF": text("Costo real", "Actual cost", "实际成本"),
            }.get(trace.name, trace.name)
        )
    )
    st.plotly_chart(
        style_chart(center_chart, height=390), width="stretch", config=PLOTLY_CONFIG
    )
    center_table = center_summary.copy()
    for amount_column in ("BUDGET_UF", "ACTUAL_UF", "BALANCE"):
        center_table[amount_column] = center_table[amount_column].map(format_amount)
    st.dataframe(
        center_table,
        hide_index=True,
        width="stretch",
        column_config={
            "REPORT_CATEGORY": text("Código", "Code", "编码"),
            "CATEGORY_NAME": text("Centro", "Center", "中心"),
            "BUDGET_UF": text(f"Budget {currency}", f"Budget {currency}", f"预算 {currency}"),
            "ACTUAL_UF": text(f"Real {currency}", f"Actual {currency}", f"实际 {currency}"),
            "BALANCE": text(f"Saldo {currency}", f"Balance {currency}", f"余额 {currency}"),
            "EXECUTION": st.column_config.ProgressColumn(
                text("Ejecución", "Execution", "执行率"),
                format="percent",
                min_value=0,
                max_value=1,
            ),
        },
    )

with suppliers_tab:
    actual = build_actual_records(ledger, selected_period)
    actual = actual[actual["REPORT_CATEGORY"].isin(selected_categories)].copy()
    actual["AMOUNT"] = actual["NET_UF"] * conversion_factor
    suppliers = (
        actual.groupby(
            [
                "REPORT_CATEGORY", "CATEGORY_NAME", "SUBCATEGORY-F",
                "SUBCATEGORY_NAME", "RUT_COMPLETO", "SUPPLIER-F",
            ],
            dropna=False,
        )
        .agg(DOCUMENTOS=("DOCUMENT_ID", "count"), COSTO=("AMOUNT", "sum"))
        .reset_index()
        .sort_values(["REPORT_CATEGORY", "COSTO"], ascending=[True, False])
    )
    suppliers["SUPPLIER-F"] = suppliers["SUPPLIER-F"].map(short_business_name)
    suppliers["CENTRO"] = suppliers["SUBCATEGORY-F"].fillna(0).astype(int).astype(str) + " · " + suppliers["SUBCATEGORY_NAME"].fillna("")
    suppliers = suppliers[["CATEGORY_NAME", "CENTRO", "RUT_COMPLETO", "SUPPLIER-F", "DOCUMENTOS", "COSTO"]]
    suppliers["COSTO"] = suppliers["COSTO"].map(format_amount)
    st.dataframe(
        suppliers,
        hide_index=True,
        width="stretch",
        height=540,
        column_config={
            "CATEGORY_NAME": text("Nombre", "Name", "名称"),
            "CENTRO": text("Centro de costo", "Cost center", "成本中心"),
            "RUT_COMPLETO": "RUT",
            "SUPPLIER-F": text("Proveedor", "Supplier", "供应商"),
            "DOCUMENTOS": text("Docs.", "Docs", "文档"),
            "COSTO": text(f"Costo {currency}", f"Cost {currency}", f"成本 {currency}"),
        },
    )

st.divider()
export_text, export_action = st.columns([3.2, 1], vertical_alignment="bottom")
with export_text:
    st.caption(
        text(
            "Entregable oficial Cost vs Budget para el período seleccionado.",
            "Official Cost vs Budget deliverable for the selected period.",
            "所选期间的官方成本与预算交付文件。",
        )
    )
with export_action:
    if st.button(
        text("Preparar Excel", "Prepare Excel", "生成 Excel"),
        icon=":material/table_view:",
        type="secondary",
        width="content",
        key="prepare_budget_excel",
    ):
        with st.spinner(text("Generando Excel...", "Generating Excel...", "正在生成 Excel...")):
            st.session_state["budget_export"] = build_budget_workbook(
                selected_period, ledger
            )
            st.session_state["budget_export_key"] = export_key
    if st.session_state.get("budget_export_key") == export_key:
        export = st.session_state["budget_export"]
        st.download_button(
            text("Descargar Excel", "Download Excel", "下载 Excel"),
            data=export.content,
            file_name=export.filename,
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            icon=":material/download:",
            width="content",
        )
