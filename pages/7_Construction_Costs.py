from __future__ import annotations

import re
import unicodedata
from datetime import date

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots

from utils.construction_data import (
    ConstructionFilters,
    get_active_construction_import,
    get_construction_filter_options,
    get_construction_items,
)
from utils.i18n import all_label, current_currency, text
from utils.ui_helpers import dataframe_to_csv_bytes, page_heading, show_database_error, style_chart


RED = "#C74634"
RED_DARK = "#8E2F2A"
INK = "#202625"
GRAPHITE = "#58605E"
STEEL = "#929997"
MIST = "#DDE2E0"
AMBER = "#A88145"

CHART_CONFIG = {
    "displayModeBar": "hover",
    "displaylogo": False,
    "responsive": True,
    "modeBarButtonsToRemove": ["lasso2d", "select2d", "autoScale2d"],
}


def observation_label(value: str) -> str:
    labels = {
        "APPROVED_EXPLICIT": text("Aprobado", "Approved", "已批准"),
        "NO_OBSERVATION": text("Sin observación", "No observation", "无意见"),
        "OBSERVED": text("Observado", "Observed", "有意见"),
    }
    return labels.get(value, value)


def support_label(value: str) -> str:
    labels = {
        "PENDING_CLASSIFICATION": text("Sin respaldo", "No support", "无凭证"),
        "SII_DOCUMENT": text("Documento SII", "SII document", "SII 文档"),
        "TRANSFER": text("Pago", "Payment", "付款"),
        "REMUNERATION": text("Remuneraciones", "Payroll", "薪酬"),
        "MOP_PAYMENT": text("Pago MOP", "MOP payment", "MOP 付款"),
        "LEASE": text("Arrendamiento", "Lease", "租赁"),
        "OTHER_NON_TAX": text("Otro respaldo", "Other support", "其他凭证"),
    }
    return labels.get(value, value)


def reconciliation_label(value: str) -> str:
    labels = {
        "MATCHED_EXACT": text("Vinculado", "Linked", "已关联"),
        "MATCHED_PARTIAL": text("Parcial", "Partial", "部分关联"),
        "MATCHED_PAYMENT": text("Pago vinculado", "Linked payment", "已关联付款"),
        "AGGREGATE_SUPPORT": text("Respaldo agregado", "Aggregate support", "汇总凭证"),
        "REVIEW_REQUIRED": text("Revisar", "Review", "复核"),
        "PENDING_REVIEW": text("Sin respaldo", "No support", "无凭证"),
    }
    return labels.get(value, value)


def _name_key(value: object) -> str:
    normalized = unicodedata.normalize("NFKD", str(value or "").upper())
    normalized = "".join(char for char in normalized if not unicodedata.combining(char))
    return " ".join(re.sub(r"[^A-Z0-9]+", " ", normalized).split())


def short_supplier(value: object) -> str:
    original = str(value or text("Sin proveedor", "No supplier", "无供应商")).strip()
    key = _name_key(original)
    known = (
        ("CHINA RAILWAY", "CRCCI"),
        ("MINISTERIO DE OBRAS PUBLICAS", "MOP"),
        ("SOCIEDAD CONCESIONARIA RUTA 5 TALCA CHILLAN", text("RR.HH. Survías", "Survías HR", "Survías 人力")),
        ("INGENIERIA GESTION Y CONTROL", "IGYC"),
        ("KAPSCH TRAFFICCOM", "Kapsch"),
        ("ALPHA INGENIEROS", "Alpha"),
        ("FID CHILE", "FID Seguros"),
        ("HILDEBRANDT", "Hildebrandt"),
        ("INGELOG", "Ingelog"),
        ("CONSULTORA DANIEL MAURICIO ULLOA", "Consultora D. Ulloa"),
        ("SOLKOM INGENIERIA", "Solkom"),
        ("CONSULTORES EN ADMINISTRACION DE PAVIMENTOS", "APSA"),
        ("IDOM CONSULTING", "IDOM"),
        ("O P H INGENIEROS", "OPH Ingenieros"),
        ("INGENIERIA CALCULO Y CONSTRUCCION JLS", "JLS Ingeniería"),
        ("LEN Y ASOCIADOS", "LEN Asociados"),
        ("CHILENA CONSOLIDADA", "Chilena Consolidada"),
        ("SOUTHBRIDGE", "Southbridge"),
        ("CHUBB", "Chubb"),
        ("CGE", "CGE"),
    )
    for fragment, alias in known:
        if fragment in key:
            return alias

    shortened = re.sub(
        r"\b(SOCIEDAD ANONIMA|EMPRESA INDIVIDUAL DE RESPONSABILIDAD LIMITADA|"
        r"LIMITADA|LTDA|SPA|S A|EIRL)\b\.?,?",
        "",
        original,
        flags=re.IGNORECASE,
    )
    shortened = " ".join(shortened.split()).strip(" ,.-")
    return shortened if len(shortened) <= 27 else f"{shortened[:26].rstrip()}…"


