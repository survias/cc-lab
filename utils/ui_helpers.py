from __future__ import annotations

from html import escape
import re
import unicodedata

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
    "Anulada por NC": "#78998D",
    "Sin clave": "#8A9290",
}
PLOTLY_CONFIG = {"displayModeBar": False, "responsive": True}


def apply_branding() -> None:
    if LOGO_PATH.exists():
        st.logo(str(LOGO_PATH))


def page_heading(title: str, caption: str | None = None) -> None:
    apply_branding()
    st.title(title)
    st.markdown('<div class="cc-title-rule"></div>', unsafe_allow_html=True)
    if caption:
        st.caption(caption)


def render_kpis(items: list[tuple[str, str] | tuple[str, str, str | None]]) -> None:
    """Render the compact executive KPI strip used across the application."""
    cards: list[str] = []
    for item in items:
        label, value = item[:2]
        detail = item[2] if len(item) > 2 else None
        detail_html = f"<small>{escape(str(detail))}</small>" if detail else ""
        cards.append(
            '<div class="cc-kpi">'
            f"<span>{escape(str(label))}</span>"
            f"<strong>{escape(str(value))}</strong>"
            f"{detail_html}</div>"
        )
    st.markdown(
        f'<div class="cc-kpis cc-kpis-{min(len(cards), 5)}">{"".join(cards)}</div>',
        unsafe_allow_html=True,
    )


def filter_heading(title: str, summary: str | None = None) -> None:
    st.markdown(f'<div class="cc-filter-heading">{escape(title)}</div>', unsafe_allow_html=True)
    if summary:
        st.markdown(
            f'<div class="cc-filter-summary">{escape(summary)}</div>',
            unsafe_allow_html=True,
        )


def result_summary(value: str) -> None:
    st.markdown(f'<div class="cc-filter-summary">{escape(value)}</div>', unsafe_allow_html=True)


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


def format_amount_cell(value: float | int | None, *, decimals: int = 1) -> str:
    """Format a table amount with an explicit unit in every cell."""
    if current_currency() == "UF":
        return format_uf(value, decimals)
    return format_clp(value)


def short_business_name(value: object, max_length: int = 32) -> str:
    original = str(value or text("Sin proveedor", "No supplier", "无供应商")).strip()
    normalized = unicodedata.normalize("NFKD", original.upper())
    normalized = "".join(char for char in normalized if not unicodedata.combining(char))
    known_names = (
        ("CHINA RAILWAY", "CRCCI"),
        ("MINISTERIO DE OBRAS PUBLICAS", "MOP"),
        ("SOCIEDAD CONCESIONARIA RUTA 5 TALCA CHILLAN", "Survías"),
        ("INGENIERIA GESTION Y CONTROL", "IGYC"),
        ("KAPSCH TRAFFICCOM", "Kapsch"),
        ("ALPHA INGENIEROS", "Alpha"),
    )
    for fragment, alias in known_names:
        if fragment in normalized:
            return alias
    shortened = re.sub(
        r"\b(SOCIEDAD ANONIMA|EMPRESA INDIVIDUAL DE RESPONSABILIDAD LIMITADA|"
        r"LIMITADA|LTDA|SPA|S A|EIRL)\b\.?,?",
        "",
        original,
        flags=re.IGNORECASE,
    )
    shortened = " ".join(shortened.split()).strip(" ,.-") or original
    return shortened if len(shortened) <= max_length else f"{shortened[: max_length - 1].rstrip()}…"


def executive_table_style(
    frame: pd.DataFrame,
    *,
    formats: dict[str, object] | None = None,
    center_columns: list[str] | tuple[str, ...] = (),
    left_columns: list[str] | tuple[str, ...] = (),
):
    """Keep business tables visually consistent without changing their data."""
    styler = frame.style.format(formats or {}, na_rep="—")
    existing_center = [column for column in center_columns if column in frame.columns]
    existing_left = [column for column in left_columns if column in frame.columns]
    if existing_center:
        styler = styler.set_properties(subset=existing_center, **{"text-align": "center"})
    if existing_left:
        styler = styler.set_properties(subset=existing_left, **{"text-align": "left"})
    return styler.set_table_styles(
        [
            {
                "selector": "th",
                "props": "text-align: center; font-weight: 650; color: #4E5956;",
            }
        ]
    )


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
