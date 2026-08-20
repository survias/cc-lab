from __future__ import annotations

import argparse
import re
import sqlite3
import sys
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from utils.config import BACKUPS_PATH, DATABASE_PATH, PAYMENTS_SOURCE_PATH, PROJECT_ROOT
from utils.migrations import apply_pending_migrations, create_database_backup, file_sha256


PAYMENT_SHEETS = ("2021", "2022", "2023", "2024", "2025", "2026")
RAW_COLUMNS = {
    "RUT-F": "rut_f",
    "RUT": "rut",
    "DV": "dv",
    "SUPPLIER-F": "supplier_f",
    "DATE-PAYMENT": "date_payment",
    "INVOICE": "invoice",
    "EXCENT-CLP": "excent_clp",
    "NET-CLP": "net_clp",
    "VAT-CLP": "vat_clp",
    "OTHER TAXES - CLP": "other_taxes_clp",
    "GROSS-CLP": "gross_clp",
    "PAID-CLP": "paid_clp",
    "CAT": "cat",
    "SUB-CAT": "sub_cat",
    "UF": "uf",
    "EXCENT-UF": "excent_uf",
    "NET-UF": "net_uf",
    "VAT-UF": "vat_uf",
    "OTHER TAXES-UF": "other_taxes_uf",
    "GROSS-UF": "gross_uf",
    "PAID-UF": "paid_uf",
    "CONSTRUCTION COST (YES/NO)": "construction_cost_yes_no",
    "INFORME N°": "informe_no",
    "NET RECOGNIZED (CLP)": "net_recognized_clp",
    "VAT RECOGNIZED (CLP)": "vat_recognized_clp",
    "RECOGNIZED AMOUNT (CLP)": "recognized_amount_clp",
    "NET RECOGNIZED (UF)": "net_recognized_uf",
    "VAT RECOGNIZED (UF)": "vat_recognized_uf",
    "RECOGNIZED AMOUNT (UF)": "recognized_amount_uf",
    "DESCRIPCIÓN": "description",
    "TIPO": "payment_type",
    "SUPPLIER-AUX": "supplier_aux",
    "DOCTO TYPE (INVOICE-BH-OTHER)": "document_type_hint",
    "INVOICE DATE": "invoice_date",
}
REQUIRED_HEADERS = {"RUT", "DATE-PAYMENT", "PAID-CLP", "CAT", "SUB-CAT"}


@dataclass(frozen=True)
class PaymentImportResult:
    payment_import_id: int
    created: bool
    source_hash: str
    row_count: int
    valid_row_count: int
    invalid_row_count: int
    first_payment_date: str | None
    last_payment_date: str | None
    backup_path: Path | None
    migrations_applied: tuple[str, ...]


def normalize_text(value: Any) -> str | None:
    if value is None or pd.isna(value):
        return None
    text = str(value).strip()
    if text.endswith(".0") and text[:-2].replace("-", "").isdigit():
        text = text[:-2]
    return text or None


def clean_rut(value: Any) -> str | None:
    text = normalize_text(value)
    if not text:
        return None
    cleaned = re.sub(r"[^0-9K]", "", text.replace(".", "").replace("-", "").upper())
    return cleaned or None


def parse_amount(value: Any) -> float:
    text = normalize_text(value)
    if not text:
        return 0.0
    if "," in text:
        text = text.replace(".", "").replace(",", ".")
    elif text.count(".") > 1:
        text = text.replace(".", "")
    try:
        return float(text)
    except ValueError:
        return 0.0


def parse_integer(value: Any) -> int | None:
    text = normalize_text(value)
    if not text:
        return None
    try:
        return int(float(text))
    except ValueError:
        return None


def parse_date(value: Any) -> str | None:
    if isinstance(value, (datetime, pd.Timestamp)):
        return value.strftime("%Y-%m-%d")
    text = normalize_text(value)
    if not text:
        return None
    text = text.split(" ")[0]
    for date_format in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(text, date_format).strftime("%Y-%m-%d")
        except ValueError:
            continue
    parsed = pd.to_datetime(text, errors="coerce", dayfirst=True)
    return None if pd.isna(parsed) else parsed.strftime("%Y-%m-%d")


def split_invoice(value: Any) -> tuple[str | None, str | None, int]:
    text = normalize_text(value)
    if not text:
        return None, None, 0
    text = text.upper().replace(" ", "")
    match = re.fullmatch(r"(\d+)([A-Z]+)", text)
    if match:
        return match.group(1), match.group(2), 1
    return text, None, 0


def normalize_flag(value: Any) -> str | None:
    text = normalize_text(value)
    if not text:
        return None
    upper = text.upper()
    if upper in {"YES", "Y", "SI", "SÍ", "1", "TRUE"}:
        return "YES"
    if upper in {"NO", "N", "0", "FALSE"}:
        return "NO"
    return upper