def _date_or_none(value: str | None) -> date | None:
    return date.fromisoformat(value) if value else None


def _number(value: float, decimals: int = 0) -> str:
    formatted = f"{float(value):,.{decimals}f}"
    return formatted.replace(",", "_").replace(".", ",").replace("_", ".")


def _amount(value: float) -> str:
    if current_currency() == "CLP":
        return f"MM$ {_number(float(value) / 1_000_000, 0)}"
    numeric = float(value)
    return (
        f"{_number(numeric / 1_000_000, 2)} MM UF"
        if abs(numeric) >= 1_000_000
        else f"{_number(numeric / 1_000, 1)} mil UF"
    )


def _full_clp(value: float) -> str:
    return f"$ {_number(value, 0)}"


def _full_uf(value: float) -> str:
    return f"{_number(value, 2)} UF"


def _percent(value: float) -> str:
    return f"{_number(value, 1)}%"


def _scale() -> tuple[float, str]:
    return (1_000_000, "MM$") if current_currency() == "CLP" else (
        1_000,
        text("miles UF", "thousand UF", "千 UF"),
    )


def _polish(figure: go.Figure, height: int = 365, legend: bool = True) -> go.Figure:
    figure = style_chart(figure, height=height, horizontal_legend=legend)
    figure.update_layout(
        separators=",.",
        hovermode="closest",
        font={"family": "Arial, sans-serif", "size": 12, "color": "#3E4644"},
        title_font={"size": 16, "color": INK},
        plot_bgcolor="#FFFFFF",
        paper_bgcolor="#FFFFFF",
    )
    return figure


def _detail_view(frame: pd.DataFrame, include_observations: bool = True) -> pd.DataFrame:
    display = frame.copy()
    display["Proveedor"] = display["supplier_name_reported"].map(short_supplier)
    display["RUT"] = display["linked_rut"].fillna(display["matched_rut"])
    display["Estado"] = display["reconciliation_status"].map(reconciliation_label)
    display["Respaldo"] = display["support_type"].map(support_label)
    display["IVA recuperable CLP"] = display["recoverable_vat_clp"]
    display["IVA recuperable UF"] = display["recoverable_vat_uf"]
    columns = [
        "report_no",
        "issue_date",
        "Proveedor",
        "RUT",
        "invoice_number_reported",
        "description",
        "net_amount_clp",
        "vat_amount_clp",
        "IVA recuperable CLP",
        "net_amount_uf",
        "vat_amount_uf",
        "IVA recuperable UF",
        "Estado",
        "Respaldo",
    ]
    if include_observations:
        columns.extend(["if_observation_raw", "survias_response_raw"])
    return display[columns].rename(
        columns={
            "report_no": text("Informe", "Report", "报告"),
            "issue_date": text("Fecha", "Date", "日期"),
            "invoice_number_reported": text("Folio", "Number", "编号"),
            "description": text("Partida", "Cost item", "成本项目"),
            "net_amount_clp": text("Neto CLP", "Net CLP", "净额 CLP"),
            "vat_amount_clp": text("IVA CLP", "VAT CLP", "增值税 CLP"),
            "net_amount_uf": text("Neto UF", "Net UF", "净额 UF"),
            "vat_amount_uf": text("IVA UF", "VAT UF", "增值税 UF"),
            "if_observation_raw": text("Observación IF", "IF observation", "IF 意见"),
            "survias_response_raw": text("Respuesta Survías", "Survías response", "Survías 回复"),
        }
    )


