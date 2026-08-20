from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from utils.database import database_integrity, missing_required_tables, query_scalar
from utils.construction_data import ConstructionFilters, get_construction_metrics
from utils.legacy_data import load_cost_control, load_payments, source_inventory
from utils.payment_data import get_active_payment_summary
from utils.uf_data import get_uf_coverage


def main() -> None:
    integrity = database_integrity()
    missing = missing_required_tables()
    document_count = int(query_scalar("SELECT COUNT(*) FROM documents") or 0)
    issue_count = int(query_scalar("SELECT COUNT(*) FROM validation_issues") or 0)
    cost_control = load_cost_control()
    payments = load_payments()
    inventory = source_inventory()
    construction = get_construction_metrics(ConstructionFilters())
    active_payment_summary = get_active_payment_summary()
    uf_coverage = get_uf_coverage()

    print(f"integrity_check: {integrity}")
    print(f"documents: {document_count}")
    print(f"validation_issues: {issue_count}")
    print(f"missing_required_tables: {sorted(missing)}")
    print(f"master_ledger_rows: {len(cost_control)}")
    print(f"active_payment_rows: {len(payments)}")
    print(f"active_payment_summary: {active_payment_summary}")
    print(f"payment_statuses: {cost_control['PAYMENT_STATUS'].value_counts().to_dict()}")
    print(f"uf_coverage: {uf_coverage['earliest_date']} to {uf_coverage['latest_date']}")
    print(f"uf_missing_dates: {uf_coverage['missing_date_count']}")
    print(f"missing_sources: {inventory.loc[~inventory['Disponible'], 'Archivo'].tolist()}")
    print(f"construction_items: {int(construction['item_count'])}")
    print(f"construction_reports: {int(query_scalar('SELECT COUNT(DISTINCT report_no) FROM construction_cost_items') or 0)}")
    print(f"construction_without_folio: {int(construction['without_folio_count'])}")
    print(f"construction_net_clp: {construction['net_amount_clp']}")
    print(f"construction_total_uf: {construction['total_amount_uf']}")

    if (
        integrity != "ok"
        or missing
        or document_count < 24_607
        or len(cost_control) < document_count
        or len(payments) < 14_201
        or int(active_payment_summary.get("payment_count", 0)) < 14_201
        or int(uf_coverage["rate_count"]) < 2_000
        or int(uf_coverage["missing_date_count"]) != 0
        or cost_control["UF-F"].isna().any()
        or not inventory["Disponible"].all()
        or int(construction["item_count"]) != 824
        or int(construction["without_folio_count"]) != 86
        or abs(float(construction["net_amount_clp"]) - 308_496_231_065.45) > 0.05
        or abs(float(construction["total_amount_uf"]) - 9_018_192.9411) > 0.0002
    ):
        raise SystemExit("La validación de la copia SQLite falló.")


if __name__ == "__main__":
    main()
