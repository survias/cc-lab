from __future__ import annotations

import streamlit as st

from utils.auth import require_authentication
from utils.config import APPLICATION_NAME, APPLY_MIGRATIONS_ON_STARTUP
from utils.i18n import currency_selector, language_selector, text
from utils.migrations import apply_pending_migrations


st.set_page_config(
    page_title=APPLICATION_NAME,
    page_icon=":material/account_balance:",
    layout="wide",
    initial_sidebar_state="auto",
)

require_authentication()

if APPLY_MIGRATIONS_ON_STARTUP:
    apply_pending_migrations()

st.markdown(
    """
    <style>
    :root {
        --cc-green: #006c5b;
        --cc-ink: #17201e;
        --cc-muted: #66736f;
        --cc-line: #dfe5e2;
        --cc-soft: #f5f7f6;
    }
    [data-testid="stAppViewContainer"] > .main .block-container {
        max-width: 1500px;
        padding-top: 1.6rem;
        padding-bottom: 2.5rem;
    }
    [data-testid="stSidebar"] {
        border-right: 1px solid var(--cc-line);
    }
    [data-testid="stSidebar"] [data-testid="stVerticalBlock"] {
        gap: 0.35rem;
    }
    h1, h2, h3, h4, p, label, button {
        letter-spacing: 0 !important;
    }
    h1 {
        font-size: 1.85rem !important;
        font-weight: 680 !important;
        margin-bottom: 0.15rem !important;
    }
    h2, h3 {
        font-weight: 650 !important;
    }
    h3 {
        font-size: 1.05rem !important;
    }
    [data-testid="stCaptionContainer"] {
        color: var(--cc-muted);
    }
    [data-testid="stMetric"] {
        background: #ffffff;
        border: 0;
        border-top: 1px solid var(--cc-line);
        border-bottom: 1px solid var(--cc-line);
        border-radius: 0;
        padding: 0.8rem 0.9rem;
        min-height: 92px;
    }
    [data-testid="stMetricLabel"] {
        color: var(--cc-muted);
        font-size: 0.78rem;
    }
    [data-testid="stMetricValue"] {
        color: var(--cc-ink);
        font-size: 1.35rem;
        font-weight: 650;
    }
    [data-testid="stExpander"] {
        border-color: var(--cc-line);
        border-radius: 6px;
    }
    [data-testid="stDataFrame"] {
        border: 1px solid var(--cc-line);
        border-radius: 2px;
        overflow: hidden;
    }
    .stButton > button, .stDownloadButton > button {
        border-radius: 4px;
        min-height: 2.35rem;
    }
    button[data-baseweb="tab"] {
        font-weight: 600;
        padding-left: 0.75rem;
        padding-right: 0.75rem;
    }
    [data-baseweb="tab-list"] {
        border-bottom: 1px solid var(--cc-line);
    }
    div[data-testid="stPlotlyChart"] {
        border-top: 1px solid #e3e7e5;
        padding-top: 0.1rem;
    }
    [data-testid="stAlert"] {
        border-radius: 6px;
        padding-top: 0.6rem;
        padding-bottom: 0.6rem;
    }
    .cc-source-line {
        display: flex;
        align-items: center;
        flex-wrap: wrap;
        gap: 0.45rem;
        color: var(--cc-muted);
        font-size: 0.82rem;
        margin: 0.2rem 0 0.8rem;
    }
    .cc-source-line > span {
        min-width: 0;
        max-width: 100%;
        overflow-wrap: anywhere;
    }
    .cc-badge {
        display: inline-flex;
        align-items: center;
        border: 1px solid #9fc7bd;
        border-radius: 4px;
        background: #edf7f4;
        color: var(--cc-green);
        font-weight: 650;
        padding: 0.12rem 0.42rem;
    }
    .cc-title-rule {
        width: 38px;
        height: 3px;
        background: #c74634;
        margin: 0.15rem 0 0.75rem;
    }
    .cc-meta {
        color: #707876;
        font-size: 0.78rem;
        margin-bottom: 0.8rem;
    }
    .cc-kpis {
        display: grid;
        grid-template-columns: repeat(4, minmax(0, 1fr));
        border-top: 1px solid #d9dedc;
        border-bottom: 1px solid #d9dedc;
        margin: 0.3rem 0 1rem;
    }
    .cc-kpis-3 { grid-template-columns: repeat(3, minmax(0, 1fr)); }
    .cc-kpis-5 { grid-template-columns: repeat(5, minmax(0, 1fr)); }
    .cc-kpi {
        padding: 0.9rem 1rem 0.95rem;
        min-width: 0;
    }
    .cc-kpi:first-child { padding-left: 0; }
    .cc-kpi + .cc-kpi { border-left: 1px solid #e2e6e4; }
    .cc-kpi span {
        display: block;
        color: #727a78;
        font-size: 0.7rem;
        text-transform: uppercase;
    }
    .cc-kpi strong {
        display: block;
        color: #202625;
        font-size: 1.42rem;
        line-height: 1.25;
        margin-top: 0.2rem;
    }
    .cc-kpi small {
        display: block;
        color: #7a8280;
        font-size: 0.72rem;
        margin-top: 0.18rem;
    }
    .cc-filter-heading {
        color: #202625;
        font-size: 0.92rem;
        font-weight: 650;
        margin: 0.15rem 0 0.15rem;
    }
    .cc-filter-summary {
        color: #707876;
        font-size: 0.76rem;
        margin: -0.1rem 0 0.35rem;
    }
    [data-testid="stHeaderActionElements"], .stAppDeployButton {
        display: none;
    }
    @media (max-width: 760px) {
        [data-testid="stAppViewContainer"] > .main .block-container {
            padding-top: 1rem;
        }
        [data-testid="stHorizontalBlock"] {
            flex-direction: column;
            gap: 0.75rem;
        }
        [data-testid="stHorizontalBlock"] > [data-testid="stColumn"] {
            width: 100% !important;
            flex: 1 1 auto !important;
        }
        .cc-kpis, .cc-kpis-3, .cc-kpis-5 {
            grid-template-columns: repeat(2, minmax(0, 1fr));
        }
        .cc-kpi:nth-child(odd) { border-left: 0; }
    }
    </style>
    """,
    unsafe_allow_html=True,
)

currency_selector()
language_selector()

pages = [
    st.Page(
        "pages/1_Menu.py",
        title=text("Resumen", "Overview", "概览"),
        icon=":material/home:",
        default=True,
    ),
    st.Page(
        "pages/2_Invoicing_Data.py",
        title=text("Costos", "Costs", "成本"),
        icon=":material/receipt_long:",
    ),
    st.Page(
        "pages/3_Payment_Data.py",
        title=text("Pagos", "Payments", "付款"),
        icon=":material/payments:",
    ),
    st.Page(
        "pages/5_Bidding_Costs.py",
        title=text("Bidding", "Bidding", "投标"),
        icon=":material/request_quote:",
    ),
    st.Page(
        "pages/6_Contracts.py",
        title=text("Contratos", "Contracts", "合同"),
        icon=":material/contract:",
    ),
    st.Page(
        "pages/7_Construction_Costs.py",
        title=text("Construcción", "Construction", "施工成本"),
        icon=":material/construction:",
    ),
    st.Page(
        "pages/7_Data_Quality.py",
        title=text("Calidad", "Data quality", "数据质量"),
        icon=":material/fact_check:",
    ),
]

navigation = st.navigation(pages)
navigation.run()
