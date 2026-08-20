from __future__ import annotations

from datetime import date

import pandas as pd
import streamlit as st

from utils.catalogs import COST_CATEGORIES, document_type_label
from utils.i18n import all_label, current_currency, payment_status_label, text
from utils.legacy_data import filter_cost_control, load_cost_control, summarize_cost_centers
from utils.queries import (
    DocumentFilters,
    get_document_filter_options,
    get_document_issues,
    get_document_metrics,
    get_documents,
    get_raw_appearances,
)
from utils.ui_helpers import (
    dataframe_to_csv_bytes,
    format_clp,
    format_clp_compact,
    format_uf,
    page_heading,
    show_database_error,
    show_historical_data_error,
)


def _cost_columns(frame: pd.DataFrame) -> pd.DataFrame:
    currency = current_currency()
    money_columns = (
        ["NET-UF-F", "TOTAL-UF-F"]
        if currency == "UF"
        else ["NET-CLP-F", "VAT-CLP-F", "TOTAL-CLP-F"]
    )
    columns = [
        "SOURCE_KIND",
        "RUT_COMPLETO",
        "SUPPLIER-F",
        "DOCUMENT TYPE",
        "INVOICE-F",
        "DATE-F",
        "CATEGORY-F",
        "CATEGORY_NAME",
        "SUBCATEGORY-F",
        "SUBCATEGORY_NAME",
        "ALLOCATION_SOURCE",
        *money_columns,
        "PAYMENT_STATUS",
        "COST_TREATMENT",
        "REVIEW_REASON",
    ]
    display = frame[[column for column in columns if column in frame.columns]].copy()
    display["PAYMENT_STATUS"] = display["PAYMENT_STATUS"].map(payment_status_label)
    return display.rename(
        columns={
            "SOURCE_KIND": text("Origen", "Source", "来源"),
            "RUT_COMPLETO": "RUT",
            "SUPPLIER-F": text("Proveedor", "Supplier", "供应商"),
            "DOCUMENT TYPE": text("Tipo", "Type", "类型"),
            "INVOICE-F": text("Folio", "Number", "编号"),
            "DATE-F": text("Fecha", "Date", "日期"),
            "CATEGORY-F": text("Grupo", "Group", "组别"),
            "CATEGORY_NAME": text("Nombre grupo", "Group name", "组别名称"),
            "SUBCATEGORY-F": text("Centro", "Center", "中心"),
            "SUBCATEGORY_NAME": text("Centro de costo", "Cost center", "成本中心"),
            "ALLOCATION_SOURCE": text("Fuente CC", "CC source", "成本中心来源"),
            "NET-CLP-F": text("Neto CLP", "Net CLP", "净额 CLP"),
            "VAT-CLP-F": text("IVA CLP", "VAT CLP", "增值税 CLP"),
            "TOTAL-CLP-F": text("Total CLP", "Total CLP", "总额 CLP"),
            "NET-UF-F": text("Neto UF", "Net UF", "净额 UF"),
            "TOTAL-UF-F": text("Total UF", "Total UF", "总额 UF"),
            "PAYMENT_STATUS": text("Estado", "Status", "状态"),
            "COST_TREATMENT": text("Tratamiento", "Treatment", "处理"),
            "REVIEW_REASON": text("Pendiente", "Pending reason", "待处理原因"),
        }
    )


def _date_or_none(value: str | None) -> date | None:
    return date.fromisoformat(value) if value else None


def _center_table(frame: pd.DataFrame) -> None:
    currency = current_currency()
    suffix = "UF" if currency == "UF" else "CLP"
    summary = summarize_cost_centers(frame)[
        [
            "CATEGORY-F", "CATEGORY_NAME", "SUBCATEGORY-F", "SUBCATEGORY_NAME", "DOCUMENTOS",
            f"COSTO_NETO_{suffix}", f"COSTO_TOTAL_{suffix}", f"PAGADO_{suffix}",
            f"NO_PAGADO_{suffix}", f"REVISAR_{suffix}",
        ]
    ]
    number_format = "%.0f UF" if currency == "UF" else "$ %.0f"
    st.dataframe(
        summary,
        hide_index=True,
        width="stretch",
        height=500,
        column_config={
            "CATEGORY-F": text("Grupo", "Group", "组别"),
            "CATEGORY_NAME": text("Nombre", "Name", "名称"),
            "SUBCATEGORY-F": text("Centro", "Center", "中心"),
            "SUBCATEGORY_NAME": text("Centro de costo", "Cost center", "成本中心"),
            "DOCUMENTOS": text("Docs.", "Docs", "文档"),
            f"COSTO_NETO_{suffix}": st.column_config.NumberColumn(text("Neto", "Net", "净额"), format=number_format),
            f"COSTO_TOTAL_{suffix}": st.column_config.NumberColumn(text("Total", "Total", "总额"), format=number_format),
            f"PAGADO_{suffix}": st.column_config.NumberColumn(text("Pagado", "Paid", "已支付"), format=number_format),
            f"NO_PAGADO_{suffix}": st.column_config.NumberColumn(text("No pagado", "Unpaid", "未支付"), format=number_format),
            f"REVISAR_{suffix}": st.column_config.NumberColumn(text("Revisar", "Review", "复核"), format=number_format),
        },
    )


