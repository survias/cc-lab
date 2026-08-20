from __future__ import annotations

import hashlib
import sqlite3
import xml.etree.ElementTree as ET
from collections import defaultdict
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from utils.config import CREDIT_NOTE_XML_PATH, DATABASE_PATH
from utils.database import query_dataframe
from utils.migrations import create_database_backup, file_sha256


DIRECT_DOCUMENT_TYPES = {"33", "34", "39", "46", "56"}


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _child(node: ET.Element | None, name: str) -> ET.Element | None:
    if node is None:
        return None
    return next((child for child in list(node) if _local_name(child.tag) == name), None)


def _text(node: ET.Element | None, name: str) -> str:
    child = _child(node, name)
    return (child.text or "").strip() if child is not None else ""


def _number(value: str) -> float:
    try:
        return float(str(value or "0").replace(".", "").replace(",", "."))
    except ValueError:
        return 0.0


def _key(value: object) -> str:
    text = "".join(character for character in str(value or "").strip().upper() if character.isalnum())
    return text.lstrip("0") or "0"


def _rut_number(value: str) -> str:
    return _key(str(value).split("-", 1)[0])


def parse_credit_note_xml(path: Path) -> dict:
    root = ET.parse(path).getroot()
    document = _child(root, "Documento")
    header = _child(document, "Encabezado")
    id_doc = _child(header, "IdDoc")
    issuer = _child(header, "Emisor")
    totals = _child(header, "Totales")
    references = []
    for line, reference in enumerate(
        [child for child in list(document) if _local_name(child.tag) == "Referencia"], 1
    ):
        references.append(
            {
                "line": line,
                "document_type": _text(reference, "TpoDocRef"),
                "folio": _text(reference, "FolioRef"),
                "date": _text(reference, "FchRef"),
                "code": _text(reference, "CodRef"),
                "reason": _text(reference, "RazonRef"),
            }
        )
    return {
        "supplier_rut": _rut_number(_text(issuer, "RUTEmisor")),
        "supplier_dv": str(_text(issuer, "RUTEmisor").split("-")[-1]),
        "supplier_name": _text(issuer, "RznSoc"),
        "document_type": int(_text(id_doc, "TipoDTE") or 61),
        "document_number": _text(id_doc, "Folio"),
        "issue_date": _text(id_doc, "FchEmis"),
        "exempt_amount_clp": _number(_text(totals, "MntExe")),
        "net_amount_clp": _number(_text(totals, "MntNeto")),
        "recoverable_vat_clp": _number(_text(totals, "IVA")),
        "total_amount_clp": _number(_text(totals, "MntTotal")),
        "references": references,
    }


def _classification(reference: dict, credit_total: float, invoice_total: float | None) -> tuple[str, str]:
    document_type = reference["document_type"]
    folio = _key(reference["folio"])
    if document_type in DIRECT_DOCUMENT_TYPES and folio == "0":
        return "GLOBAL_ADJUSTMENT", "LOW"
    if document_type not in DIRECT_DOCUMENT_TYPES:
        return "NON_INVOICE_REFERENCE", "LOW"
    if invoice_total is None:
        return "REFERENCE_NOT_FOUND", "MEDIUM"
    if abs(abs(credit_total) - abs(invoice_total)) <= 1:
        return "FULL_CANCELLATION", "HIGH"
    return "PARTIAL_CREDIT", "HIGH"


