from __future__ import annotations

import hashlib
import re
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path

import pandas as pd

from scripts.import_payments import (
    RAW_COLUMNS,
    REQUIRED_HEADERS,
    clean_rut,
    normalize_text,
    normalized_payment,
    parse_amount,
    parse_date,
    parse_integer,
)
from utils.config import DATABASE_PATH
from utils.migrations import apply_pending_migrations, create_database_backup


SII_COLUMNS = {
    "Nro": "nro",
    "Tipo Doc": "tipo_doc",
    "Tipo Compra": "tipo_compra",
    "RUT Proveedor": "rut_proveedor",
    "Razon Social": "razon_social",
    "Folio": "folio",
    "Fecha Docto": "fecha_docto",
    "Fecha Recepcion": "fecha_recepcion",
    "Fecha Acuse": "fecha_acuse",
    "Monto Exento": "monto_exento",
    "Monto Neto": "monto_neto",
    "Monto IVA Recuperable": "monto_iva_recuperable",
    "Monto Iva No Recuperable": "monto_iva_no_recuperable",
    "Codigo IVA No Rec.": "codigo_iva_no_rec",
    "Monto Total": "monto_total",
    "Monto Neto Activo Fijo": "monto_neto_activo_fijo",
    "IVA Activo Fijo": "iva_activo_fijo",
    "IVA uso Comun": "iva_uso_comun",
    "Impto. Sin Derecho a Credito": "impto_sin_derecho_credito",
    "IVA No Retenido": "iva_no_retenido",
    "Tabacos Puros": "tabacos_puros",
    "Tabacos Cigarrillos": "tabacos_cigarrillos",
    "Tabacos Elaborados": "tabacos_elaborados",
    "NCE o NDE sobre Fact. de Compra": "nce_nde_fact_compra",
    "Codigo Otro Impuesto": "codigo_otro_impuesto",
    "Valor Otro Impuesto": "valor_otro_impuesto",
    "Tasa Otro Impuesto": "tasa_otro_impuesto",
}
SII_REQUIRED = {"Tipo Doc", "RUT Proveedor", "Razon Social", "Folio", "Fecha Docto", "Monto Total"}


@dataclass(frozen=True)
class MonthlyPreview:
    period: str
    sii_rows: int
    sii_documents: int
    sii_duplicates: int
    payment_rows: int
    valid_payments: int
    invalid_payments: int
    first_payment_date: str | None
    last_payment_date: str | None


@dataclass(frozen=True)
class MonthlyImportResult:
    period: str
    sii_source_id: int
    payment_import_id: int
    new_documents: int
    duplicate_documents: int
    valid_payments: int
    invalid_payments: int
    backup_path: Path


def _file_hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _validate_period(period: str) -> str:
    value = str(period).strip()
    if not re.fullmatch(r"20\d{2}(0[1-9]|1[0-2])", value):
        raise ValueError("El período debe tener formato AAAAMM.")
    return value


def _read_csv(data: bytes) -> pd.DataFrame:
    last_error: Exception | None = None
    for encoding in ("utf-8-sig", "latin-1"):
        try:
            frame = pd.read_csv(
                BytesIO(data), sep=";", dtype=object, keep_default_na=False, encoding=encoding
            )
            if len(frame.columns) == 1:
                frame = pd.read_csv(
                    BytesIO(data), sep=",", dtype=object, keep_default_na=False, encoding=encoding
                )
            return frame
        except (UnicodeDecodeError, pd.errors.ParserError) as exc:
            last_error = exc
    raise ValueError(f"No fue posible leer el CSV: {last_error}")


def read_sii_file(data: bytes, filename: str) -> pd.DataFrame:
    suffix = Path(filename).suffix.lower()
    if suffix == ".csv":
        frame = _read_csv(data)
    elif suffix == ".xlsx":
        frame = pd.read_excel(BytesIO(data), dtype=object, keep_default_na=False)
    else:
        raise ValueError("El archivo SII debe ser CSV o XLSX.")

    frame.columns = [str(column).strip() for column in frame.columns]
    missing = sorted(SII_REQUIRED - set(frame.columns))
    if missing:
        raise ValueError(f"RCV SII: faltan columnas {', '.join(missing)}.")
    for column in SII_COLUMNS:
        if column not in frame.columns:
            frame[column] = None
    return frame[list(SII_COLUMNS)].rename(columns=SII_COLUMNS)


