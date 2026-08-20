from __future__ import annotations

import argparse
import re
import sqlite3
import sys
import unicodedata
from collections import Counter, defaultdict
from contextlib import closing
from dataclasses import dataclass
from datetime import date
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.import_construction_costs import normalize_invoice_key
from utils.config import BACKUPS_PATH, DATABASE_PATH, MIGRATIONS_PATH
from utils.migrations import apply_pending_migrations, create_database_backup


AMOUNT_TOLERANCE_CLP = 5.0
SYSTEM_USER = "SYSTEM_RULES_V1"
INVOICE_DOCUMENT_TYPES = (33, 34)

MANUAL_ALIASES = {
    "CHINA RAILWAY CONSTRUCCION CORPORATION INTERNATIONAL LIMITED": (
        "59296220",
        "9",
        "CHINA RAILWAY CONSTRUCTION CORPORATION (INTERNATIONAL) LIMITED",
    ),
    "MINISTERIO DE OBRAS PUBLICAS DE CHILE MOP": (
        "61202000",
        "0",
        "MINISTERIO DE OBRAS PUBLICAS",
    ),
}


@dataclass(frozen=True)
class ReconciliationResult:
    item_count: int
    aliases: int
    exact_documents: int
    partial_documents: int
    exact_payments: int
    review_required: int
    aggregate_support: int
    pending_review: int
    backup_path: Path | None
    migrations_applied: tuple[str, ...]


def normalize_name(value: object) -> str:
    if value is None:
        return ""
    text = unicodedata.normalize("NFKD", str(value).upper())
    text = "".join(character for character in text if not unicodedata.combining(character))
    return " ".join(re.sub(r"[^A-Z0-9]+", " ", text).split())


def _amounts_match(left: float, right: float) -> bool:
    return abs(float(left or 0) - float(right or 0)) <= AMOUNT_TOLERANCE_CLP


def _document_cost(row: sqlite3.Row) -> float:
    return float(row["net_amount_clp"] or 0) + float(row["exempt_amount_clp"] or 0)


def _payment_amounts(row: sqlite3.Row) -> tuple[float, ...]:
    values = {
        float(row["paid_amount_clp"] or 0),
        float(row["gross_amount_clp"] or 0),
        float(row["net_amount_clp"] or 0) + float(row["exempt_amount_clp"] or 0),
    }
    return tuple(value for value in values if value > 0)


def _date_distance(left: object, right: object) -> int:
    if not left or not right:
        return 999999
    try:
        return abs((date.fromisoformat(str(left)[:10]) - date.fromisoformat(str(right)[:10])).days)
    except ValueError:
        return 999999


def _dominant_rut(counter: Counter[str]) -> str | None:
    if not counter:
        return None
    ranked = counter.most_common()
    if len(ranked) == 1:
        return ranked[0][0]
    return ranked[0][0] if ranked[0][1] >= ranked[1][1] * 3 else None


def _canonical_supplier(
    rut: str,
    documents: list[sqlite3.Row],
    payments: list[sqlite3.Row],
) -> tuple[str | None, str | None]:
    names: Counter[str] = Counter()
    dvs: Counter[str] = Counter()
    for row in [*documents, *payments]:
        if str(row["supplier_rut"] or "") != rut:
            continue
        name = str(row["supplier_name"] or "").strip()
        dv = str(row["supplier_dv"] or "").strip().upper()
        if name:
            names[name] += 1
        if dv:
            dvs[dv] += 1
    return (names.most_common(1)[0][0] if names else None, dvs.most_common(1)[0][0] if dvs else None)