def _table_style(frame: pd.DataFrame, highlight: bool = False):
    red_states = {reconciliation_label("REVIEW_REQUIRED"), reconciliation_label("PENDING_REVIEW")}
    amber_states = {reconciliation_label("MATCHED_PARTIAL"), reconciliation_label("AGGREGATE_SUPPORT")}

    def row_style(row: pd.Series) -> list[str]:
        if not highlight or "Estado" not in row:
            color = ""
        elif row["Estado"] in red_states:
            color = "background-color: #F8E8E5; color: #712C24"
        elif row["Estado"] in amber_states:
            color = "background-color: #F6F0E5; color: #624A23"
        else:
            color = ""
        return [color] * len(row)

    formats = {
        text("Neto CLP", "Net CLP", "净额 CLP"): _full_clp,
        text("IVA CLP", "VAT CLP", "增值税 CLP"): _full_clp,
        "IVA recuperable CLP": _full_clp,
        text("Neto UF", "Net UF", "净额 UF"): _full_uf,
        text("IVA UF", "VAT UF", "增值税 UF"): _full_uf,
        "IVA recuperable UF": _full_uf,
    }
    return frame.style.apply(row_style, axis=1).format(formats, na_rep="")


st.markdown(
    """
    <style>
    .stMainBlockContainer { max-width: 1480px; padding-top: 1.8rem; }
    .cc-title-rule { width: 38px; height: 3px; background: #C74634; margin: .15rem 0 .7rem; }
    .cc-meta { color: #707876; font-size: .78rem; margin-bottom: .8rem; }
    .cc-kpis {
        display: grid; grid-template-columns: repeat(4, minmax(0, 1fr));
        border-top: 1px solid #D9DEDC; border-bottom: 1px solid #D9DEDC;
        margin: .3rem 0 1rem;
    }
    .cc-kpi { padding: .9rem 1rem .95rem 0; min-width: 0; }
    .cc-kpi + .cc-kpi { border-left: 1px solid #E2E6E4; padding-left: 1rem; }
    .cc-kpi span { display: block; color: #727A78; font-size: .7rem; text-transform: uppercase; }
    .cc-kpi strong { display: block; color: #202625; font-size: 1.42rem; line-height: 1.25; margin-top: .2rem; }
    .cc-kpi small { display: block; color: #7A8280; font-size: .72rem; margin-top: .18rem; }
    .cc-filter-heading {
        color: #202625; font-size: .92rem; font-weight: 650;
        margin: .15rem 0 .15rem;
    }
    .cc-filter-summary { color: #707876; font-size: .76rem; margin: -.15rem 0 .25rem; }
    div[data-testid="stPlotlyChart"] { border-top: 1px solid #E3E7E5; padding-top: .1rem; }
    @media (max-width: 850px) {
        .cc-kpis { grid-template-columns: repeat(2, minmax(0, 1fr)); }
        .cc-kpi:nth-child(3) { border-left: 0; }
    }
    </style>
    """,
    unsafe_allow_html=True,
)

page_heading(text("Costos de construcción", "Construction costs", "施工成本"))
st.markdown('<div class="cc-title-rule"></div>', unsafe_allow_html=True)

currency = current_currency()
suffix = currency.lower()
net_col = f"net_amount_{suffix}"
vat_col = f"vat_amount_{suffix}"
recoverable_col = f"recoverable_vat_{suffix}"
divisor, scale_label = _scale()

