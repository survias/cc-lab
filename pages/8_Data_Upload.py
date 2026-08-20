from __future__ import annotations

import streamlit as st

from utils.i18n import text
from utils.legacy_data import source_inventory
from utils.config import DATABASE_PATH
from utils.payment_data import get_active_payment_summary
from utils.ui_helpers import page_heading


page_heading(text("Fuentes", "Sources", "数据源"))

inventory = source_inventory()
available = int(inventory["Disponible"].sum())
payment_summary = get_active_payment_summary()

metrics = st.columns(3)
metrics[0].metric(text("Fuentes activas", "Active sources", "活动数据源"), available)
metrics[1].metric(text("Tamaño", "Size", "大小"), f"{DATABASE_PATH.stat().st_size / 1_048_576:.1f} MB")
metrics[2].metric(text("Modo", "Mode", "模式"), text("Base maestra", "Master database", "主数据库"))

display = inventory.rename(
    columns={
        "Fuente": text("Fuente", "Source", "数据源"),
        "Archivo": text("Archivo", "File", "文件"),
        "Disponible": text("Disponible", "Available", "可用"),
        "Registros": text("Registros", "Records", "记录"),
        "Tamaño MB": text("Tamaño MB", "Size MB", "大小 MB"),
    }
)
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
