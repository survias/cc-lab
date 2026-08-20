from __future__ import annotations

import pandas as pd
import streamlit as st

from utils.config import LOGO_PATH
from utils.i18n import current_currency, text


CHART_COLORS = ["#006C5B", "#315B7D", "#D28B32", "#A94F45", "#6B7280", "#78998D"]
STATUS_COLORS = {
    "Pagado": "#006C5B",
    "No pagado": "#D28B32",
    "Pago sin documento": "#6E7472",
    "Revisar cruce": "#A94F45",
    "Nota de crédito": "#315B7D",
    "Sin clave": "#8A9290",
}
PLOTLY_CONFIG = {"displayModeBar": False, "responsive": True}


def apply_branding() -> None:
    if LOGO_PATH.exists():
        st.logo(str(LOGO_PATH))


def page_heading(title: str, caption: str | None = None) -> None:
    apply_branding()
    st.title(title)
    if caption:
        st.caption(caption)


def style_chart(figure, *, height: int = 360, horizontal_legend: bool = True):
    legend = (
        {"orientation": "h", "yanchor": "bottom", "y": 1.02, "xanchor": "right", "x": 1}
        if horizontal_legend
        else {"orientation": "v"}
    )
    figure.update_layout(
        template="plotly_white",
        colorway=CHART_COLORS,
        height=height,
        margin={"l": 10, "r": 10, "t": 48, "b": 10},
        font={"family": "Arial, sans-serif", "size": 12, "color": "#34413D"},
        legend={**legend, "title_text": ""},
        hoverlabel={"font_size": 12},
    )
    if figure.layout.title and figure.layout.title.text:
        figure.update_layout(title_font={"size": 15, "color": "#17201E"}, title_x=0)
    figure.update_xaxes(gridcolor="#EDF1EF", zeroline=False)
    figure.update_yaxes(gridcolor="#EDF1EF", zeroline=False)
    return figure


def format_clp(value: float | int | None) -> str:
    if value is None or pd.isna(value):
        return "$0"
    return f"${value:,.0f}".replace(",", ".")


def format_clp_compact(value: float | int | None) -> str:
    if value is None or pd.isna(value):
        return "$0"
    numeric_value = float(value)
    if abs(numeric_value) < 1_000_000:
        return format_clp(numeric_value)
    decimals = 0 if abs(numeric_value) >= 100_000_000 else 2
    formatted = f"{numeric_value / 1_000_000:,.{decimals}f}"
    formatted = formatted.replace(",", "_").replace(".", ",").replace("_", ".")
    return f"${formatted} MM"


def format_period(period: str | int | None) -> str:
    text = "" if period is None else str(period)
    if len(text) == 6 and text.isdigit():
        return f"{text[4:6]}/{text[:4]}"
    return text or "Sin información"


def format_uf(value: float | int | None, decimals: int = 0) -> str:
    if value is None or pd.isna(value):
        return "0 UF"
    formatted = f"{float(value):,.{decimals}f}"
    formatted = formatted.replace(",", "_").replace(".", ",").replace("_", ".")
    return f"{formatted} UF"


def format_currency(value: float | int | None, decimals: int = 0) -> str:
    return format_uf(value, decimals) if current_currency() == "UF" else format_clp_compact(value)


def dataframe_to_csv_bytes(frame: pd.DataFrame) -> bytes:
    return frame.to_csv(index=False, sep=";").encode("utf-8-sig")


def show_database_error(exc: Exception) -> None:
    st.error(text("No fue posible consultar SQLite.", "SQLite query failed.", "SQLite 查询失败。"))
    st.code(str(exc))


def show_historical_data_error(exc: Exception) -> None:
    st.error(
        text(
            "No fue posible cargar la base maestra.",
            "The master database could not be loaded.",
            "无法加载主数据库。",
        )
    )
    st.code(str(exc))


def render_pending_module(title: str, description: str) -> None:
    page_heading(title, "Módulo pendiente de migración")
    st.info(description)
    st.write(
        "La página ya está integrada en la navegación, pero no utiliza datos ni lógica de la "
        "aplicación antigua. Se habilitará en una etapa posterior."
    )