try:
    active_import = get_active_construction_import()
    options = get_construction_filter_options()
    overview_items = get_construction_items(ConstructionFilters())
except Exception as exc:
    show_database_error(exc)
    st.stop()

if active_import is None:
    st.warning(text("Sin informes cargados", "No reports loaded", "未加载报告"))
    st.stop()

if overview_items.empty:
    st.info(text("Sin resultados", "No results", "无结果"))
    st.stop()

net_amount = float(overview_items[net_col].sum())
vat_amount = float(overview_items[vat_col].sum())
recoverable_vat = float(overview_items[recoverable_col].sum())
vat_gap = max(vat_amount - recoverable_vat, 0)
vat_rate = 100 * recoverable_vat / vat_amount if vat_amount else 0
report_count = int(overview_items["report_no"].nunique())

st.markdown(
    f"""
    <div class="cc-meta">
        {report_count} {text('informes', 'reports', '份报告')} ·
        {len(overview_items)} {text('partidas', 'items', '项')} ·
        {overview_items['supplier_name_reported'].nunique()} {text('proveedores', 'suppliers', '家供应商')}
    </div>
    <div class="cc-kpis">
        <div class="cc-kpi"><span>{text('Costo neto', 'Net cost', '净成本')}</span><strong>{_amount(net_amount)}</strong></div>
        <div class="cc-kpi"><span>{text('IVA presentado', 'Submitted VAT', '申报增值税')}</span><strong>{_amount(vat_amount)}</strong></div>
        <div class="cc-kpi"><span>{text('IVA recuperable', 'Recoverable VAT', '可抵扣增值税')}</span><strong>{_amount(recoverable_vat)}</strong><small>{_percent(vat_rate)}</small></div>
        <div class="cc-kpi"><span>{text('IVA por aclarar', 'VAT to clarify', '待核增值税')}</span><strong>{_amount(vat_gap)}</strong></div>
    </div>
    <div class="cc-filter-heading">{text('Filtros del detalle', 'Detail filters', '明细筛选')}</div>
    """,
    unsafe_allow_html=True,
)

filter_row = st.columns([1, 1.8, 1.25])
selected_reports = filter_row[0].multiselect(
    text("Informe", "Report", "报告"),
    options["reports"],
    placeholder=all_label(),
    format_func=lambda value: f"{text('Informe', 'Report', '报告')} {value}",
)
selected_suppliers = filter_row[1].multiselect(
    text("Proveedor", "Supplier", "供应商"),
    options["suppliers"],
    placeholder=all_label(),
)
selected_statuses = filter_row[2].multiselect(
    text("Estado", "Status", "状态"),
    options["reconciliation_statuses"],
    placeholder=all_label(),
    format_func=reconciliation_label,
)

with st.expander(text("Más filtros", "More filters", "更多筛选")):
    more = st.columns([1, 1, 1, 1.5])
    start_date = more[0].date_input(
        text("Desde", "From", "起始日期"),
        value=_date_or_none(options["min_date"]),
        format="DD/MM/YYYY",
    )
    end_date = more[1].date_input(
        text("Hasta", "To", "结束日期"),
        value=_date_or_none(options["max_date"]),
        format="DD/MM/YYYY",
    )
    folio = more[2].text_input(text("Folio", "Number", "编号"))
    search_text = more[3].text_input(text("Partida", "Cost item", "成本项目"))
    observation_classes = st.multiselect(
        text("Estado IF", "IF status", "IF 状态"),
        options["observation_classes"],
        placeholder=all_label(),
        format_func=observation_label,
    )

filters = ConstructionFilters(
    reports=tuple(selected_reports),
    start_date=start_date,
    end_date=end_date,
    suppliers=tuple(selected_suppliers),
    folio=folio,
    observation_classes=tuple(observation_classes),
    reconciliation_statuses=tuple(selected_statuses),
    search_text=search_text,
)

try:
    items = get_construction_items(filters)
except Exception as exc:
    show_database_error(exc)
    st.stop()

if items.empty:
    st.info(text("Sin resultados", "No results", "无结果"))
    st.stop()