def read_monthly_payments(data: bytes, filename: str) -> tuple[pd.DataFrame, str]:
    suffix = Path(filename).suffix.lower()
    candidates: list[tuple[str, pd.DataFrame]] = []
    if suffix == ".csv":
        candidates.append(("PAGOS", _read_csv(data)))
    elif suffix == ".xlsx":
        workbook = pd.ExcelFile(BytesIO(data))
        try:
            for sheet in workbook.sheet_names:
                frame = pd.read_excel(
                    workbook, sheet_name=sheet, dtype=object, keep_default_na=False
                )
                frame.columns = [str(column).strip() for column in frame.columns]
                if REQUIRED_HEADERS.issubset(frame.columns):
                    candidates.append((sheet, frame))
        finally:
            workbook.close()
    else:
        raise ValueError("El archivo mensual de pagos debe ser CSV o XLSX.")

    if not candidates:
        raise ValueError(
            "Pagos: no se encontró una tabla con RUT, DATE-PAYMENT, PAID-CLP, CAT y SUB-CAT."
        )
    if len(candidates) > 1:
        raise ValueError("Pagos: el archivo debe contener una sola tabla mensual.")

    sheet, frame = candidates[0]
    frame.columns = [str(column).strip() for column in frame.columns]
    for raw_column in RAW_COLUMNS:
        if raw_column not in frame.columns:
            frame[raw_column] = None
    return frame[list(RAW_COLUMNS)].rename(columns=RAW_COLUMNS), sheet


def _document_from_row(row: pd.Series, source_id: int, source_row: int, period: str) -> dict | None:
    document_type = parse_integer(row["tipo_doc"])
    supplier_full = clean_rut(row["rut_proveedor"])
    folio = normalize_text(row["folio"])
    if document_type is None or not supplier_full or len(supplier_full) < 2 or not folio:
        return None
    supplier_rut, supplier_dv = supplier_full[:-1], supplier_full[-1]
    folio = folio.upper().replace(" ", "")
    return {
        "document_key": f"{supplier_rut}|{document_type}|{folio}",
        "supplier_rut": supplier_rut,
        "supplier_dv": supplier_dv,
        "supplier_name": normalize_text(row["razon_social"]),
        "document_type": document_type,
        "document_number": folio,
        "issue_date": parse_date(row["fecha_docto"]),
        "reception_date": parse_date(row["fecha_recepcion"]),
        "source_period": period,
        "exempt_amount_clp": parse_amount(row["monto_exento"]),
        "net_amount_clp": parse_amount(row["monto_neto"]),
        "recoverable_vat_clp": parse_amount(row["monto_iva_recuperable"]),
        "non_recoverable_vat_clp": parse_amount(row["monto_iva_no_recuperable"]),
        "total_amount_clp": parse_amount(row["monto_total"]),
        "purchase_type": normalize_text(row["tipo_compra"]),
        "source_id": source_id,
        "source_row": source_row,
        "quality_status": "ok",
    }


def _payment_dates(frame: pd.DataFrame) -> list[str]:
    dates = [parse_date(value) for value in frame["date_payment"]]
    return [value for value in dates if value]


def _existing_periods(database_path: Path, period: str) -> tuple[bool, bool]:
    with closing(sqlite3.connect(database_path)) as connection:
        sii_exists = connection.execute(
            """
            SELECT 1 FROM sources
            WHERE source_area = 'SII' AND source_type = 'RCV_COMPRA' AND source_period = ?
            LIMIT 1
            """,
            (period,),
        ).fetchone()
        payment_exists = connection.execute(
            """
            SELECT 1 FROM payment_imports
            WHERE is_active = 1 AND import_mode = 'monthly' AND source_period = ?
            LIMIT 1
            """,
            (period,),
        ).fetchone()
    return bool(sii_exists), bool(payment_exists)


