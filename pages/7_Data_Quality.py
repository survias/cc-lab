from __future__ import annotations

from datetime import date

import pandas as pd
import streamlit as st

from utils.auth import require_authentication

require_authentication()

from utils.i18n import all_label, current_currency, text
from utils.legacy_data import load_cost_control
from utils.monthly_import import import_monthly_files, preview_monthly_files
from utils.queries import QualityFilters, get_quality_issues, get_quality_options, get_quality_summary
from utils.reconciliation import (
    get_cost_centers,
    get_credit_note_candidates,
    get_credit_note_decisions,
    get_import_history,
    get_reconciliation_batches,
    reverse_reconciliation_batch,
    save_bulk_review_decisions,
    save_credit_note_decision,
    save_manual_matches_bulk,
    save_paid_confirmation,
    save_payment_confirmation,
    save_unpaid_confirmation,
)
from utils.uf_data import get_month_uf_rates, get_uf_coverage, update_uf_from_sii, years_requiring_update
from utils.ui_helpers import format_clp_compact, format_uf, page_heading, show_database_error


def _center_fields(centers: pd.DataFrame) -> tuple[int, int, str]:
    groups = sorted(centers["category_code"].astype(int).unique())
    columns = st.columns(2)
    group = columns[0].selectbox(
        text("Grupo", "Group", "组别"),
        groups,
        format_func=lambda value: (
            f"{value} · {centers.loc[centers['category_code'] == value, 'category_name'].iloc[0]}"
        ),
        key="reconcile_group",
    )
    group_centers = centers[centers["category_code"] == group]
    center_options = group_centers["subcategory_code"].astype(int).tolist()
    center = columns[1].selectbox(
        text("Centro de costo", "Cost center", "成本中心"),
        center_options,
        format_func=lambda value: (
            f"{value} · {group_centers.loc[group_centers['subcategory_code'] == value, 'subcategory_name'].iloc[0]}"
        ),
        key="reconcile_center",
    )
    note = st.text_area(text("Nota", "Note", "备注"), height=68, key="reconcile_note")
    return int(group), int(center), note


def _one_row_per_record(frame: pd.DataFrame, id_column: str) -> pd.DataFrame:
    if frame.empty:
        return frame
    return frame.sort_values(id_column).drop_duplicates(id_column, keep="first")


def _payment_suggestions(documents: pd.DataFrame, ledger: pd.DataFrame) -> pd.DataFrame:
    payments = ledger[(ledger["RECORD_TYPE"] == "PAYMENT") & ledger["PAYMENT_ID"].notna()].copy()
    suggestions: list[dict] = []
    used_payments: set[int] = set()
    for _, document in documents.iterrows():
        candidates = payments[payments["RUT_KEY"] == document["RUT_KEY"]].copy()
        if candidates.empty:
            continue
        candidates["DIFERENCIA_CLP"] = (
            candidates["TOTAL-CLP-F"].abs() - abs(document["TOTAL-CLP-F"])
        ).abs()
        candidates["MISMO_FOLIO"] = candidates["FOLIO_BASE"] == document["FOLIO_BASE"]
        candidates["FECHA_VALIDA"] = candidates["DATE-F"] >= document["DATE-F"]
        by_folio = candidates[candidates["MISMO_FOLIO"] & candidates["FECHA_VALIDA"]]
        by_amount = candidates[(candidates["DIFERENCIA_CLP"] <= 1) & candidates["FECHA_VALIDA"]]
        match = by_folio if len(by_folio) == 1 else by_amount
        if len(match) != 1:
            continue
        payment = match.iloc[0]
        payment_id = int(payment["PAYMENT_ID"])
        if payment_id in used_payments:
            continue
        used_payments.add(payment_id)
        suggestions.append(
            {
                "DOCUMENT_ID": int(document["DOCUMENT_ID"]),
                "PAYMENT_ID": payment_id,
                "Proveedor": document["SUPPLIER-F"],
                "Factura": document["INVOICE-F"],
                "Fecha pago": payment["DATE-F"],
                "Folio pago": payment["INVOICE-F"],
                "Monto pago CLP": payment["TOTAL-CLP-F"],
                "Diferencia CLP": payment["DIFERENCIA_CLP"],
                "Criterio": "RUT + folio" if bool(payment["MISMO_FOLIO"]) else "RUT + monto",
            }
        )
    return pd.DataFrame(suggestions)