def read_payment_sheets(source_path: Path) -> dict[str, pd.DataFrame]:
    workbook = pd.ExcelFile(source_path)
    missing_sheets = [sheet for sheet in PAYMENT_SHEETS if sheet not in workbook.sheet_names]
    if missing_sheets:
        raise ValueError(f"Faltan pestañas de pagos: {', '.join(missing_sheets)}")

    frames: dict[str, pd.DataFrame] = {}
    for sheet in PAYMENT_SHEETS:
        frame = pd.read_excel(workbook, sheet_name=sheet, dtype=object, keep_default_na=False)
        frame.columns = [str(column).strip() for column in frame.columns]
        missing_headers = sorted(REQUIRED_HEADERS - set(frame.columns))
        if missing_headers:
            raise ValueError(f"Pestaña {sheet}: faltan columnas {', '.join(missing_headers)}")
        for raw_column in RAW_COLUMNS:
            if raw_column not in frame.columns:
                frame[raw_column] = None
        frames[sheet] = frame[list(RAW_COLUMNS)].rename(columns=RAW_COLUMNS)
    workbook.close()
    return frames


def normalized_payment(
    payment_import_id: int,
    source_id: int,
    sheet: str,
    source_row: int,
    row: pd.Series,
) -> dict[str, Any] | None:
    supplier_rut = clean_rut(row["rut"])
    supplier_name = normalize_text(row["supplier_f"])
    payment_date = parse_date(row["date_payment"])
    paid_amount_clp = parse_amount(row["paid_clp"])
    if not supplier_rut and not supplier_name and not payment_date and paid_amount_clp == 0:
        return None

    invoice_raw = normalize_text(row["invoice"])
    invoice_base, invoice_suffix, has_split_marker = split_invoice(invoice_raw)
    category = parse_integer(row["cat"])
    subcategory = parse_integer(row["sub_cat"])
    quality_status = "warning" if not supplier_rut or category is None or subcategory is None else "ok"
    return {
        "payment_key": f"HP|{payment_import_id}|{sheet}|{source_row}",
        "supplier_rut": supplier_rut,
        "supplier_dv": normalize_text(row["dv"]),
        "supplier_name": supplier_name,
        "payment_date": payment_date,
        "payment_year": int(sheet),
        "invoice_number_raw": invoice_raw,
        "invoice_number_base": invoice_base,
        "invoice_split_suffix": invoice_suffix,
        "invoice_has_split_marker": has_split_marker,
        "invoice_date": parse_date(row["invoice_date"]),
        "document_type_hint": normalize_text(row["document_type_hint"]),
        "exempt_amount_clp": parse_amount(row["excent_clp"]),
        "net_amount_clp": parse_amount(row["net_clp"]),
        "vat_amount_clp": parse_amount(row["vat_clp"]),
        "other_taxes_clp": parse_amount(row["other_taxes_clp"]),
        "gross_amount_clp": parse_amount(row["gross_clp"]),
        "paid_amount_clp": paid_amount_clp,
        "cost_center_cat": category,
        "cost_center_sub_cat": subcategory,
        "uf_value": parse_amount(row["uf"]),
        "exempt_amount_uf": parse_amount(row["excent_uf"]),
        "net_amount_uf": parse_amount(row["net_uf"]),
        "vat_amount_uf": parse_amount(row["vat_uf"]),
        "other_taxes_uf": parse_amount(row["other_taxes_uf"]),
        "gross_amount_uf": parse_amount(row["gross_uf"]),
        "paid_amount_uf": parse_amount(row["paid_uf"]),
        "construction_cost_flag": normalize_flag(row["construction_cost_yes_no"]),
        "informe_no": normalize_text(row["informe_no"]),
        "net_recognized_clp": parse_amount(row["net_recognized_clp"]),
        "vat_recognized_clp": parse_amount(row["vat_recognized_clp"]),
        "recognized_amount_clp": parse_amount(row["recognized_amount_clp"]),
        "net_recognized_uf": parse_amount(row["net_recognized_uf"]),
        "vat_recognized_uf": parse_amount(row["vat_recognized_uf"]),
        "recognized_amount_uf": parse_amount(row["recognized_amount_uf"]),
        "description": normalize_text(row["description"]),
        "payment_type": normalize_text(row["payment_type"]),
        "supplier_aux": normalize_text(row["supplier_aux"]),
        "source_id": source_id,
        "source_sheet": sheet,
        "source_row": source_row,
        "quality_status": quality_status,
        "payment_import_id": payment_import_id,
    }


def _relative_source_path(path: Path) -> str:
    try:
        return str(path.relative_to(PROJECT_ROOT))
    except ValueError:
        return str(path)