def _build_aliases(
    items: list[sqlite3.Row],
    documents: list[sqlite3.Row],
    payments: list[sqlite3.Row],
) -> dict[str, tuple[str, str | None, str | None, str, float]]:
    source_ruts: dict[str, Counter[str]] = defaultdict(Counter)
    for row in [*documents, *payments]:
        name_key = normalize_name(row["supplier_name"])
        rut = str(row["supplier_rut"] or "").strip()
        if name_key and rut:
            source_ruts[name_key][rut] += 1

    docs_by_folio: dict[str, list[sqlite3.Row]] = defaultdict(list)
    for row in documents:
        folio = normalize_invoice_key(row["document_number"])
        if folio:
            docs_by_folio[folio].append(row)

    folio_amount_evidence: dict[str, Counter[str]] = defaultdict(Counter)
    for item in items:
        folio = str(item["invoice_key"] or "")
        if not folio:
            continue
        item_net = float(item["net_amount_clp"] or 0)
        item_total = float(item["total_amount_clp"] or 0)
        exact_ruts = {
            str(document["supplier_rut"])
            for document in docs_by_folio.get(folio, [])
            if _amounts_match(item_net, _document_cost(document))
            or _amounts_match(item_total, float(document["total_amount_clp"] or 0))
        }
        if len(exact_ruts) == 1:
            folio_amount_evidence[normalize_name(item["supplier_name_reported"])][exact_ruts.pop()] += 1

    aliases: dict[str, tuple[str, str | None, str | None, str, float]] = {}
    for item in items:
        reported = str(item["supplier_name_reported"] or "").strip()
        name_key = normalize_name(reported)
        if not reported or name_key in aliases:
            continue

        if name_key in MANUAL_ALIASES:
            rut, dv, canonical = MANUAL_ALIASES[name_key]
            aliases[name_key] = (rut, dv, canonical, "MANUAL_KNOWN", 1.0)
            continue

        rut = _dominant_rut(source_ruts.get(name_key, Counter()))
        source = "NORMALIZED_NAME"
        confidence = 0.96
        if rut is None:
            rut = _dominant_rut(folio_amount_evidence.get(name_key, Counter()))
            source = "FOLIO_AMOUNT_EVIDENCE"
            confidence = 0.99
        if rut is None:
            continue

        canonical, dv = _canonical_supplier(rut, documents, payments)
        aliases[name_key] = (rut, dv, canonical, source, confidence)
    return aliases


def _is_payroll_aggregate(item: sqlite3.Row) -> bool:
    supplier = normalize_name(item["supplier_name_reported"])
    description = normalize_name(item["description"])
    return (
        "SOCIEDAD CONCESIONARIA RUTA 5 TALCA CHILLAN" in supplier
        and any(word in description for word in ("REMUNER", "SALARIO", "PERSONAL", "SUELDO"))
    )


def _insert_match(
    connection: sqlite3.Connection,
    item: sqlite3.Row,
    *,
    document_id: int | None = None,
    payment_id: int | None = None,
    match_type: str,
    method: str,
    score: float,
    allocated_clp: float,
    allocated_uf: float,
    percentage: float,
    status: str,
    notes: str,
) -> None:
    connection.execute(
        """
        INSERT INTO construction_cost_matches(
            construction_item_id, document_id, payment_id, match_type,
            match_method, match_score, allocated_amount_clp, allocated_amount_uf,
            allocation_percentage, match_status, confirmed_by, confirmed_at, notes
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, ?)
        """,
        (
            int(item["construction_item_id"]),
            document_id,
            payment_id,
            match_type,
            method,
            score,
            allocated_clp,
            allocated_uf,
            percentage,
            status,
            SYSTEM_USER,
            notes,
        ),
    )