page_heading(text("Calidad y conciliación", "Quality and reconciliation", "质量与对账"))
currency = current_currency()
total_column = "TOTAL-UF-F" if currency == "UF" else "TOTAL-CLP-F"
format_amount = format_uf if currency == "UF" else format_clp_compact

if "quality_message" in st.session_state:
    st.success(st.session_state.pop("quality_message"))
if "upload_nonce" not in st.session_state:
    st.session_state.upload_nonce = 0

try:
    ledger = load_cost_control()
    if "PAYMENT_REVIEW_STATUS" not in ledger.columns:
        st.cache_data.clear()
        ledger = load_cost_control()
    centers = get_cost_centers()
except Exception as exc:
    show_database_error(exc)
    st.stop()

upload_tab, reconcile_tab, credit_tab, uf_tab, history_tab, issues_tab = st.tabs(
    [
        text("Cargar período", "Upload period", "上传期间"),
        text("Conciliar", "Reconcile", "对账"),
        text("Notas de crédito", "Credit notes", "贷项通知单"),
        "UF",
        text("Historial", "History", "历史"),
        text("Hallazgos", "Issues", "问题"),
    ]
)


with upload_tab:
    today = date.today()
    period_columns = st.columns([0.7, 0.7, 2.6])
    selected_year = period_columns[0].selectbox(
        text("Año", "Year", "年份"), list(range(2021, 2036)), index=today.year - 2021
    )
    selected_month = period_columns[1].selectbox(
        text("Mes", "Month", "月份"), list(range(1, 13)), index=today.month - 1,
        format_func=lambda value: f"{value:02d}",
    )
    period = f"{selected_year}{selected_month:02d}"
    period_columns[2].caption(
        text("Archivos correspondientes al mes seleccionado.", "Files for the selected month.", "所选月份的文件。")
    )
    upload_columns = st.columns(2)
    sii_file = upload_columns[0].file_uploader(
        text("RCV SII", "SII purchase ledger", "SII 采购台账"), type=["csv", "xlsx"],
        key=f"monthly_sii_file_{st.session_state.upload_nonce}",
    )
    payment_file = upload_columns[1].file_uploader(
        text("Pagos del mes", "Monthly payments", "月度付款"), type=["csv", "xlsx"],
        key=f"monthly_payment_file_{st.session_state.upload_nonce}",
    )
    preview = None
    if sii_file is not None and payment_file is not None:
        try:
            preview = preview_monthly_files(
                period, sii_file.getvalue(), sii_file.name, payment_file.getvalue(), payment_file.name
            )
        except Exception as exc:
            st.error(str(exc))
        else:
            metrics = st.columns(4)
            metrics[0].metric("RCV", preview.sii_rows)
            metrics[1].metric(text("Documentos nuevos", "New documents", "新文档"), preview.sii_documents - preview.sii_duplicates)
            metrics[2].metric(text("Pagos válidos", "Valid payments", "有效付款"), preview.valid_payments)
            metrics[3].metric(text("Observados", "Observed", "异常"), preview.invalid_payments + preview.sii_duplicates)
    if st.button(
        text("Importar período", "Import period", "导入期间"), icon=":material/upload_file:",
        type="primary", disabled=preview is None,
    ):
        try:
            result = import_monthly_files(
                period, sii_file.getvalue(), sii_file.name, payment_file.getvalue(), payment_file.name
            )
        except Exception as exc:
            st.error(str(exc))
        else:
            st.cache_data.clear()
            st.session_state.quality_message = text(
                f"Período {period} importado: {result.new_documents} documentos y {result.valid_payments} pagos.",
                f"Period {period} imported: {result.new_documents} documents and {result.valid_payments} payments.",
                f"期间 {period} 已导入：{result.new_documents} 份文档和 {result.valid_payments} 笔付款。",
            )
            st.session_state.upload_nonce += 1
            st.rerun()