def _existing_import(connection: sqlite3.Connection, source_hash: str) -> sqlite3.Row | None:
    return connection.execute(
        """
        SELECT payment_import_id, row_count, valid_row_count, invalid_row_count,
               first_payment_date, last_payment_date, is_active
        FROM payment_imports
        WHERE source_hash = ?
        """,
        (source_hash,),
    ).fetchone()


def _insert_source(
    connection: sqlite3.Connection,
    payment_import_id: int,
    source_path: Path,
    source_hash: str,
    sheet: str,
) -> int:
    cursor = connection.execute(
        """
        INSERT INTO sources(
            source_area, source_type, source_file_path, source_file_name,
            source_period, company_rut, file_hash, notes, payment_import_id
        ) VALUES ('PAYMENTS', 'H-P', ?, ?, ?, '77337752-9', ?, ?, ?)
        """,
        (
            _relative_source_path(source_path),
            source_path.name,
            sheet,
            source_hash,
            f"H-P snapshot {payment_import_id}, sheet {sheet}",
            payment_import_id,
        ),
    )
    return int(cursor.lastrowid)


def _insert_issue(
    connection: sqlite3.Connection,
    *,
    payment_import_id: int,
    issue_type: str,
    severity: str,
    source_id: int,
    source_row: int,
    payment_id: int | None,
    payment_key: str | None,
    description: str,
) -> None:
    connection.execute(
        """
        INSERT INTO validation_issues(
            issue_area, issue_type, severity, source_id, source_row,
            payment_id, payment_key, issue_description, issue_status,
            payment_import_id
        ) VALUES ('PAYMENTS_HP', ?, ?, ?, ?, ?, ?, ?, 'open', ?)
        """,
        (
            issue_type,
            severity,
            source_id,
            source_row,
            payment_id,
            payment_key,
            description,
            payment_import_id,
        ),
    )