def preview_monthly_files(
    period: str,
    sii_data: bytes,
    sii_filename: str,
    payment_data: bytes,
    payment_filename: str,
    database_path: Path = DATABASE_PATH,
) -> MonthlyPreview:
    period = _validate_period(period)
    sii_exists, payment_exists = _existing_periods(database_path, period)
    if sii_exists or payment_exists:
        existing = []
        if sii_exists:
            existing.append("RCV SII")
        if payment_exists:
            existing.append("pagos")
        raise ValueError(f"El período {period} ya contiene: {', '.join(existing)}.")

    sii = read_sii_file(sii_data, sii_filename)
    payments, _ = read_monthly_payments(payment_data, payment_filename)
    payment_dates = _payment_dates(payments)
    outside_period = [value for value in payment_dates if value.replace("-", "")[:6] != period]
    if outside_period:
        raise ValueError(
            f"Pagos: {len(outside_period)} fechas no pertenecen al período {period}."
        )

    with closing(sqlite3.connect(database_path)) as connection:
        existing_keys = {
            row[0] for row in connection.execute("SELECT document_key FROM documents")
        }
    document_keys: list[str] = []
    for offset, (_, row) in enumerate(sii.iterrows(), start=2):
        document = _document_from_row(row, 0, offset, period)
        if document:
            document_keys.append(document["document_key"])

    valid_payments = 0
    for offset, (_, row) in enumerate(payments.iterrows(), start=2):
        payment = normalized_payment(0, 0, period[:4], offset, row)
        if payment is not None and payment["payment_date"]:
            valid_payments += 1

    return MonthlyPreview(
        period=period,
        sii_rows=len(sii),
        sii_documents=len(document_keys),
        sii_duplicates=sum(key in existing_keys for key in document_keys),
        payment_rows=len(payments),
        valid_payments=valid_payments,
        invalid_payments=len(payments) - valid_payments,
        first_payment_date=min(payment_dates) if payment_dates else None,
        last_payment_date=max(payment_dates) if payment_dates else None,
    )


def _insert_issue(
    connection: sqlite3.Connection,
    *,
    area: str,
    issue_type: str,
    source_id: int,
    source_row: int,
    description: str,
    document_id: int | None = None,
    document_key: str | None = None,
    payment_id: int | None = None,
    payment_key: str | None = None,
    payment_import_id: int | None = None,
    severity: str = "warning",
) -> None:
    connection.execute(
        """
        INSERT INTO validation_issues(
            issue_area, issue_type, severity, source_id, source_row,
            document_id, document_key, payment_id, payment_key,
            issue_description, issue_status, payment_import_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'open', ?)
        """,
        (
            area, issue_type, severity, source_id, source_row, document_id,
            document_key, payment_id, payment_key, description, payment_import_id,
        ),
    )