with reconcile_tab:
    source = st.segmented_control(
        text("Origen", "Source", "来源"), ["DOCUMENT", "PAYMENT"], default="DOCUMENT",
        format_func=lambda value: "SII" if value == "DOCUMENT" else text("Pagos", "Payments", "付款"),
        width="stretch",
    )
    include_reviewed = st.toggle(
        text("Incluir ya conciliados", "Include reconciled", "包括已对账记录"),
        help=text(
            "Permite revisar o corregir decisiones anteriores.",
            "Allows previous decisions to be reviewed or corrected.",
            "允许审核或更正以前的决定。",
        ),
    )
    if source == "DOCUMENT":
        work_items = ledger[
            (ledger["RECORD_TYPE"] == "DOCUMENT")
            & (ledger["DOCUMENT TYPE"] != 61)
        ].copy()
        if not include_reviewed:
            work_items = work_items[
                (work_items["REVIEW_REASON"] != "")
                | (work_items["PAYMENT_REVIEW_STATUS"] == "PENDING")
            ]
        id_column = "DOCUMENT_ID"
        work_items["ESTADO_PAGO"] = work_items["PAYMENT_REVIEW_STATUS"].map(
            {
                "PAID_LINKED": text("Pagado conciliado", "Paid and linked", "已付款并关联"),
                "PAID_CONFIRMED": text("Pagado confirmado", "Paid confirmed", "已确认付款"),
                "UNPAID_CONFIRMED": text("No pagado confirmado", "Unpaid confirmed", "已确认未付款"),
                "PENDING": text("Por revisar", "To review", "待审核"),
            }
        ).fillna(work_items["PAYMENT_STATUS"])
    else:
        work_items = ledger[ledger["RECORD_TYPE"] == "PAYMENT"].copy()
        if not include_reviewed:
            work_items = work_items[work_items["REVIEW_REASON"] != ""]
        id_column = "PAYMENT_ID"
        work_items["ESTADO_PAGO"] = text("Pago sin documento", "Payment without document", "无凭证付款")
    work_items = _one_row_per_record(work_items, id_column)
    work_items["MOTIVO"] = work_items["REVIEW_REASON"].fillna("")
    payment_pending = work_items["PAYMENT_REVIEW_STATUS"].eq("PENDING") if source == "DOCUMENT" else False
    if source == "DOCUMENT":
        work_items.loc[(work_items["MOTIVO"] == "") & payment_pending, "MOTIVO"] = text(
            "Estado de pago pendiente", "Payment status pending", "付款状态待定"
        )
        both = (work_items["MOTIVO"] != "") & payment_pending & ~work_items["MOTIVO"].eq(
            text("Estado de pago pendiente", "Payment status pending", "付款状态待定")
        )
        work_items.loc[both, "MOTIVO"] += " · " + text(
            "Pago pendiente", "Payment pending", "付款待定"
        )

    filter_columns = st.columns([2, 1, 1])
    supplier_options_frame = (
        work_items[["RUT_KEY", "RUT_COMPLETO", "SUPPLIER-F"]]
        .dropna(subset=["RUT_KEY"])
        .drop_duplicates()
        .sort_values(["SUPPLIER-F", "RUT_COMPLETO"])
        .drop_duplicates("RUT_KEY")
    )
    supplier_options = ["", *supplier_options_frame["RUT_KEY"].astype(str).tolist()]
    supplier_labels = {
        str(row.RUT_KEY): f"{row['SUPPLIER-F']} · {row.RUT_COMPLETO}"
        for _, row in supplier_options_frame.iterrows()
    }
    selected_supplier = filter_columns[0].selectbox(
        text("Proveedor o RUT", "Supplier or RUT", "供应商或 RUT"),
        supplier_options,
        format_func=lambda value: supplier_labels.get(value, all_label()),
        placeholder=text(
            "Escribe para buscar un proveedor", "Type to search for a supplier", "输入以搜索供应商"
        ),
        key=f"reconcile_supplier_{source}",
    )
    reasons = sorted(work_items["MOTIVO"].dropna().unique())
    reason = filter_columns[1].selectbox(
        text("Motivo", "Reason", "原因"), ["", *reasons], format_func=lambda value: value or all_label()
    )
    payment_filter = filter_columns[2].selectbox(
        text("Estado de pago", "Payment status", "付款状态"),
        ["", *sorted(work_items["ESTADO_PAGO"].dropna().unique())],
        format_func=lambda value: value or all_label(),
    )
    if selected_supplier:
        work_items = work_items[work_items["RUT_KEY"] == selected_supplier]
    if reason:
        work_items = work_items[work_items["MOTIVO"] == reason]
    if payment_filter:
        work_items = work_items[work_items["ESTADO_PAGO"] == payment_filter]

    metrics = st.columns(3)
    metrics[0].metric(text("Registros", "Records", "记录"), len(work_items))
    metrics[1].metric(text("Monto", "Amount", "金额"), format_amount(work_items[total_column].sum()))
    metrics[2].metric(
        text("Estado de pago pendiente", "Payment status pending", "付款状态待定"),
        int(work_items["PAYMENT_REVIEW_STATUS"].eq("PENDING").sum()) if source == "DOCUMENT" else "-",
    )

    select_all = st.checkbox(
        text("Seleccionar todos los registros filtrados", "Select all filtered records", "选择所有筛选记录"),
        help=text(
            "Incluye también los registros que no se muestran en la primera página.",
            "Also includes records beyond the first visible page.",
            "也包括第一页之外的记录。",
        ),
        key=f"reconcile_select_all_{source}",
    )
    editor_source = work_items.head(750).copy()
    editor_source.insert(0, "SELECCIONAR", False)
    if select_all:
        editor_source["SELECCIONAR"] = True
    editor_columns = [
        "SELECCIONAR", id_column, "SUPPLIER-F", "RUT_COMPLETO", "DOCUMENT TYPE",
        "INVOICE-F", "DATE-F", "MOTIVO", "ESTADO_PAGO", "SUBCATEGORY-F",
        total_column, "DECISION_NOTES",
    ]
    edited = st.data_editor(
        editor_source[[column for column in editor_columns if column in editor_source.columns]],
        hide_index=True, width="stretch", height=400,
        disabled=[column for column in editor_columns if column != "SELECCIONAR"],
        column_config={
            "SELECCIONAR": st.column_config.CheckboxColumn(text("Seleccionar", "Select", "选择")),
            id_column: None,
            "SUPPLIER-F": text("Proveedor", "Supplier", "供应商"),
            "RUT_COMPLETO": "RUT",
            "DOCUMENT TYPE": text("Tipo", "Type", "类型"),
            "INVOICE-F": text("Folio", "Number", "编号"),
            "DATE-F": text("Fecha", "Date", "日期"),
            "MOTIVO": text("Pendiente", "Pending", "待处理"),
            "ESTADO_PAGO": text("Pago", "Payment", "付款"),
            "SUBCATEGORY-F": text("Centro", "Center", "中心"),
            total_column: st.column_config.NumberColumn(
                text(f"Monto {currency}", f"Amount {currency}", f"金额 {currency}"),
                format="%.2f UF" if currency == "UF" else "$ %.0f",
            ),
            "DECISION_NOTES": text("Nota guardada", "Saved note", "已保存备注"),
        },
        key=f"reconciliation_editor_{source}_{selected_supplier or 'all'}",
    )
    selected_rows = edited[edited["SELECCIONAR"]].copy()
    if select_all:
        selected_ids = work_items[id_column].dropna().astype(int).unique().tolist()
        selected_amount = work_items[total_column].sum()
    else:
        selected_ids = selected_rows[id_column].dropna().astype(int).unique().tolist()
        selected_amount = selected_rows[total_column].sum()
    st.caption(
        text(
            f"{len(selected_ids)} seleccionados · {format_amount(selected_amount)}",
            f"{len(selected_ids)} selected · {format_amount(selected_amount)}",
            f"已选择 {len(selected_ids)} 条 · {format_amount(selected_amount)}",
        )
    )

    center_group, center_code, note = _center_fields(centers)
    payment_action = "NONE"
    if source == "DOCUMENT":
        payment_action = st.selectbox(
            text("Estado de pago al guardar", "Payment status when saving", "保存时的付款状态"),
            ["NONE", "PAID_CONFIRMED", "UNPAID_CONFIRMED"],
            format_func=lambda value: {
                "NONE": text("Sin cambio", "No change", "不变更"),
                "PAID_CONFIRMED": text("Confirmar pagadas", "Confirm paid", "确认已付款"),
                "UNPAID_CONFIRMED": text("Confirmar no pagadas", "Confirm unpaid", "确认未付款"),
            }[value],
            help=text(
                "Puedes guardar el centro y el estado de pago en una sola acción.",
                "You can save the cost center and payment status in one action.",
                "可以在一次操作中保存成本中心和付款状态。",
            ),
            key=f"reconcile_payment_action_{source}",
        )
    if st.button(
        text("Guardar selección", "Save selection", "保存选择"),
        icon=":material/account_tree:", type="primary", disabled=not selected_ids,
    ):
        selected_ledger = work_items[work_items[id_column].isin(selected_ids)]
        save_bulk_review_decisions(
            source, selected_ids, center_group, center_code, "COST", note,
            "SELECTION", selected_ledger["TOTAL-CLP-F"].sum(),
        )
        if source == "DOCUMENT" and payment_action != "NONE":
            save_payment_confirmation(
                selected_ids, payment_action, note
            )
        st.cache_data.clear()
        st.session_state.quality_message = text(
            (
                f"Centro y estado de pago guardados para {len(selected_ids)} registros."
                if payment_action != "NONE"
                else f"Centro asignado a {len(selected_ids)} registros."
            ),
            (
                f"Cost center and payment status saved for {len(selected_ids)} records."
                if payment_action != "NONE"
                else f"Cost center assigned to {len(selected_ids)} records."
            ),
            (
                f"已为 {len(selected_ids)} 条记录保存成本中心和付款状态。"
                if payment_action != "NONE"
                else f"已为 {len(selected_ids)} 条记录分配成本中心。"
            ),
        )
        st.rerun()

    if source == "DOCUMENT" and selected_ids:
        st.divider()
        st.subheader(text("Estado de pago", "Payment status", "付款状态"))
        selected_documents = work_items[work_items["DOCUMENT_ID"].isin(selected_ids)]
        confirmable_documents = selected_documents[selected_documents["PAYMENT_COUNT"].eq(0)]
        suggestions = _payment_suggestions(confirmable_documents, ledger)
        if not suggestions.empty:
            st.dataframe(
                suggestions.drop(columns=["DOCUMENT_ID", "PAYMENT_ID"]),
                hide_index=True, width="stretch", height=min(260, 35 + len(suggestions) * 35),
            )
            if st.button(
                text(
                    f"Aceptar {len(suggestions)} cruces sugeridos",
                    f"Accept {len(suggestions)} suggested matches",
                    f"接受 {len(suggestions)} 个建议匹配",
                ), icon=":material/link:",
            ):
                save_manual_matches_bulk(
                    list(zip(suggestions["DOCUMENT_ID"], suggestions["PAYMENT_ID"])), note
                )
                st.cache_data.clear()
                st.session_state.quality_message = text(
                    "Cruces de pago guardados.", "Payment matches saved.", "付款匹配已保存。"
                )
                st.rerun()
        else:
            st.caption(text("Sin cruces automáticos seguros para la selección.", "No safe automatic matches for this selection.", "所选记录没有安全的自动匹配。"))

        st.caption(
            text(
                "Una confirmación manual cambia el estado en toda la plataforma, aunque no exista un pago vinculado en SQLite.",
                "A manual confirmation changes the status throughout the platform even when no payment is linked in SQLite.",
                "即使 SQLite 中没有关联付款，手动确认也会更改整个平台的状态。",
            )
        )
        action_columns = st.columns(2)
        if action_columns[0].button(
            text(
                f"Confirmar pagadas ({len(confirmable_documents)})",
                f"Confirm paid ({len(confirmable_documents)})",
                f"确认已付款（{len(confirmable_documents)}）",
            ), icon=":material/paid:", disabled=confirmable_documents.empty,
        ):
            save_paid_confirmation(
                confirmable_documents["DOCUMENT_ID"].astype(int).tolist(), note
            )
            st.cache_data.clear()
            st.session_state.quality_message = text(
                "Estado pagado confirmado.", "Paid status confirmed.", "已确认付款状态。"
            )
            st.rerun()
        if action_columns[1].button(
            text(
                f"Confirmar no pagadas ({len(confirmable_documents)})",
                f"Confirm unpaid ({len(confirmable_documents)})",
                f"确认未付款（{len(confirmable_documents)}）",
            ), icon=":material/money_off:", disabled=confirmable_documents.empty,
        ):
            save_unpaid_confirmation(
                confirmable_documents["DOCUMENT_ID"].astype(int).tolist(), note
            )
            st.cache_data.clear()
            st.session_state.quality_message = text(
                "Estado no pagado confirmado.", "Unpaid status confirmed.", "已确认未付款状态。"
            )
            st.rerun()

        if len(confirmable_documents) == 1:
            document = confirmable_documents.iloc[0]
            candidates = ledger[
                (ledger["RECORD_TYPE"] == "PAYMENT")
                & ledger["PAYMENT_ID"].notna()
                & (ledger["RUT_KEY"] == document["RUT_KEY"])
            ].copy()
            if not candidates.empty:
                candidates["DIFERENCIA_CLP"] = (
                    candidates["TOTAL-CLP-F"].abs() - abs(document["TOTAL-CLP-F"])
                ).abs()
                candidates = candidates.sort_values(["DIFERENCIA_CLP", "DATE-F"]).head(50)
                candidate_ids = candidates["PAYMENT_ID"].astype(int).tolist()
                selected_payment = st.selectbox(
                    text("Vincular otro pago", "Link another payment", "关联其他付款"),
                    candidate_ids,
                    format_func=lambda payment_id: (
                        f"{candidates.loc[candidates['PAYMENT_ID'] == payment_id, 'DATE-F'].iloc[0]:%d/%m/%Y} · "
                        f"{candidates.loc[candidates['PAYMENT_ID'] == payment_id, 'INVOICE-F'].iloc[0] or 'sin folio'} · "
                        f"{format_clp_compact(candidates.loc[candidates['PAYMENT_ID'] == payment_id, 'TOTAL-CLP-F'].iloc[0])}"
                    ),
                )
                if st.button(text("Vincular pago", "Link payment", "关联付款"), icon=":material/link:"):
                    save_manual_matches_bulk([(int(document["DOCUMENT_ID"]), selected_payment)], note)
                    st.cache_data.clear()
                    st.session_state.quality_message = text(
                        "Pago vinculado.", "Payment linked.", "付款已关联。"
                    )
                    st.rerun()