st.markdown(
    f"""
    <div class="cc-filter-summary">
        {items['report_no'].nunique()} {text('informes', 'reports', '份报告')} ·
        {len(items)} {text('partidas', 'items', '项')} ·
        {items['supplier_name_reported'].nunique()} {text('proveedores', 'suppliers', '家供应商')}
    </div>
    """,
    unsafe_allow_html=True,
)

executive_tab, reports_tab, observations_tab, audit_tab, base_tab = st.tabs(
    [
        text("Ejecutivo", "Executive", "管理"),
        text("Informes", "Reports", "报告"),
        text("Revisión IF", "IF review", "IF 审查"),
        text("Auditoría", "Audit", "审计"),
        text("Base", "Database", "数据表"),
    ]
)

report_finance = (
    items.groupby("report_no", as_index=False)
    .agg(
        items=("construction_item_id", "count"),
        net=(net_col, "sum"),
        vat=(vat_col, "sum"),
        recoverable=(recoverable_col, "sum"),
    )
    .sort_values("report_no")
)
report_finance["vat_gap"] = (report_finance["vat"] - report_finance["recoverable"]).clip(lower=0)
observed_by_report = (
    items.assign(
        observed_amount=items[net_col].where(items["if_observation_class"] == "OBSERVED", 0)
    )
    .groupby("report_no", as_index=False)["observed_amount"]
    .sum()
)
report_finance = report_finance.merge(observed_by_report, on="report_no", how="left")
report_finance["observed_pct"] = (
    100 * report_finance["observed_amount"] / report_finance["net"].replace(0, pd.NA)
).fillna(0)