def import_credit_note_xml_folder(
    folder: Path = CREDIT_NOTE_XML_PATH,
    database_path: Path = DATABASE_PATH,
) -> dict:
    folder = Path(folder).expanduser()
    files = sorted(folder.glob("*.xml"))
    if not files:
        raise FileNotFoundError(f"No se encontraron XML en {folder}")
    create_database_backup(database_path, database_path.parent / "backups", "credit_note_xml")
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    imported_files = new_documents = references = auto_linked = 0
    skipped_files = parse_errors = 0
    with closing(sqlite3.connect(database_path)) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 5000")
        try:
            connection.execute("BEGIN IMMEDIATE")
            folder_hash = hashlib.sha256("".join(file_sha256(path) for path in files).encode()).hexdigest()
            source = connection.execute(
                "SELECT source_id FROM sources WHERE source_type = 'XML_RECEIVED_CREDIT_NOTES' AND file_hash = ?",
                (folder_hash,),
            ).fetchone()
            if source:
                source_id = int(source[0])
            else:
                source_id = int(
                    connection.execute(
                        """
                        INSERT INTO sources(source_area, source_type, source_file_path, source_file_name,
                                            source_period, company_rut, file_hash, imported_at, notes)
                        VALUES ('SII', 'XML_RECEIVED_CREDIT_NOTES', ?, ?, 'HISTORICO', '77337752-9', ?, ?, ?)
                        """,
                        (str(folder), folder.name, folder_hash, now, f"{len(files)} XML de notas de crédito recibidas"),
                    ).lastrowid
                )

            existing_files = {
                str(row[0]): int(row[1])
                for row in connection.execute(
                    "SELECT file_hash, xml_file_id FROM credit_note_xml_files"
                )
            }
            document_rows = connection.execute(
                """
                SELECT document_id, supplier_rut, document_type, document_number, total_amount_clp
                FROM documents
                """
            ).fetchall()
            target_by_key = {
                (_key(row[1]), str(row[2]), _key(row[3])): row
                for row in document_rows
            }
            for position, path in enumerate(files, 1):
                digest = file_sha256(path)
                if digest in existing_files:
                    skipped_files += 1
                    continue
                try:
                    parsed = parse_credit_note_xml(path)
                    document_key = f"{parsed['supplier_rut']}|61|{_key(parsed['document_number'])}"
                    current = target_by_key.get(
                        (parsed["supplier_rut"], "61", _key(parsed["document_number"]))
                    )
                    if current is None:
                        document_id = int(
                            connection.execute(
                                """
                                INSERT INTO documents(
                                    document_key, supplier_rut, supplier_dv, supplier_name,
                                    document_type, document_number, issue_date, source_period,
                                    exempt_amount_clp, net_amount_clp, recoverable_vat_clp,
                                    total_amount_clp, purchase_type, source_id, source_row,
                                    quality_status
                                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'COMPRA', ?, ?, 'ok')
                                """,
                                (
                                    document_key, parsed["supplier_rut"], parsed["supplier_dv"],
                                    parsed["supplier_name"], parsed["document_type"],
                                    parsed["document_number"], parsed["issue_date"],
                                    parsed["issue_date"][:7].replace("-", ""),
                                    parsed["exempt_amount_clp"], parsed["net_amount_clp"],
                                    parsed["recoverable_vat_clp"], parsed["total_amount_clp"],
                                    source_id, position,
                                ),
                            ).lastrowid
                        )
                        current = (
                            document_id, parsed["supplier_rut"], 61,
                            parsed["document_number"], parsed["total_amount_clp"],
                        )
                        target_by_key[(parsed["supplier_rut"], "61", _key(parsed["document_number"]))] = current
                        new_documents += 1
                    document_id = int(current[0])
                    xml_file_id = int(
                        connection.execute(
                            """
                            INSERT INTO credit_note_xml_files(
                                source_id, file_path, file_name, file_hash,
                                credit_note_document_id, parsed_at, parse_status
                            ) VALUES (?, ?, ?, ?, ?, ?, 'parsed')
                            """,
                            (source_id, str(path), path.name, digest, document_id, now),
                        ).lastrowid
                    )
                    direct_refs = [ref for ref in parsed["references"] if ref["document_type"] in DIRECT_DOCUMENT_TYPES]
                    matched_direct_refs = []
                    for ref in parsed["references"]:
                        target = target_by_key.get(
                            (parsed["supplier_rut"], ref["document_type"], _key(ref["folio"]))
                        )
                        target_id = int(target[0]) if target else None
                        classification, confidence = _classification(
                            ref, parsed["total_amount_clp"], float(target[4]) if target else None
                        )
                        if target_id and ref["document_type"] in DIRECT_DOCUMENT_TYPES:
                            method = "RUT+TIPO+FOLIO"
                            matched_direct_refs.append((ref, target_id, classification, confidence))
                        elif ref["document_type"] in DIRECT_DOCUMENT_TYPES:
                            method = "REFERENCIA_NO_ENCONTRADA"
                        else:
                            method = "REFERENCIA_NO_DOCUMENTAL"
                        reference_id = int(
                            connection.execute(
                                """
                                INSERT INTO credit_note_xml_references(
                                    xml_file_id, credit_note_document_id, reference_line,
                                    referenced_document_type, referenced_folio, reference_date,
                                    reference_code, reference_reason, matched_document_id,
                                    match_method, classification, confidence
                                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                                """,
                                (
                                    xml_file_id, document_id, ref["line"], ref["document_type"],
                                    ref["folio"], ref["date"], ref["code"], ref["reason"],
                                    target_id, method, classification, confidence,
                                ),
                            ).lastrowid
                        )
                        references += 1
                    if len(matched_direct_refs) == 1 and len(direct_refs) == 1:
                        ref, target_id, classification, confidence = matched_direct_refs[0]
                        connection.execute(
                            """
                            INSERT INTO credit_note_decisions(
                                credit_note_id, decision_type, invoice_document_id,
                                allocated_amount_clp, notes, created_at, updated_at,
                                source_reference_id, classification, match_method
                            ) VALUES (?, 'LINKED', ?, ?, ?, ?, ?,
                                      (SELECT reference_id FROM credit_note_xml_references
                                       WHERE xml_file_id = ? AND reference_line = ?), ?, 'RUT+TIPO+FOLIO')
                            ON CONFLICT(credit_note_id) DO UPDATE SET
                                decision_type = excluded.decision_type,
                                invoice_document_id = excluded.invoice_document_id,
                                allocated_amount_clp = excluded.allocated_amount_clp,
                                notes = excluded.notes,
                                updated_at = excluded.updated_at,
                                source_reference_id = excluded.source_reference_id,
                                classification = excluded.classification,
                                match_method = excluded.match_method
                            WHERE credit_note_decisions.source_reference_id IS NULL
                            """,
                            (
                                document_id, target_id, abs(parsed["total_amount_clp"]),
                                f"Asociación automática desde XML · CodRef {ref['code']} · {classification}",
                                now, now, xml_file_id, ref["line"], classification,
                            ),
                        )
                        auto_linked += 1
                    imported_files += 1
                except Exception as exc:
                    parse_errors += 1
                    connection.execute(
                        """
                        INSERT INTO credit_note_xml_files(
                            source_id, file_path, file_name, file_hash, parsed_at,
                            parse_status, parse_error
                        ) VALUES (?, ?, ?, ?, ?, 'error', ?)
                        """,
                        (source_id, str(path), path.name, digest, now, str(exc)),
                    )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
    return {
        "files": len(files),
        "imported_files": imported_files,
        "skipped_files": skipped_files,
        "new_documents": new_documents,
        "references": references,
        "auto_linked": auto_linked,
        "parse_errors": parse_errors,
        "folder": str(folder),
    }


def get_credit_note_xml_summary(database_path: Path = DATABASE_PATH):
    return query_dataframe(
        """
        SELECT COUNT(*) AS xml_files,
               SUM(CASE WHEN parse_status = 'parsed' THEN 1 ELSE 0 END) AS parsed_files,
               SUM(CASE WHEN parse_status = 'error' THEN 1 ELSE 0 END) AS errors
        FROM credit_note_xml_files
        """,
        path=database_path,
    ).iloc[0].to_dict()