with credit_tab:
    credit_notes = get_credit_note_decisions()
    metrics = st.columns(5)
    metrics[0].metric(text("Notas de crédito", "Credit notes", "贷项通知单"), len(credit_notes))
    metrics[1].metric(text("Pendientes", "Pending", "待处理"), int(credit_notes["decision_type"].isna().sum()))
    metrics[2].metric(text("Vinculadas", "Linked", "已关联"), int(credit_notes["decision_type"].eq("LINKED").sum()))
    metrics[3].metric(
        text("Ajustes", "Adjustments", "调整"),
        int(credit_notes["classification"].eq("GLOBAL_ADJUSTMENT_APPLIED").sum()),
    )
    metrics[4].metric(
        text("Independientes", "Standalone", "独立"),
        int(
            (
                credit_notes["decision_type"].eq("STANDALONE")
                & ~credit_notes["classification"].eq("GLOBAL_ADJUSTMENT_APPLIED")
            ).sum()
        ),
    )
    credit_search = st.text_input(text("Proveedor o RUT", "Supplier or RUT", "供应商或 RUT"), key="credit_search")
    credit_view = credit_notes.copy()
    if credit_search.strip():
        term = credit_search.strip()
        rut = credit_view["supplier_rut"].astype(str) + "-" + credit_view["supplier_dv"].fillna("").astype(str)
        credit_view = credit_view[
            credit_view["supplier_name"].str.contains(term, case=False, na=False)
            | rut.str.contains(term, case=False, na=False)
        ]
    if credit_view.empty:
        st.info(text("No hay notas de crédito para este filtro.", "No credit notes for this filter.", "此筛选条件下无贷项通知单。"))
    else:
        credit_ids = credit_view["credit_note_id"].astype(int).tolist()
        credit_id = st.selectbox(
            text("Nota de crédito", "Credit note", "贷项通知单"), credit_ids,
            format_func=lambda value: (
                f"{credit_view.loc[credit_view['credit_note_id'] == value, 'supplier_name'].iloc[0]} · "
                f"{credit_view.loc[credit_view['credit_note_id'] == value, 'document_number'].iloc[0]} · "
                f"{format_clp_compact(credit_view.loc[credit_view['credit_note_id'] == value, 'amount_clp'].iloc[0])}"
            ),
        )
        credit = credit_view.loc[credit_view["credit_note_id"] == credit_id].iloc[0]
        detail = st.columns(4)
        detail[0].metric("RUT", f"{credit['supplier_rut']}-{credit['supplier_dv']}")
        detail[1].metric(text("Folio", "Number", "编号"), credit["document_number"])
        detail[2].metric(text("Fecha", "Date", "日期"), credit["issue_date"])
        detail[3].metric(text("Monto", "Amount", "金额"), format_clp_compact(credit["amount_clp"]))
        if pd.notna(credit.get("reference_classifications")):
            st.caption(
                text("Clasificación XML: ", "XML classification: ", "XML 分类：")
                + str(credit["reference_classifications"])
                + " · "
                + text("Código: ", "Code: ", "代码：")
                + str(credit["reference_codes"] or "-")
            )
        if credit.get("classification") == "GLOBAL_ADJUSTMENT_APPLIED":
            st.success(text("Ajuste global aplicado al costo.", "Global adjustment applied to cost.", "全球调整已计入成本。"))
        candidates = get_credit_note_candidates(credit_id)
        credit_note = st.text_area(
            text("Nota", "Note", "备注"), value=credit["notes"] or "", height=68, key=f"credit_note_{credit_id}"
        )
        if not candidates.empty:
            candidate_ids = candidates["invoice_document_id"].astype(int).tolist()
            current_invoice = int(credit["invoice_document_id"]) if pd.notna(credit["invoice_document_id"]) else None
            invoice_id = st.selectbox(
                text("Factura relacionada", "Related invoice", "相关发票"), candidate_ids,
                index=candidate_ids.index(current_invoice) if current_invoice in candidate_ids else 0,
                format_func=lambda value: (
                    f"Tipo {int(candidates.loc[candidates['invoice_document_id'] == value, 'document_type'].iloc[0])} · "
                    f"Folio {candidates.loc[candidates['invoice_document_id'] == value, 'document_number'].iloc[0]} · "
                    f"{candidates.loc[candidates['invoice_document_id'] == value, 'issue_date'].iloc[0]} · "
                    f"{format_clp_compact(candidates.loc[candidates['invoice_document_id'] == value, 'amount_clp'].iloc[0])}"
                ),
            )
            invoice_amount = float(candidates.loc[candidates["invoice_document_id"] == invoice_id, "amount_clp"].iloc[0])
            result_label = text("Anulación total", "Full cancellation", "全部冲销") if float(credit["amount_clp"]) >= invoice_amount - 1 else text("Rebaja parcial", "Partial credit", "部分冲减")
            st.caption(result_label)
        else:
            invoice_id = None
            st.warning(text("No se encontraron facturas anteriores del mismo proveedor.", "No earlier invoices were found for the same supplier.", "未找到同一供应商的更早发票。"))
        buttons = st.columns(2)
        if buttons[0].button(
            text("Vincular a factura", "Link to invoice", "关联发票"), icon=":material/link:",
            type="primary", disabled=invoice_id is None,
        ):
            save_credit_note_decision(credit_id, "LINKED", invoice_id, credit_note)
            st.cache_data.clear()
            st.session_state.quality_message = text("Nota de crédito vinculada.", "Credit note linked.", "贷项通知单已关联。")
            st.rerun()
        if buttons[1].button(
            text("Registrar independiente", "Mark as standalone", "标记为独立"), icon=":material/check:",
        ):
            save_credit_note_decision(credit_id, "STANDALONE", notes=credit_note)
            st.cache_data.clear()
            st.session_state.quality_message = text("Nota de crédito registrada.", "Credit note saved.", "贷项通知单已保存。")
            st.rerun()