def import_monthly_files(
    period: str,
    sii_data: bytes,
    sii_filename: str,
    payment_data: bytes,
    payment_filename: str,
    database_path: Path = DATABASE_PATH,
) -> MonthlyImportResult:
    preview = preview_monthly_files(
        period, sii_data, sii_filename, payment_data, payment_filename, database_path
    )
    apply_pending_migrations(database_path=database_path)
    sii = read_sii_file(sii_data, sii_filename)
    payments, payment_sheet = read_monthly_payments(payment_data, payment_filename)
    sii_hash = _file_hash(sii_data)
    payment_hash = _file_hash(payment_data)
    backup_path = create_database_backup(
        database_path, database_path.parent / "backups", f"monthly_{period}"
    )

    with closing(sqlite3.connect(database_path)) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 5000")
        try:
            connection.execute("BEGIN IMMEDIATE")
            if connection.execute(
                "SELECT 1 FROM sources WHERE file_hash = ? LIMIT 1", (sii_hash,)
            ).fetchone():
                raise ValueError("El archivo SII ya fue importado anteriormente.")
            if connection.execute(
                "SELECT 1 FROM payment_imports WHERE source_hash = ? LIMIT 1", (payment_hash,)
            ).fetchone():
                raise ValueError("El archivo de pagos ya fue importado anteriormente.")

            source_cursor = connection.execute(
                """
                INSERT INTO sources(
                    source_area, source_type, source_file_path, source_file_name,
                    source_period, company_rut, file_hash, notes
                ) VALUES ('SII', 'RCV_COMPRA', ?, ?, ?, '77337752-9', ?, ?)
                """,
                (
                    f"streamlit://SII/{period}/{sii_filename}", sii_filename, period,
                    sii_hash, f"Carga mensual confirmada desde Streamlit: {period}",
                ),
            )
            sii_source_id = int(source_cursor.lastrowid)
            raw_columns = ["source_id", "source_row", *SII_COLUMNS.values()]
            raw_sql = (
                f"INSERT INTO sii_rcv_raw({', '.join(raw_columns)}) "
                f"VALUES ({', '.join('?' for _ in raw_columns)})"
            )
            document_columns = [
                "document_key", "supplier_rut", "supplier_dv", "supplier_name",
                "document_type", "document_number", "issue_date", "reception_date",
                "source_period", "exempt_amount_clp", "net_amount_clp",
                "recoverable_vat_clp", "non_recoverable_vat_clp", "total_amount_clp",
                "purchase_type", "source_id", "source_row", "quality_status",
            ]
            document_sql = (
                f"INSERT OR IGNORE INTO documents({', '.join(document_columns)}) "
                f"VALUES ({', '.join('?' for _ in document_columns)})"
            )
            new_documents = 0
            duplicate_documents = 0
            for offset, (_, row) in enumerate(sii.iterrows(), start=2):
                raw_values = [sii_source_id, offset]
                raw_values.extend(normalize_text(row[column]) for column in SII_COLUMNS.values())
                connection.execute(raw_sql, raw_values)
                document = _document_from_row(row, sii_source_id, offset, period)
                if document is None:
                    _insert_issue(
                        connection, area="SII_RCV", issue_type="invalid_raw_row",
                        source_id=sii_source_id, source_row=offset,
                        description=f"RCV {period}, fila {offset}: documento no normalizable.",
                    )
                    continue
                cursor = connection.execute(
                    document_sql, [document[column] for column in document_columns]
                )
                if cursor.rowcount:
                    new_documents += 1
                else:
                    duplicate_documents += 1
                    _insert_issue(
                        connection, area="SII_RCV", issue_type="duplicate_document_key",
                        source_id=sii_source_id, source_row=offset,
                        document_key=document["document_key"],
                        description=(
                            f"RCV {period}, fila {offset}: documento "
                            f"{document['document_key']} ya existente."
                        ),
                    )

            imported_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
            payment_cursor = connection.execute(
                """
                INSERT INTO payment_imports(
                    source_file_path, source_file_name, source_hash, imported_at,
                    row_count, valid_row_count, invalid_row_count,
                    first_payment_date, last_payment_date, is_active, notes,
                    source_period, import_mode
                ) VALUES (?, ?, ?, ?, ?, 0, 0, ?, ?, 1, ?, ?, 'monthly')
                """,
                (
                    f"streamlit://PAYMENTS/{period}/{payment_filename}", payment_filename,
                    payment_hash, imported_at, len(payments), preview.first_payment_date,
                    preview.last_payment_date,
                    "Carga mensual incremental confirmada desde Streamlit.", period,
                ),
            )
            payment_import_id = int(payment_cursor.lastrowid)
            payment_source_cursor = connection.execute(
                """
                INSERT INTO sources(
                    source_area, source_type, source_file_path, source_file_name,
                    source_period, company_rut, file_hash, notes, payment_import_id
                ) VALUES ('PAYMENTS', 'H-P_MONTHLY', ?, ?, ?, '77337752-9', ?, ?, ?)
                """,
                (
                    f"streamlit://PAYMENTS/{period}/{payment_filename}", payment_filename,
                    period, payment_hash, f"Carga mensual {period}, hoja {payment_sheet}",
                    payment_import_id,
                ),
            )
            payment_source_id = int(payment_source_cursor.lastrowid)
            payment_raw_columns = [
                "payment_import_id", "source_id", "source_sheet", "source_row",
                *RAW_COLUMNS.values(),
            ]
            payment_raw_sql = (
                f"INSERT INTO payments_raw({', '.join(payment_raw_columns)}) "
                f"VALUES ({', '.join('?' for _ in payment_raw_columns)})"
            )
            payment_columns = [
                "payment_key", "supplier_rut", "supplier_dv", "supplier_name",
                "payment_date", "payment_year", "invoice_number_raw", "invoice_number_base",
                "invoice_split_suffix", "invoice_has_split_marker", "invoice_date",
                "document_type_hint", "exempt_amount_clp", "net_amount_clp",
                "vat_amount_clp", "other_taxes_clp", "gross_amount_clp",
                "paid_amount_clp", "cost_center_cat", "cost_center_sub_cat", "uf_value",
                "exempt_amount_uf", "net_amount_uf", "vat_amount_uf", "other_taxes_uf",
                "gross_amount_uf", "paid_amount_uf", "construction_cost_flag", "informe_no",
                "net_recognized_clp", "vat_recognized_clp", "recognized_amount_clp",
                "net_recognized_uf", "vat_recognized_uf", "recognized_amount_uf",
                "description", "payment_type", "supplier_aux", "source_id", "source_sheet",
                "source_row", "quality_status", "payment_import_id",
            ]
            payment_sql = (
                f"INSERT INTO payments({', '.join(payment_columns)}) "
                f"VALUES ({', '.join('?' for _ in payment_columns)})"
            )
            valid_payments = 0
            invalid_payments = 0
            for offset, (_, row) in enumerate(payments.iterrows(), start=2):
                raw_values = [payment_import_id, payment_source_id, payment_sheet, offset]
                raw_values.extend(normalize_text(row[column]) for column in RAW_COLUMNS.values())
                connection.execute(payment_raw_sql, raw_values)
                payment = normalized_payment(
                    payment_import_id, payment_source_id, period[:4], offset, row
                )
                if payment is None or not payment["payment_date"]:
                    invalid_payments += 1
                    _insert_issue(
                        connection, area="PAYMENTS_HP", issue_type="invalid_raw_row",
                        source_id=payment_source_id, source_row=offset,
                        payment_import_id=payment_import_id,
                        description=f"Pagos {period}, fila {offset}: pago no normalizable.",
                    )
                    continue
                payment["payment_key"] = f"HP|{payment_import_id}|{period}|{offset}"
                payment["payment_year"] = int(period[:4])
                payment["source_sheet"] = payment_sheet
                cursor = connection.execute(
                    payment_sql, [payment[column] for column in payment_columns]
                )
                payment_id = int(cursor.lastrowid)
                valid_payments += 1
                if not payment["supplier_rut"]:
                    _insert_issue(
                        connection, area="PAYMENTS_HP", issue_type="missing_supplier_rut",
                        source_id=payment_source_id, source_row=offset,
                        payment_id=payment_id, payment_key=payment["payment_key"],
                        payment_import_id=payment_import_id,
                        description=f"Pagos {period}, fila {offset}: pago sin RUT.",
                    )
                if payment["cost_center_cat"] is None or payment["cost_center_sub_cat"] is None:
                    _insert_issue(
                        connection, area="PAYMENTS_HP", issue_type="missing_cost_center",
                        source_id=payment_source_id, source_row=offset,
                        payment_id=payment_id, payment_key=payment["payment_key"],
                        payment_import_id=payment_import_id,
                        description=f"Pagos {period}, fila {offset}: pago sin CAT o SUB-CAT.",
                    )

            connection.execute(
                """
                UPDATE payment_imports
                SET valid_row_count = ?, invalid_row_count = ?
                WHERE payment_import_id = ?
                """,
                (valid_payments, invalid_payments, payment_import_id),
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise

    return MonthlyImportResult(
        period=period,
        sii_source_id=sii_source_id,
        payment_import_id=payment_import_id,
        new_documents=new_documents,
        duplicate_documents=duplicate_documents,
        valid_payments=valid_payments,
        invalid_payments=invalid_payments,
        backup_path=backup_path,
    )
