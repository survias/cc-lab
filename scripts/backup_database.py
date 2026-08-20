from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from utils.config import (  # noqa: E402
    DATABASE_PATH,
    EXTERNAL_BACKUP_PATH,
    EXTERNAL_BACKUP_RETENTION,
)


def create_external_backup(source: Path, destination: Path, retention: int) -> Path:
    if not source.is_file():
        raise FileNotFoundError(f"No existe la base maestra: {source}")
    destination.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    final_path = destination / f"cc_lab_{timestamp}.sqlite"
    temporary_path = destination / f".{final_path.name}.tmp"

    with closing(sqlite3.connect(f"file:{source.resolve().as_posix()}?mode=ro", uri=True)) as origin, closing(
        sqlite3.connect(temporary_path)
    ) as target:
        origin.backup(target)

    with closing(sqlite3.connect(f"file:{temporary_path.resolve().as_posix()}?mode=ro", uri=True)) as check:
        integrity = str(check.execute("PRAGMA integrity_check").fetchone()[0])
    if integrity != "ok":
        temporary_path.unlink(missing_ok=True)
        raise RuntimeError(f"El respaldo no superó integrity_check: {integrity}")

    os.replace(temporary_path, final_path)
    backups = sorted(
        destination.glob("cc_lab_*.sqlite"),
        key=lambda path: path.stat().st_mtime_ns,
        reverse=True,
    )
    for expired in backups[max(1, retention):]:
        expired.unlink()
    return final_path


def main() -> None:
    parser = argparse.ArgumentParser(description="Crea y rota un respaldo externo íntegro de C&C Lab.")
    parser.add_argument("--database", type=Path, default=DATABASE_PATH)
    parser.add_argument("--destination", type=Path, default=EXTERNAL_BACKUP_PATH)
    parser.add_argument("--retention", type=int, default=EXTERNAL_BACKUP_RETENTION)
    args = parser.parse_args()

    backup = create_external_backup(args.database, args.destination, max(1, args.retention))
    print(f"backup: {backup}")
    print(f"size_mb: {backup.stat().st_size / 1_048_576:.1f}")
    print("integrity_check: ok")


if __name__ == "__main__":
    main()