page_heading(text("Costos", "Costs", "成本"))
currency = current_currency()
amount_column = "NET-UF-F" if currency == "UF" else "NET-CLP-F"
format_amount = format_uf if currency == "UF" else format_clp_compact

try:
    cost_control = load_cost_control()
except Exception as exc:
    show_historical_data_error(exc)
    st.stop()

control_tab, database_tab, sii_tab = st.tabs(
    [
        text("Control", "Control", "成本控制"),
        text("Base maestra", "Master ledger", "主数据"),
        text("RCV SII", "SII purchase ledger", "SII 购买台账"),
    ]
)

with control_tab:
    min_date = cost_control["DATE-F"].min().date()
    max_date = cost_control["DATE-F"].max().date()
    with st.expander(text("Filtros", "Filters", "筛选"), expanded=True):
        filter_row = st.columns([1, 1, 1.1, 1.3])
        selected_categories = filter_row[0].multiselect(
            text("Grupos", "Groups", "组别"),
            sorted(cost_control["CATEGORY-F"].dropna().astype(int).unique()),
            format_func=lambda code: f"{code} · {COST_CATEGORIES.get(code, '')}",
            placeholder=all_label(),
        )
        available_subcategories = cost_control
        if selected_categories:
            available_subcategories = available_subcategories[
                available_subcategories["CATEGORY-F"].isin(selected_categories)
            ]
        subcategory_lookup = (
            available_subcategories[["SUBCATEGORY-F", "SUBCATEGORY_NAME"]]
            .dropna()
            .drop_duplicates()
            .sort_values("SUBCATEGORY-F")
        )
        subcategory_names = dict(
            zip(subcategory_lookup["SUBCATEGORY-F"].astype(int), subcategory_lookup["SUBCATEGORY_NAME"])
        )
        selected_subcategories = filter_row[1].multiselect(
            text("Centros", "Cost centers", "成本中心"),
            list(subcategory_names),
            format_func=lambda code: f"{code} · {subcategory_names[code]}",
            placeholder=all_label(),
        )
        supplier_source = available_subcategories
        if selected_subcategories:
            supplier_source = supplier_source[supplier_source["SUBCATEGORY-F"].isin(selected_subcategories)]
        selected_suppliers = filter_row[2].multiselect(
            text("Proveedores", "Suppliers", "供应商"),
            sorted(supplier_source["SUPPLIER-F"].dropna().unique()),
            placeholder=all_label(),
        )
        with filter_row[3]:
            apply_date_filter = st.toggle(text("Fecha", "Date", "日期"))
            selected_range = st.date_input(
                text("Período", "Period", "期间"),
                value=(min_date, max_date),
                min_value=min_date,
                max_value=max_date,
                format="DD/MM/YYYY",
                disabled=not apply_date_filter,
                label_visibility="collapsed",
            )

    start_date, end_date = (
        selected_range if apply_date_filter and len(selected_range) == 2 else (None, None)
    )
    filtered_costs = filter_cost_control(
        cost_control,
        start_date=start_date,
        end_date=end_date,
        categories=selected_categories,
        subcategories=selected_subcategories,
        suppliers=selected_suppliers,
    )
    confirmed_costs = filtered_costs[filtered_costs["INCLUDED_IN_COST"]]
    pending_count = int((filtered_costs["REVIEW_REASON"] != "").sum())

    metrics = st.columns(4)
    metrics[0].metric(text("Registros confirmados", "Confirmed records", "已确认记录"), f"{len(confirmed_costs):,}".replace(",", "."))
    metrics[1].metric(text(f"Costo {currency}", f"Cost {currency}", f"成本 {currency}"), format_amount(confirmed_costs[amount_column].sum()))
    metrics[2].metric(text("Proveedores", "Suppliers", "供应商"), confirmed_costs["RUT_COMPLETO"].nunique())
    metrics[3].metric(text("Pendientes", "Pending", "待处理"), f"{pending_count:,}".replace(",", "."))

    view_labels = {
        "centers": text("Centros", "Cost centers", "成本中心"),
        "providers": text("Proveedores", "Suppliers", "供应商"),
        "documents": text("Documentos", "Documents", "文档"),
    }
    view_mode = st.segmented_control(
        text("Vista", "View", "视图"),
        list(view_labels),
        format_func=view_labels.get,
        default="centers",
    )
    if view_mode == "centers":
        _center_table(confirmed_costs)
    elif view_mode == "providers":
        provider_summary = (
            confirmed_costs.groupby(["RUT_COMPLETO", "SUPPLIER-F"], dropna=False)
            .agg(
                DOCUMENTOS=("INVOICE-F", "count"),
                NETO=(amount_column, "sum"),
                TOTAL=("TOTAL-UF-F" if currency == "UF" else "TOTAL-CLP-F", "sum"),
            )
            .reset_index()
            .sort_values("NETO", ascending=False)
        )
        st.dataframe(
            provider_summary,
            hide_index=True,
            width="stretch",
            height=500,
            column_config={
                "RUT_COMPLETO": "RUT",
                "SUPPLIER-F": text("Proveedor", "Supplier", "供应商"),
                "DOCUMENTOS": text("Docs.", "Docs", "文档"),
                "NETO": st.column_config.NumberColumn(text(f"Neto {currency}", f"Net {currency}", f"净额 {currency}"), format="%.0f UF" if currency == "UF" else "$ %.0f"),
                "TOTAL": st.column_config.NumberColumn(text(f"Total {currency}", f"Total {currency}", f"总额 {currency}"), format="%.0f UF" if currency == "UF" else "$ %.0f"),
            },
        )
    else:
        st.dataframe(_cost_columns(confirmed_costs), hide_index=True, width="stretch", height=500)

    st.download_button(
        text("Descargar", "Download", "下载"),
        data=dataframe_to_csv_bytes(_cost_columns(confirmed_costs)),
        file_name="cc_lab_costos_confirmados.csv",
        mime="text/csv",
        icon=":material/download:",
    )