def reconcile_construction_costs(
    database_path: Path = DATABASE_PATH,
    migrations_path: Path = MIGRATIONS_PATH,
    backup_dir: Path = BACKUPS_PATH,
    create_backup: bool = True,
) -> ReconciliationResult:
    migrations = apply_pending_migrations(database_path, migrations_path, backup_dir)
    backup_path = (
        create_database_backup(database_path, backup_dir, "construction_reconciliation")
        if create_backup
        else None
    )

    with closing(sqlite3.connect(database_path)) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        items = connection.execute(
            """
            SELECT i.*
            FROM construction_cost_items i
            JOIN construction_imports ci
              ON ci.construction_import_id = i.construction_import_id
            WHERE ci.is_active = 1
            ORDER BY i.construction_item_id
            """
        ).fetchall()
        documents = connection.execute(
            f"""
            SELECT *
            FROM documents
            WHERE document_type IN ({','.join('?' for _ in INVOICE_DOCUMENT_TYPES)})
            """,
            INVOICE_DOCUMENT_TYPES,
        ).fetchall()
        payments = connection.execute(
            """
            SELECT p.*
            FROM payments p
            JOIN payment_imports pi ON pi.payment_import_id = p.payment_import_id
            WHERE pi.is_active = 1
            """
        ).fetchall()

        aliases = _build_aliases(items, documents, payments)
        docs_by_rut_folio: dict[tuple[str, str], list[sqlite3.Row]] = defaultdict(list)
        docs_by_folio: dict[str, list[sqlite3.Row]] = defaultdict(list)
        for row in documents:
            folio = normalize_invoice_key(row["document_number"])
            if not folio:
                continue
            rut = str(row["supplier_rut"] or "")
            docs_by_rut_folio[(rut, folio)].append(row)
            docs_by_folio[folio].append(row)

        payments_by_rut: dict[str, list[sqlite3.Row]] = defaultdict(list)
        payments_by_name: dict[str, list[sqlite3.Row]] = defaultdict(list)
        for row in payments:
            rut = str(row["supplier_rut"] or "")
            if rut:
                payments_by_rut[rut].append(row)
            name_key = normalize_name(row["supplier_name"])
            if name_key:
                payments_by_name[name_key].append(row)

        counts = Counter()
        try:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "DELETE FROM construction_cost_matches WHERE confirmed_by = ?",
                (SYSTEM_USER,),
            )
            connection.execute("DELETE FROM construction_supplier_aliases")

            alias_rows: dict[str, tuple[str, str | None, str | None, str, float]] = {}
            for item in items:
                reported = str(item["supplier_name_reported"] or "").strip()
                key = normalize_name(reported)
                if reported and key in aliases:
                    alias_rows[reported] = aliases[key]
            connection.executemany(
                """
                INSERT INTO construction_supplier_aliases(
                    supplier_name_reported, supplier_name_key, supplier_rut, supplier_dv,
                    canonical_name, alias_source, confidence
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (reported, normalize_name(reported), *values)
                    for reported, values in sorted(alias_rows.items())
                ],
            )

            for item in items:
                item_id = int(item["construction_item_id"])
                name_key = normalize_name(item["supplier_name_reported"])
                alias = aliases.get(name_key)
                rut = alias[0] if alias else None
                folio = str(item["invoice_key"] or "")
                item_net = float(item["net_amount_clp"] or 0)
                item_total = float(item["total_amount_clp"] or 0)
                item_net_uf = float(item["net_amount_uf"] or 0)

                if _is_payroll_aggregate(item):
                    connection.execute(
                        """
                        UPDATE construction_cost_items
                        SET support_type = 'REMUNERATION',
                            reconciliation_status = 'AGGREGATE_SUPPORT',
                            normalized_at = CURRENT_TIMESTAMP
                        WHERE construction_item_id = ?
                        """,
                        (item_id,),
                    )
                    counts["aggregate_support"] += 1
                    continue

                document_candidates = (
                    docs_by_rut_folio.get((rut, folio), [])
                    if rut and folio
                    else docs_by_folio.get(folio, []) if folio else []
                )
                same_date_candidates = [
                    row
                    for row in document_candidates
                    if str(row["issue_date"] or "")[:10] == str(item["issue_date"] or "")[:10]
                ]
                if same_date_candidates:
                    document_candidates = same_date_candidates
                exact_documents = [
                    row
                    for row in document_candidates
                    if _amounts_match(item_net, _document_cost(row))
                    or _amounts_match(item_total, float(row["total_amount_clp"] or 0))
                ]

                if len(exact_documents) == 1:
                    document = exact_documents[0]
                    method = "DOCUMENT_RUT_FOLIO_AMOUNT_EXACT" if rut else "DOCUMENT_GLOBAL_FOLIO_AMOUNT_EXACT"
                    _insert_match(
                        connection,
                        item,
                        document_id=int(document["document_id"]),
                        match_type="DOCUMENT",
                        method=method,
                        score=1.0 if rut else 0.98,
                        allocated_clp=item_net,
                        allocated_uf=item_net_uf,
                        percentage=1.0,
                        status="CONFIRMED",
                        notes="Coincidencia exacta en CLP; la UF se conserva como presentación.",
                    )
                    connection.execute(
                        """
                        UPDATE construction_cost_items
                        SET support_type = 'SII_DOCUMENT',
                            reconciliation_status = 'MATCHED_EXACT',
                            normalized_at = CURRENT_TIMESTAMP
                        WHERE construction_item_id = ?
                        """,
                        (item_id,),
                    )
                    counts["exact_documents"] += 1
                    continue

                if len(exact_documents) > 1:
                    for document in exact_documents:
                        _insert_match(
                            connection,
                            item,
                            document_id=int(document["document_id"]),
                            match_type="DOCUMENT_CANDIDATE",
                            method="DOCUMENT_AMBIGUOUS_EXACT",
                            score=0.6,
                            allocated_clp=0,
                            allocated_uf=0,
                            percentage=0,
                            status="REVIEW_REQUIRED",
                            notes="Más de un documento coincide en folio y monto CLP.",
                        )
                    connection.execute(
                        """
                        UPDATE construction_cost_items
                        SET support_type = 'SII_DOCUMENT',
                            reconciliation_status = 'REVIEW_REQUIRED',
                            normalized_at = CURRENT_TIMESTAMP
                        WHERE construction_item_id = ?
                        """,
                        (item_id,),
                    )
                    counts["review_required"] += 1
                    continue

                partial_documents = [
                    row
                    for row in document_candidates
                    if _document_cost(row) > 0
                    and 0 < item_net < _document_cost(row) - AMOUNT_TOLERANCE_CLP
                ]
                if len(partial_documents) == 1:
                    document = partial_documents[0]
                    document_amount = _document_cost(document)
                    percentage = item_net / document_amount
                    _insert_match(
                        connection,
                        item,
                        document_id=int(document["document_id"]),
                        match_type="DOCUMENT_PARTIAL",
                        method="DOCUMENT_RUT_FOLIO_PARTIAL_CLP",
                        score=0.9,
                        allocated_clp=item_net,
                        allocated_uf=item_net_uf,
                        percentage=percentage,
                        status="PARTIAL",
                        notes=f"Monto presentado equivale al {percentage:.2%} del neto/exento del documento.",
                    )
                    connection.execute(
                        """
                        UPDATE construction_cost_items
                        SET support_type = 'SII_DOCUMENT',
                            reconciliation_status = 'MATCHED_PARTIAL',
                            normalized_at = CURRENT_TIMESTAMP
                        WHERE construction_item_id = ?
                        """,
                        (item_id,),
                    )
                    counts["partial_documents"] += 1
                    continue

                if document_candidates:
                    ranked = sorted(
                        document_candidates,
                        key=lambda row: min(
                            abs(item_net - _document_cost(row)),
                            abs(item_total - float(row["total_amount_clp"] or 0)),
                        ),
                    )
                    document = ranked[0]
                    _insert_match(
                        connection,
                        item,
                        document_id=int(document["document_id"]),
                        match_type="DOCUMENT_CANDIDATE",
                        method="DOCUMENT_RUT_FOLIO_AMOUNT_DIFFERENCE",
                        score=0.5,
                        allocated_clp=0,
                        allocated_uf=0,
                        percentage=0,
                        status="REVIEW_REQUIRED",
                        notes="El folio existe, pero el monto CLP presentado no coincide.",
                    )
                    connection.execute(
                        """
                        UPDATE construction_cost_items
                        SET support_type = 'SII_DOCUMENT',
                            reconciliation_status = 'REVIEW_REQUIRED',
                            normalized_at = CURRENT_TIMESTAMP
                        WHERE construction_item_id = ?
                        """,
                        (item_id,),
                    )
                    counts["review_required"] += 1
                    continue

                payment_candidates = payments_by_rut.get(rut, []) if rut else payments_by_name.get(name_key, [])
                payment_candidates = [
                    row
                    for row in payment_candidates
                    if any(
                        _amounts_match(item_net, amount) or _amounts_match(item_total, amount)
                        for amount in _payment_amounts(row)
                    )
                ]
                if payment_candidates:
                    ranked = sorted(
                        payment_candidates,
                        key=lambda row: (
                            _date_distance(item["issue_date"], row["payment_date"]),
                            int(row["payment_id"]),
                        ),
                    )
                    nearest_distance = _date_distance(item["issue_date"], ranked[0]["payment_date"])
                    nearest = [
                        row
                        for row in ranked
                        if _date_distance(item["issue_date"], row["payment_date"]) == nearest_distance
                    ]
                    if len(nearest) == 1 and nearest_distance <= 180:
                        payment = nearest[0]
                        support_type = "MOP_PAYMENT" if rut == "61202000" else "TRANSFER"
                        payment_basis = min(
                            _payment_amounts(payment),
                            key=lambda amount: min(abs(item_net - amount), abs(item_total - amount)),
                        )
                        percentage = item_net / payment_basis if payment_basis else 0
                        _insert_match(
                            connection,
                            item,
                            payment_id=int(payment["payment_id"]),
                            match_type="PAYMENT",
                            method="PAYMENT_RUT_AMOUNT_DATE_EXACT",
                            score=0.95,
                            allocated_clp=item_net,
                            allocated_uf=item_net_uf,
                            percentage=percentage,
                            status="CONFIRMED",
                            notes="Pago activo coincidente en CLP y fecha cercana.",
                        )
                        connection.execute(
                            """
                            UPDATE construction_cost_items
                            SET support_type = ?,
                                reconciliation_status = 'MATCHED_PAYMENT',
                                normalized_at = CURRENT_TIMESTAMP
                            WHERE construction_item_id = ?
                            """,
                            (support_type, item_id),
                        )
                        counts["exact_payments"] += 1
                        continue

                connection.execute(
                    """
                    UPDATE construction_cost_items
                    SET support_type = 'PENDING_CLASSIFICATION',
                        reconciliation_status = 'PENDING_REVIEW',
                        normalized_at = CURRENT_TIMESTAMP
                    WHERE construction_item_id = ?
                    """,
                    (item_id,),
                )
                counts["pending_review"] += 1

            connection.commit()
        except Exception:
            connection.rollback()
            raise

    return ReconciliationResult(
        item_count=len(items),
        aliases=len(alias_rows),
        exact_documents=counts["exact_documents"],
        partial_documents=counts["partial_documents"],
        exact_payments=counts["exact_payments"],
        review_required=counts["review_required"],
        aggregate_support=counts["aggregate_support"],
        pending_review=counts["pending_review"],
        backup_path=backup_path,
        migrations_applied=tuple(migrations),
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Reconstruye los vínculos de costos de construcción.")
    parser.add_argument("--database", type=Path, default=DATABASE_PATH)
    parser.add_argument("--no-backup", action="store_true")
    args = parser.parse_args()
    result = reconcile_construction_costs(
        database_path=args.database,
        backup_dir=args.database.parent / "backups" if args.database != DATABASE_PATH else BACKUPS_PATH,
        create_backup=not args.no_backup,
    )
    print(f"Registros: {result.item_count}")
    print(f"Alias de proveedor: {result.aliases}")
    print(f"Documentos exactos: {result.exact_documents}")
    print(f"Documentos parciales: {result.partial_documents}")
    print(f"Pagos exactos: {result.exact_payments}")
    print(f"Revisión requerida: {result.review_required}")
    print(f"Respaldo agregado: {result.aggregate_support}")
    print(f"Sin respaldo: {result.pending_review}")
    if result.backup_path:
        print(f"Respaldo: {result.backup_path}")


if __name__ == "__main__":
    main()