with uf_tab:
    try:
        uf_coverage = get_uf_coverage()
        today = date.today()
        current_month_uf = get_month_uf_rates(today.year, today.month)
    except Exception as exc:
        show_database_error(exc)
    else:
        current_rates = current_month_uf[current_month_uf["uf_date"] <= today.isoformat()]
        today_uf = None if current_rates.empty else float(current_rates.iloc[-1]["uf_clp"])
        latest_uf = uf_coverage["latest_date"]
        missing_uf = int(uf_coverage["missing_date_count"])
        metrics = st.columns(3)
        metrics[0].metric(
            text("UF del día", "Today's UF", "今日 UF"),
            f"$ {today_uf:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".") if today_uf else "-",
        )
        metrics[1].metric(text("Actualizada hasta", "Updated through", "更新至"), latest_uf or "-")
        metrics[2].metric(text("Fechas faltantes", "Missing dates", "缺失日期"), missing_uf)
        update_years = years_requiring_update(uf_coverage, today) or [today.year]
        if st.button(text("Actualizar desde SII", "Update from SII", "从 SII 更新"), icon=":material/sync:", type="primary"):
            try:
                result = update_uf_from_sii(update_years)
            except Exception as exc:
                st.error(str(exc))
            else:
                st.cache_data.clear()
                st.session_state.quality_message = text(
                    f"UF actualizada: {result.inserted} valores nuevos y {result.updated} corregidos.",
                    f"UF updated: {result.inserted} new values and {result.updated} corrected.",
                    f"UF 已更新：{result.inserted} 个新值，{result.updated} 个修正值。",
                )
                st.rerun()
        st.dataframe(
            current_month_uf, hide_index=True, width="stretch", height=370,
            column_config={"uf_date": text("Fecha", "Date", "日期"), "uf_clp": st.column_config.NumberColumn("UF", format="$ %.2f")},
        )