with executive_tab:
    first_row = st.columns([1.35, 1])
    cost_chart = make_subplots(specs=[[{"secondary_y": True}]])
    cost_chart.add_trace(
        go.Bar(
            x=report_finance["report_no"],
            y=report_finance["net"] / divisor,
            name=text("Costo neto", "Net cost", "净成本"),
            marker={"color": GRAPHITE},
            customdata=[[_amount(value)] for value in report_finance["net"]],
            hovertemplate=(
                f"{text('Informe', 'Report', '报告')} %{{x}}<br>"
                "%{customdata[0]}<extra></extra>"
            ),
        ),
        secondary_y=False,
    )
    cost_chart.add_trace(
        go.Scatter(
            x=report_finance["report_no"],
            y=report_finance["observed_pct"],
            name=text("Costo observado IF", "IF-observed cost", "IF 有意见成本"),
            mode="lines+markers",
            line={"color": RED, "width": 2.4},
            marker={"color": RED, "size": 6},
            hovertemplate=f"%{{y:.1f}}% {text('del costo', 'of cost', '成本')}<extra></extra>",
        ),
        secondary_y=True,
    )
    cost_chart.update_yaxes(title_text=scale_label, secondary_y=False)
    cost_chart.update_yaxes(title_text="%", ticksuffix="%", range=[0, 105], showgrid=False, secondary_y=True)
    cost_chart.update_xaxes(dtick=1, title_text=text("Informe", "Report", "报告"))
    cost_chart.update_layout(
        title=text("Costo informado y exposición IF", "Reported cost and IF exposure", "申报成本与 IF 风险"),
        bargap=0.3,
    )
    first_row[0].plotly_chart(_polish(cost_chart, 390), width="stretch", config=CHART_CONFIG)

    suppliers = (
        items.assign(Proveedor=items["supplier_name_reported"].map(short_supplier))
        .groupby("Proveedor", as_index=False)
        .agg(amount=(net_col, "sum"), items=("construction_item_id", "count"))
        .nlargest(9, "amount")
        .sort_values("amount")
    )
    supplier_colors = [GRAPHITE] * len(suppliers)
    if supplier_colors:
        supplier_colors[-1] = RED
    supplier_chart = go.Figure(
        go.Bar(
            x=suppliers["amount"] / divisor,
            y=suppliers["Proveedor"],
            orientation="h",
            marker={"color": supplier_colors},
            customdata=[[_amount(value), count] for value, count in zip(suppliers["amount"], suppliers["items"])],
            hovertemplate=(
                "%{y}<br>%{customdata[0]}<br>%{customdata[1]} "
                f"{text('partidas', 'items', '项')}<extra></extra>"
            ),
        )
    )
    supplier_chart.update_layout(
        title=text("Principales exposiciones", "Largest exposures", "主要成本敞口"),
        showlegend=False,
    )
    supplier_chart.update_xaxes(title_text=scale_label)
    first_row[1].plotly_chart(_polish(supplier_chart, 390, False), width="stretch", config=CHART_CONFIG)

    second_row = st.columns([1.4, 1])
    vat_chart = make_subplots(specs=[[{"secondary_y": True}]])
    vat_chart.add_trace(
        go.Bar(
            x=report_finance["report_no"],
            y=report_finance["recoverable"] / divisor,
            name=text("IVA recuperable", "Recoverable VAT", "可抵扣增值税"),
            marker={"color": GRAPHITE},
            customdata=[[_amount(value)] for value in report_finance["recoverable"]],
            hovertemplate=(
                f"{text('Informe', 'Report', '报告')} %{{x}}<br>"
                "%{customdata[0]}<extra></extra>"
            ),
        ),
        secondary_y=False,
    )
    vat_chart.add_trace(
        go.Scatter(
            x=report_finance["report_no"],
            y=report_finance["vat_gap"] / divisor,
            name=text("IVA por aclarar", "VAT to clarify", "待核增值税"),
            mode="lines+markers",
            line={"color": RED, "width": 2.2},
            marker={"color": RED, "size": 7},
            customdata=[[_amount(value)] for value in report_finance["vat_gap"]],
            hovertemplate="%{customdata[0]}<extra></extra>",
        ),
        secondary_y=True,
    )
    vat_chart.update_yaxes(title_text=scale_label, secondary_y=False)
    vat_chart.update_yaxes(title_text=scale_label, showgrid=False, secondary_y=True)
    vat_chart.update_xaxes(dtick=1, title_text=text("Informe", "Report", "报告"))
    vat_chart.update_layout(
        title=text("IVA recuperable y diferencias", "Recoverable VAT and differences", "可抵扣增值税与差异"),
        bargap=0.3,
    )
    second_row[0].plotly_chart(_polish(vat_chart, 330), width="stretch", config=CHART_CONFIG)

    if_mix = (
        items.groupby("if_observation_class", as_index=False)
        .agg(items=("construction_item_id", "count"))
    )
    if_mix["label"] = if_mix["if_observation_class"].map(observation_label)
    if_mix["share"] = 100 * if_mix["items"] / if_mix["items"].sum()
    if_colors = {
        observation_label("APPROVED_EXPLICIT"): INK,
        observation_label("NO_OBSERVATION"): STEEL,
        observation_label("OBSERVED"): RED,
    }
    if_chart = px.bar(
        if_mix,
        x="share",
        y=[text("Partidas", "Items", "项目")] * len(if_mix),
        color="label",
        orientation="h",
        text=if_mix["share"].map(_percent),
        custom_data=["items"],
        color_discrete_map=if_colors,
    )
    if_chart.update_traces(
        textposition="inside",
        hovertemplate=(
            "%{fullData.name}<br>%{customdata[0]} "
            f"{text('partidas', 'items', '项')}<extra></extra>"
        ),
    )
    if_chart.update_layout(
        title=text("Estado de revisión IF", "IF review status", "IF 审查状态"),
        barmode="stack",
    )
    if_chart.update_xaxes(range=[0, 100], ticksuffix="%", title_text="")
    if_chart.update_yaxes(title_text="")
    second_row[1].plotly_chart(_polish(if_chart, 330), width="stretch", config=CHART_CONFIG)

