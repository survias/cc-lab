from __future__ import annotations

import argparse
import shutil
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from utils.config import DATABASE_PATH, ORIGINAL_DATABASE_PATH


def rebuild_database(refresh: bool = False) -> None:
    if not ORIGINAL_DATABASE_PATH.exists():
        raise FileNotFoundError(f"No existe la base original: {ORIGINAL_DATABASE_PATH}")

    DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
    if DATABASE_PATH.exists() and not refresh:
        print(f"La copia ya existe: {DATABASE_PATH}")
        print("Usa --refresh para reemplazarla conservando un respaldo.")
        return

    if DATABASE_PATH.exists():
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_path = DATABASE_PATH.with_name(f"cc_lab_{timestamp}.sqlite.bak")
        shutil.copy2(DATABASE_PATH, backup_path)
        print(f"Respaldo creado: {backup_path}")

    shutil.copy2(ORIGINAL_DATABASE_PATH, DATABASE_PATH)
    print(f"Copia reconstruida desde: {ORIGINAL_DATABASE_PATH}")
    print(f"Base activa de C&C Lab: {DATABASE_PATH}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Reconstruye la copia autocontenida de SQLite sin modificar el origen."
    )
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="Reemplaza la copia existente después de crear un respaldo.",
    )
    args = parser.parse_args()
    rebuild_database(refresh=args.refresh)


if __name__ == "__main__":
    main()