with history_tab:
    batches = get_reconciliation_batches()
    if batches.empty:
        st.caption(text("Sin acciones masivas guardadas.", "No saved bulk actions.", "暂无已保存的批量操作。"))
    else:
        batch_display = batches.copy()
        batch_display["ESTADO"] = batch_display["reversed_at"].map(
            lambda value: text("Revertida", "Reversed", "已撤销") if pd.notna(value) else text("Aplicada", "Applied", "已应用")
        )
        st.dataframe(
            batch_display[["created_at", "item_type", "record_count", "total_amount_clp", "subcategory_code", "subcategory_name", "notes", "ESTADO"]],
            hide_index=True, width="stretch", height=280,
            column_config={
                "created_at": text("Fecha", "Date", "日期"), "item_type": text("Origen", "Source", "来源"),
                "record_count": text("Registros", "Records", "记录"),
                "total_amount_clp": st.column_config.NumberColumn(text("Monto CLP", "Amount CLP", "金额 CLP"), format="$ %.0f"),
                "subcategory_code": text("Centro", "Center", "中心"),
                "subcategory_name": text("Centro de costo", "Cost center", "成本中心"),
                "notes": text("Nota", "Note", "备注"), "ESTADO": text("Estado", "Status", "状态"),
            },
        )
        reversible = batches[batches["reversed_at"].isna()]
        if not reversible.empty:
            selected_batch = st.selectbox(text("Acción a revertir", "Action to reverse", "要撤销的操作"), reversible["batch_id"].tolist())
            if st.button(text("Revertir", "Reverse", "撤销"), icon=":material/undo:"):
                restored = reverse_reconciliation_batch(selected_batch)
                st.cache_data.clear()
                st.session_state.quality_message = text(
                    f"Acción revertida: {restored} registros.", f"Action reversed: {restored} records.", f"操作已撤销：{restored} 条记录。"
                )
                st.rerun()
    st.divider()
    sii_history, payment_history = get_import_history()
    history_columns = st.columns(2)
    with history_columns[0]:
        st.subheader("RCV SII")
        st.dataframe(sii_history, hide_index=True, width="stretch", height=300)
    with history_columns[1]:
        st.subheader(text("Pagos", "Payments", "付款"))
        st.dataframe(payment_history, hide_index=True, width="stretch", height=300)