with reports_tab:
    report_table = pd.DataFrame(
        {
            text("Informe", "Report", "报告"): report_finance["report_no"].astype(int),
            text("Partidas", "Items", "项目"): report_finance["items"].astype(int),
            text(f"Neto ({scale_label})", f"Net ({scale_label})", f"净额 ({scale_label})"): report_finance["net"] / divisor,
            text(f"IVA ({scale_label})", f"VAT ({scale_label})", f"增值税 ({scale_label})"): report_finance["vat"] / divisor,
            text(f"IVA recuperable ({scale_label})", f"Recoverable VAT ({scale_label})", f"可抵扣增值税 ({scale_label})"): report_finance["recoverable"] / divisor,
            text(f"Por aclarar ({scale_label})", f"To clarify ({scale_label})", f"待核 ({scale_label})"): report_finance["vat_gap"] / divisor,
            text("Costo observado", "Observed cost", "有意见成本"): report_finance["observed_pct"],
        }
    )
    amount_columns = report_table.columns[2:6]
    report_style = report_table.style.format(
        {column: lambda value: _number(value, 1) for column in amount_columns}
        | {report_table.columns[-1]: _percent}
    ).set_properties(subset=[report_table.columns[-1]], **{"font-weight": "600"})
    st.dataframe(report_style, hide_index=True, width="stretch", height=520)

with observations_tab:
    observed = items[items["if_observation_class"] == "OBSERVED"].copy()
    observed["Proveedor"] = observed["supplier_name_reported"].map(short_supplier)
    observation_table = observed[
        [
            "report_no",
            "issue_date",
            "Proveedor",
            "invoice_number_reported",
            "description",
            net_col,
            "if_observation_raw",
            "survias_response_raw",
        ]
    ].rename(
        columns={
            "report_no": text("Informe", "Report", "报告"),
            "issue_date": text("Fecha", "Date", "日期"),
            "invoice_number_reported": text("Folio", "Number", "编号"),
            "description": text("Partida", "Cost item", "成本项目"),
            net_col: text(f"Neto ({scale_label})", f"Net ({scale_label})", f"净额 ({scale_label})"),
            "if_observation_raw": text("Observación IF", "IF observation", "IF 意见"),
            "survias_response_raw": text("Respuesta Survías", "Survías response", "Survías 回复"),
        }
    )
    observation_table[observation_table.columns[5]] = observed[net_col].values / divisor
    st.dataframe(
        observation_table.style.format({observation_table.columns[5]: lambda value: _number(value, 1)}),
        hide_index=True,
        width="stretch",
        height=565,
    )

with audit_tab:
    audit_items = items[
        items["reconciliation_status"].isin(
            ["REVIEW_REQUIRED", "PENDING_REVIEW", "AGGREGATE_SUPPORT", "MATCHED_PARTIAL"]
        )
    ]
    audit_counts = items.groupby("reconciliation_status").size()
    audit_metrics = st.columns(4)
    audit_metrics[0].metric(text("Parciales", "Partial", "部分"), int(audit_counts.get("MATCHED_PARTIAL", 0)))
    audit_metrics[1].metric(text("Agregados", "Aggregate", "汇总"), int(audit_counts.get("AGGREGATE_SUPPORT", 0)))
    audit_metrics[2].metric(text("Revisar", "Review", "复核"), int(audit_counts.get("REVIEW_REQUIRED", 0)))
    audit_metrics[3].metric(text("Sin respaldo", "No support", "无凭证"), int(audit_counts.get("PENDING_REVIEW", 0)))
    st.dataframe(
        _table_style(_detail_view(audit_items, include_observations=False), highlight=True),
        hide_index=True,
        width="stretch",
        height=535,
    )

with base_tab:
    detail = _detail_view(items)
    st.dataframe(_table_style(detail), hide_index=True, width="stretch", height=590)
    st.download_button(
        text("Descargar base auditada", "Download audited data", "下载审计数据"),
        data=dataframe_to_csv_bytes(items),
        file_name="cc_lab_construccion_auditoria.csv",
        mime="text/csv",
        icon=":material/download:",
    )
