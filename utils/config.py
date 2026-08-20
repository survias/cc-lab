from __future__ import annotations

import os
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
BD_PATH = PROJECT_ROOT / "BD"


def _env_path(name: str, default: Path) -> Path:
    value = os.environ.get(name, "").strip()
    return Path(value).expanduser() if value else default


def _env_flag(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    try:
        return max(0, int(os.environ.get(name, str(default))))
    except ValueError:
        return default


OPTIONAL_SOURCES_PATH = BD_PATH / "sources"
DATABASE_PATH = _env_path("CC_LAB_DATABASE_PATH", BD_PATH / "cc_lab.sqlite")
BACKUPS_PATH = _env_path("CC_LAB_BACKUP_DIR", DATABASE_PATH.parent / "backups")
EXTERNAL_BACKUP_PATH = _env_path(
    "CC_LAB_BACKUP_MIRROR_DIR", DATABASE_PATH.parent / "external-backups"
)
PAYMENTS_SOURCE_PATH = _env_path(
    "CC_LAB_PAYMENTS_SOURCE", OPTIONAL_SOURCES_PATH / "H-P.xlsx"
)
LEGACY_DATABASE2_PATH = _env_path(
    "CC_LAB_LEGACY_DATABASE2", OPTIONAL_SOURCES_PATH / "database2.csv"
)
SUPPLIERS_PATH = BD_PATH / "suppliers.csv"
BIDDING_PATH = BD_PATH / "bidding.csv"
REVENUES_PATH = BD_PATH / "revenues.csv"
BUDGET_TEMPLATE_PATH = BD_PATH / "Cost_vs_Budget_template.xlsx"
ACTIVE_CONTRACTS_PATH = BD_PATH / "00_Contracts.csv"
CONTRACT_INVOICES_PATH = BD_PATH / "01_Contracts-Invoice.csv"
IMAGES_PATH = PROJECT_ROOT / "images"
LOGO_PATH = IMAGES_PATH / "logo_solo-Photoroom.png"
MIGRATIONS_PATH = BD_PATH / "migrations"
CREDIT_NOTE_XML_PATH = _env_path(
    "CC_LAB_CREDIT_NOTE_XML_SOURCE", OPTIONAL_SOURCES_PATH / "credit_notes"
)
CONSTRUCTION_SOURCE_PATH = _env_path(
    "CC_LAB_CONSTRUCTION_SOURCE",
    OPTIONAL_SOURCES_PATH / "Consolidado Costos de Construccion - SPA.xlsx",
)
ORIGINAL_DATABASE_PATH = _env_path(
    "CC_LAB_ORIGINAL_DATABASE", OPTIONAL_SOURCES_PATH / "cc_lab_original.sqlite"
)

# Configuración de despliegue. Los valores seguros se activan explícitamente
# en producción; el desarrollo local conserva su flujo actual.
AUTH_REQUIRED = _env_flag("CC_LAB_AUTH_REQUIRED")
AUTH_PROVIDER = os.environ.get("CC_LAB_AUTH_PROVIDER", "microsoft").strip() or "microsoft"
ACCESS_PASSWORD = os.environ.get("CC_LAB_ACCESS_PASSWORD", "").strip()
ALLOWED_EMAIL_DOMAINS = tuple(
    domain.strip().lower().lstrip("@")
    for domain in os.environ.get("CC_LAB_ALLOWED_EMAIL_DOMAINS", "").split(",")
    if domain.strip()
)
APPLY_MIGRATIONS_ON_STARTUP = _env_flag("CC_LAB_APPLY_MIGRATIONS", True)
REQUIRE_PERSISTENT_STORAGE = _env_flag("CC_LAB_REQUIRE_PERSISTENT_STORAGE")
TRANSACTION_BACKUP_RETENTION = _env_int("CC_LAB_TRANSACTION_BACKUP_RETENTION", 0)
EXTERNAL_BACKUP_RETENTION = _env_int("CC_LAB_EXTERNAL_BACKUP_RETENTION", 30)

APPLICATION_NAME = "C&C Lab"
EXPECTED_TABLES = {
    "schema_migrations",
    "sources",
    "sii_rcv_raw",
    "documents",
    "validation_issues",
    "payment_imports",
    "payments_raw",
    "payments",
    "cost_centers",
    "review_decisions",
    "manual_matches",
    "uf_daily",
    "construction_imports",
    "construction_cost_items",
    "construction_cost_matches",
}

# Límites centralizados para la interfaz y futuras reglas de conciliación.
MAX_VISIBLE_ROWS = 25_000
RECONCILIATION_TOLERANCE_CLP = 1.0
