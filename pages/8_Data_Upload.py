from __future__ import annotations

import plotly.express as px
import streamlit as st

from utils.i18n import text
from utils.legacy_data import source_inventory
from utils.config import DATABASE_PATH
from utils.payment_data import get_active_payment_summary
from utils.ui_helpers import (
    CHART_COLORS,
    PLOTLY_CONFIG,
    filter_heading,
    page_heading,
    render_kpis,
    result_summary,
    style_chart,
)


page_heading(text("Fuentes", "Sources", "数据源"))

inventory = source_inventory()
available = int(inventory["Disponible"].sum())
payment_summary = get_active_payment_summary()

render_kpis(
    [
        (text("Fuentes activas", "Active sources", "活动数据源"), str(available)),
        (text("Registros SQLite", "SQLite records", "SQLite 记录"), f"{inventory.loc[inventory['Archivo'] == 'cc_lab.sqlite', 'Registros'].sum():,.0f}".replace(",", ".")),
        (text("Tamaño", "Size", "大小"), f"{DATABASE_PATH.stat().st_size / 1_048_576:.1f} MB"),
        (text("Modo", "Mode", "模式"), text("Base maestra", "Master database", "主数据库")),
    ]
)

filter_heading(text("Filtros de fuentes", "Source filters", "数据源筛选"))
status_filter = st.selectbox(
    text("Disponibilidad", "Availability", "可用性"),
    [None, True, False],
    format_func=lambda value: text("Todas", "All", "全部") if value is None else (
        text("Disponibles", "Available", "可用") if value else text("No disponibles", "Unavailable", "不可用")
    ),
)
filtered_inventory = inventory if status_filter is None else inventory[inventory["Disponible"] == status_filter]
result_summary(
    text(
        f"{len(filtered_inventory)} fuentes en la selección",
        f"{len(filtered_inventory)} sources in the selection",
        f"当前选择中有 {len(filtered_inventory)} 个数据源",
    )
)

chart_data = filtered_inventory.dropna(subset=["Registros"]).copy()
if not chart_data.empty:
    source_chart = px.bar(
        chart_data.sort_values("Registros"),
        x="Registros",
        y="Fuente",
        orientation="h",
        title=text("Registros por fuente", "Records by source", "按数据源划分的记录"),
        color_discrete_sequence=[CHART_COLORS[0]],
    )
    st.plotly_chart(style_chart(source_chart, height=310, horizontal_legend=False), width="stretch", config=PLOTLY_CONFIG)

display = filtered_inventory.rename(
    columns={
        "Fuente": text("Fuente", "Source", "数据源"),
        "Archivo": text("Archivo", "File", "文件"),
        "Disponible": text("Disponible", "Available", "可用"),
        "Registros": text("Registros", "Records", "记录"),
        "Tamaño MB": text("Tamaño MB", "Size MB", "大小 MB"),
    }
)
summary_tab, detail_tab = st.tabs(
    [text("Resumen", "Overview", "概览"), text("Detalle técnico", "Technical detail", "技术明细")]
)
with summary_tab:
    executive_columns = [
        text("Fuente", "Source", "数据源"),
        text("Disponible", "Available", "可用"),
        text("Registros", "Records", "记录"),
        text("Tamaño MB", "Size MB", "大小 MB"),
    ]
    st.dataframe(display[executive_columns], hide_index=True, width="stretch", height=320)
with detail_tab:
    st.dataframe(display, hide_index=True, width="stretch", height=360)
if payment_summary:
    st.caption(
        text(
            f"Pagos activos: {int(payment_summary['payment_count']):,} · "
            f"{payment_summary['first_payment_date']} a {payment_summary['last_payment_date']}",
            f"Active payments: {int(payment_summary['payment_count']):,} · "
            f"{payment_summary['first_payment_date']} to {payment_summary['last_payment_date']}",
            f"当前付款：{int(payment_summary['payment_count']):,} · "
            f"{payment_summary['first_payment_date']} 至 {payment_summary['last_payment_date']}",
        ).replace(",", ".")
    )
st.caption(
    text(
        "Las cargas mensuales y conciliaciones se gestionan en Calidad.",
        "Monthly uploads and reconciliations are managed in Quality.",
        "月度上传和对账在质量模块中管理。",
    )
)