with issues_tab:
    try:
        options = get_quality_options()
        columns = st.columns(4)
        issue_area = columns[0].selectbox(text("Área", "Area", "领域"), ["", *options["issue_area"]], format_func=lambda value: value or all_label())
        issue_type = columns[1].selectbox(text("Hallazgo", "Issue", "问题"), ["", *options["issue_type"]], format_func=lambda value: value or all_label())
        severity = columns[2].selectbox(text("Severidad", "Severity", "严重性"), ["", *options["severity"]], format_func=lambda value: value or all_label())
        issue_status = columns[3].selectbox(text("Estado", "Status", "状态"), ["", *options["issue_status"]], format_func=lambda value: value or all_label())
        filters = QualityFilters(issue_area, issue_type, severity, issue_status)
        summary = get_quality_summary(filters)
        issues = get_quality_issues(filters)
    except Exception as exc:
        show_database_error(exc)
    else:
        metrics = st.columns(4)
        metrics[0].metric(text("Hallazgos", "Issues", "问题"), summary["issue_count"])
        metrics[1].metric(text("Errores", "Errors", "错误"), summary["error_count"])
        metrics[2].metric(text("Advertencias", "Warnings", "警告"), summary["warning_count"])
        metrics[3].metric(text("Abiertos", "Open", "未解决"), summary["open_count"])
        st.dataframe(issues, hide_index=True, width="stretch", height=470)