def import_payments(
    source_path: Path = PAYMENTS_SOURCE_PATH,
    database_path: Path = DATABASE_PATH,
) -> PaymentImportResult:
    source_path = source_path.expanduser().resolve()
    if not source_path.is_file():
        raise FileNotFoundError(f"No existe H-P: {source_path}")

    migrations = tuple(apply_pending_migrations(database_path=database_path))
    source_hash = file_sha256(source_path)
    frames = read_payment_sheets(source_path)

    with closing(sqlite3.connect(database_path)) as connection:
        connection.row_factory = sqlite3.Row
        existing = _existing_import(connection, source_hash)
        if existing is not None and int(existing["is_active"]) == 1:
            return PaymentImportResult(
                payment_import_id=int(existing["payment_import_id"]),
                created=False,
                source_hash=source_hash,
                row_count=int(existing["row_count"]),
                valid_row_count=int(existing["valid_row_count"]),
                invalid_row_count=int(existing["invalid_row_count"]),
                first_payment_date=existing["first_payment_date"],
                last_payment_date=existing["last_payment_date"],
                backup_path=None,
                migrations_applied=migrations,
            )

    backup_path = create_database_backup(database_path, BACKUPS_PATH, "payment_import")
    modified_at = datetime.fromtimestamp(source_path.stat().st_mtime).astimezone().isoformat(timespec="seconds")
    row_count = sum(len(frame) for frame in frames.values())

    with closing(sqlite3.connect(database_path)) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            connection.execute("BEGIN IMMEDIATE")
            if existing is not None:
                connection.execute("UPDATE payment_imports SET is_active = 0 WHERE is_active = 1")
                connection.execute(
                    "UPDATE payment_imports SET is_active = 1 WHERE payment_import_id = ?",
                    (int(existing["payment_import_id"]),),
                )
                connection.commit()
                return PaymentImportResult(
                    payment_import_id=int(existing["payment_import_id"]),
                    created=False,
                    source_hash=source_hash,
                    row_count=int(existing["row_count"]),
                    valid_row_count=int(existing["valid_row_count"]),
                    invalid_row_count=int(existing["invalid_row_count"]),
                    first_payment_date=existing["first_payment_date"],
                    last_payment_date=existing["last_payment_date"],
                    backup_path=backup_path,
                    migrations_applied=migrations,
                )

            connection.execute("UPDATE payment_imports SET is_active = 0 WHERE is_active = 1")
            cursor = connection.execute(
                """
                INSERT INTO payment_imports(
                    source_file_path, source_file_name, source_hash,
                    source_modified_at, row_count, valid_row_count,
                    invalid_row_count, is_active, notes
                ) VALUES (?, ?, ?, ?, ?, 0, 0, 1, ?)
                """,
                (
                    _relative_source_path(source_path),
                    source_path.name,
                    source_hash,
                    modified_at,
                    row_count,
                    "CAT and SUB-CAT are preserved from H-P as the allocation source.",
                ),
            )
            import_id = int(cursor.lastrowid)
            valid_count = 0
            invalid_count = 0
            payment_dates: list[str] = []

            raw_columns = ["payment_import_id", "source_id", "source_sheet", "source_row", *RAW_COLUMNS.values()]
            raw_sql = f"INSERT INTO payments_raw({', '.join(raw_columns)}) VALUES ({', '.join('?' for _ in raw_columns)})"
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
            payment_sql = f"INSERT INTO payments({', '.join(payment_columns)}) VALUES ({', '.join('?' for _ in payment_columns)})"

            for sheet, frame in frames.items():
                source_id = _insert_source(connection, import_id, source_path, source_hash, sheet)
                for offset, (_, row) in enumerate(frame.iterrows(), start=2):
                    raw_values = [import_id, source_id, sheet, offset]
                    raw_values.extend(normalize_text(row[column]) for column in RAW_COLUMNS.values())
                    connection.execute(raw_sql, raw_values)

                    payment = normalized_payment(import_id, source_id, sheet, offset, row)
                    if payment is None:
                        invalid_count += 1
                        _insert_issue(
                            connection,
                            payment_import_id=import_id,
                            issue_type="invalid_raw_row",
                            severity="warning",
                            source_id=source_id,
                            source_row=offset,
                            payment_id=None,
                            payment_key=None,
                            description=f"H-P {sheet}, fila {offset}: no contiene un pago normalizable.",
                        )
                        continue

                    payment_cursor = connection.execute(
                        payment_sql,
                        [payment[column] for column in payment_columns],
                    )
                    payment_id = int(payment_cursor.lastrowid)
                    valid_count += 1
                    if payment["payment_date"]:
                        payment_dates.append(str(payment["payment_date"]))
                    if not payment["supplier_rut"]:
                        _insert_issue(
                            connection,
                            payment_import_id=import_id,
                            issue_type="missing_supplier_rut",
                            severity="warning",
                            source_id=source_id,
                            source_row=offset,
                            payment_id=payment_id,
                            payment_key=str(payment["payment_key"]),
                            description=f"H-P {sheet}, fila {offset}: pago sin RUT normalizado.",
                        )
                    if payment["cost_center_cat"] is None or payment["cost_center_sub_cat"] is None:
                        _insert_issue(
                            connection,
                            payment_import_id=import_id,
                            issue_type="missing_cost_center",
                            severity="warning",
                            source_id=source_id,
                            source_row=offset,
                            payment_id=payment_id,
                            payment_key=str(payment["payment_key"]),
                            description=f"H-P {sheet}, fila {offset}: pago sin CAT o SUB-CAT válido.",
                        )
                    if int(payment["invoice_has_split_marker"]):
                        _insert_issue(
                            connection,
                            payment_import_id=import_id,
                            issue_type="split_invoice_marker",
                            severity="info",
                            source_id=source_id,
                            source_row=offset,
                            payment_id=payment_id,
                            payment_key=str(payment["payment_key"]),
                            description=(
                                f"H-P {sheet}, fila {offset}: folio {payment['invoice_number_raw']} "
                                f"con base {payment['invoice_number_base']}."
                            ),
                        )

            first_date = min(payment_dates) if payment_dates else None
            last_date = max(payment_dates) if payment_dates else None
            connection.execute(
                """
                UPDATE payment_imports
                SET valid_row_count = ?, invalid_row_count = ?,
                    first_payment_date = ?, last_payment_date = ?
                WHERE payment_import_id = ?
                """,
                (valid_count, invalid_count, first_date, last_date, import_id),
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise

    return PaymentImportResult(
        payment_import_id=import_id,
        created=True,
        source_hash=source_hash,
        row_count=row_count,
        valid_row_count=valid_count,
        invalid_row_count=invalid_count,
        first_payment_date=first_date,
        last_payment_date=last_date,
        backup_path=backup_path,
        migrations_applied=migrations,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Importa una versión completa de H-P a C&C Lab.")
    parser.add_argument("source", nargs="?", type=Path, default=PAYMENTS_SOURCE_PATH)
    parser.add_argument("--database", type=Path, default=DATABASE_PATH)
    args = parser.parse_args()
    result = import_payments(args.source, args.database)
    action = "Importación creada" if result.created else "Archivo ya importado"
    print(f"{action}: {result.payment_import_id}")
    print(f"hash: {result.source_hash}")
    print(f"filas fuente: {result.row_count}")
    print(f"pagos normalizados: {result.valid_row_count}")
    print(f"filas no normalizables: {result.invalid_row_count}")
    print(f"periodo: {result.first_payment_date} a {result.last_payment_date}")
    if result.migrations_applied:
        print(f"migraciones: {', '.join(result.migrations_applied)}")
    if result.backup_path:
        print(f"respaldo: {result.backup_path}")


if __name__ == "__main__":
    main()
