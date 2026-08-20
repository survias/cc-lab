from __future__ import annotations

import streamlit as st


LANGUAGE_LABELS = {
    "es": "ES",
    "en": "EN",
    "zh": "中文",
}

CURRENCIES = ("UF", "CLP")


def currency_selector() -> str:
    if st.session_state.get("app_currency") not in CURRENCIES:
        st.session_state.app_currency = "UF"
    return st.sidebar.segmented_control(
        text("Moneda", "Currency", "币种"),
        options=CURRENCIES,
        key="app_currency",
        width="stretch",
    )


def language_selector() -> str:
    if st.session_state.get("app_language") not in LANGUAGE_LABELS:
        st.session_state.app_language = "es"
    return st.sidebar.radio(
        "Idioma",
        options=list(LANGUAGE_LABELS),
        format_func=LANGUAGE_LABELS.get,
        key="app_language",
        label_visibility="collapsed",
        horizontal=True,
    )


def current_language() -> str:
    language = st.session_state.get("app_language", "es")
    return language if language in LANGUAGE_LABELS else "es"


def current_currency() -> str:
    currency = st.session_state.get("app_currency", "UF")
    return currency if currency in CURRENCIES else "UF"


def text(spanish: str, english: str, chinese: str) -> str:
    return {
        "es": spanish,
        "en": english,
        "zh": chinese,
    }[current_language()]


def payment_status_label(status: str) -> str:
    labels = {
        "Pagado": ("Pagado", "Paid", "已支付"),
        "No pagado": ("No pagado", "Unpaid", "未支付"),
        "Pago sin documento": ("Pago sin documento", "Payment without document", "无凭证付款"),
        "Revisar cruce": ("Revisar cruce", "Review match", "待复核"),
        "Nota de crédito": ("Nota de crédito", "Credit note", "贷项通知单"),
        "Anulada por NC": ("Anulada por NC", "Cancelled by credit note", "已由贷项通知单取消"),
        "Sin clave": ("Sin clave", "Missing key", "缺少键值"),
    }
    values = labels.get(status)
    return text(*values) if values else status


def all_label() -> str:
    return text("Todos", "All", "全部")