with database_tab:
    load_history = st.toggle(
        text("Mostrar base maestra", "Show master ledger", "显示主数据"),
        help=text("SII y H-P conciliados desde SQLite", "SII and H-P reconciled from SQLite", "来自 SQLite 的 SII 与 H-P 对账"),
    )
    if load_history:
        historical_display = _cost_columns(cost_control)
        st.dataframe(historical_display, hide_index=True, width="stretch", height=610)
        st.download_button(
            text("Descargar", "Download", "下载"),
            data=dataframe_to_csv_bytes(historical_display),
            file_name="cc_lab_base_maestra.csv",
            mime="text/csv",
            icon=":material/download:",
        )
    else:
        st.caption(
            text(
                f"{len(cost_control):,} registros disponibles en SQLite",
                f"{len(cost_control):,} records available in SQLite",
                f"SQLite 中有 {len(cost_control):,} 条记录",
            ).replace(",", ".")
        )

with sii_tab:
    load_sii = st.toggle(text("Consultar RCV", "Query ledger", "查询台账"))
    if not load_sii:
        sii_count = int(
            cost_control.loc[cost_control["SOURCE_KIND"] == "SII", "DOCUMENT_ID"].nunique()
        )
        st.caption(
            text(
                f"{sii_count:,} documentos SII",
                f"{sii_count:,} SII documents",
                f"{sii_count:,} 份 SII 文档",
            ).replace(",", ".")
        )
    else:
        try:
            options = get_document_filter_options()
            with st.expander(text("Filtros", "Filters", "筛选"), expanded=True):
                row_one = st.columns(4)
                sii_start = row_one[0].date_input(
                    text("Desde", "From", "起始日期"),
                    value=None,
                    min_value=_date_or_none(options["min_date"]),
                    max_value=_date_or_none(options["max_date"]),
                    key="sii_start",
                )
                sii_end = row_one[1].date_input(
                    text("Hasta", "To", "结束日期"),
                    value=None,
                    min_value=_date_or_none(options["min_date"]),
                    max_value=_date_or_none(options["max_date"]),
                    key="sii_end",
                )
                sii_period = row_one[2].selectbox(
                    text("Período fuente", "Source period", "数据期间"),
                    ["", *options["periods"]],
                    format_func=lambda value: value or all_label(),
                )
                sii_type = row_one[3].selectbox(
                    text("Tipo", "Document type", "文档类型"),
                    [None, *options["document_types"]],
                    format_func=lambda value: all_label() if value is None else f"{value} · {document_type_label(value)}",
                )
                row_two = st.columns(5)
                sii_supplier = row_two[0].text_input(text("Proveedor", "Supplier", "供应商"), key="sii_supplier")
                sii_rut = row_two[1].text_input("RUT", key="sii_rut")
                sii_folio = row_two[2].text_input(text("Folio", "Number", "编号"), key="sii_folio")
                sii_quality = row_two[3].selectbox(
                    text("Calidad", "Quality", "质量"),
                    ["", *options["quality_statuses"]],
                    format_func=lambda value: value or all_label(),
                )
                conflict_options = ["Todos", "Con conflicto", "Sin conflicto"]
                conflict_labels = {
                    "Todos": all_label(),
                    "Con conflicto": text("Con conflicto", "Conflict", "有冲突"),
                    "Sin conflicto": text("Sin conflicto", "No conflict", "无冲突"),
                }
                sii_conflict = row_two[4].selectbox(
                    text("Duplicidad", "Duplicates", "重复"),
                    conflict_options,
                    format_func=conflict_labels.get,
                )

            sii_filters = DocumentFilters(
                start_date=sii_start,
                end_date=sii_end,
                source_period=sii_period,
                supplier_name=sii_supplier,
                supplier_rut=sii_rut,
                document_type=sii_type,
                folio=sii_folio,
                quality_status=sii_quality,
                duplicate_conflict=sii_conflict,
            )
            sii_metrics = get_document_metrics(sii_filters)
            sii_documents = get_documents(sii_filters)
        except Exception as exc:
            show_database_error(exc)
        else:
            metric_columns = st.columns(4)
            metric_columns[0].metric(text("Documentos", "Documents", "文档"), f"{int(sii_metrics['document_count']):,}".replace(",", "."))
            metric_columns[1].metric(text("Neto", "Net", "净额"), format_clp_compact(sii_metrics["net_economic_clp"]))
            metric_columns[2].metric(text("IVA recuperable", "Recoverable VAT", "可抵扣增值税"), format_clp_compact(sii_metrics["recoverable_vat_economic_clp"]))
            metric_columns[3].metric(text("Conflictos", "Conflicts", "冲突"), int(sii_metrics["conflict_count"]))

            if sii_documents.empty:
                st.info(text("Sin resultados", "No results", "无结果"))
            else:
                sii_columns = {
                    "issue_date": text("Fecha", "Date", "日期"),
                    "supplier_rut_full": "RUT",
                    "supplier_name": text("Proveedor", "Supplier", "供应商"),
                    "document_type": text("Tipo", "Type", "类型"),
                    "document_number": text("Folio", "Number", "编号"),
                    "net_economic_clp": text("Neto", "Net", "净额"),
                    "recoverable_vat_economic_clp": text("IVA", "VAT", "增值税"),
                    "total_economic_clp": text("Total", "Total", "总额"),
                    "source_period": text("Período", "Period", "期间"),
                    "quality_status": text("Calidad", "Quality", "质量"),
                    "duplicate_conflict": text("Duplicidad", "Duplicates", "重复"),
                }
                sii_visible = sii_documents[[*sii_columns]].rename(columns=sii_columns)
                selection = st.dataframe(
                    sii_visible,
                    hide_index=True,
                    width="stretch",
                    height=500,
                    on_select="rerun",
                    selection_mode="single-row",
                )
                st.download_button(
                    text("Descargar", "Download", "下载"),
                    data=dataframe_to_csv_bytes(sii_visible),
                    file_name="cc_lab_rcv_sii.csv",
                    mime="text/csv",
                    icon=":material/download:",
                )

                selected_rows = selection.selection.rows if selection else []
                if selected_rows:
                    selected = sii_documents.iloc[selected_rows[0]]
                    st.subheader(f"{selected['supplier_name']} · {selected['document_number']}")
                    trace_metrics = st.columns(4)
                    trace_metrics[0].metric("RUT", selected["supplier_rut_full"])
                    trace_metrics[1].metric(text("Tipo", "Type", "类型"), int(selected["document_type"]))
                    trace_metrics[2].metric(text("Original", "Original", "原始值"), format_clp(selected["total_original_clp"]))
                    trace_metrics[3].metric(text("Económico", "Economic", "经济值"), format_clp(selected["total_economic_clp"]))
                    issues = get_document_issues(str(selected["document_key"]))
                    raw_rows = get_raw_appearances(str(selected["document_key"]))
                    with st.expander(f"{text('Hallazgos', 'Issues', '问题')} · {len(issues)}"):
                        st.dataframe(issues, hide_index=True, width="stretch")
                    with st.expander(f"{text('Filas fuente', 'Source rows', '源数据行')} · {len(raw_rows)}"):
                        st.dataframe(raw_rows, hide_index=True, width="stretch")
