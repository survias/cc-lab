from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
from contextlib import closing
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from utils.config import (  # noqa: E402
    ACTIVE_CONTRACTS_PATH,
    APPLY_MIGRATIONS_ON_STARTUP,
    BACKUPS_PATH,
    BIDDING_PATH,
    BUDGET_TEMPLATE_PATH,
    CONTRACT_INVOICES_PATH,
    DATABASE_PATH,
    MIGRATIONS_PATH,
    REQUIRE_PERSISTENT_STORAGE,
    REVENUES_PATH,
)
from utils.migrations import applied_migrations  # noqa: E402


def main() -> None:
    if not DATABASE_PATH.is_file():
        raise SystemExit(f"No existe la base maestra: {DATABASE_PATH}")

    if REQUIRE_PERSISTENT_STORAGE and not os.environ.get("CC_LAB_DATABASE_PATH"):
        raise SystemExit("Producción exige definir CC_LAB_DATABASE_PATH en un volumen persistente.")

    if DATABASE_PATH.stat().st_mode & 0o222 == 0:
        raise SystemExit(f"La base debe tener permisos de lectura y escritura: {DATABASE_PATH}")

    with closing(sqlite3.connect(f"file:{DATABASE_PATH.resolve().as_posix()}?mode=rw", uri=True)) as connection:
        integrity = str(connection.execute("PRAGMA quick_check").fetchone()[0])
    if integrity != "ok":
        raise SystemExit(f"SQLite no superó quick_check: {integrity}")

    if REQUIRE_PERSISTENT_STORAGE:
        BACKUPS_PATH.mkdir(parents=True, exist_ok=True)
        try:
            with tempfile.NamedTemporaryFile(
                dir=BACKUPS_PATH, prefix=".write-test-", delete=True
            ):
                pass
        except OSError as exc:
            raise SystemExit(f"El directorio de respaldos no es escribible: {BACKUPS_PATH}") from exc

    required_files = [
        BIDDING_PATH,
        REVENUES_PATH,
        ACTIVE_CONTRACTS_PATH,
        CONTRACT_INVOICES_PATH,
        BUDGET_TEMPLATE_PATH,
    ]
    missing = [str(path) for path in required_files if not path.is_file()]
    if missing:
        raise SystemExit(f"Faltan archivos de referencia requeridos: {missing}")

    available = {path.stem for path in MIGRATIONS_PATH.glob("[0-9][0-9][0-9]_*.sql")}
    pending = sorted(available - applied_migrations(DATABASE_PATH))
    if pending and not APPLY_MIGRATIONS_ON_STARTUP:
        raise SystemExit(f"Hay migraciones pendientes y el arranque automático está desactivado: {pending}")

    print(f"database: {DATABASE_PATH}")
    print(f"database_size_mb: {DATABASE_PATH.stat().st_size / 1_048_576:.1f}")
    print(f"backup_dir: {BACKUPS_PATH}")
    print("sqlite_quick_check: ok")
    print(f"pending_migrations: {pending}")


if __name__ == "__main__":
    main()
