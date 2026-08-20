from __future__ import annotations

import hashlib
import sqlite3
from contextlib import closing
from datetime import datetime
from pathlib import Path

from utils.config import (
    BACKUPS_PATH,
    DATABASE_PATH,
    MIGRATIONS_PATH,
    TRANSACTION_BACKUP_RETENTION,
)


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def create_database_backup(
    database_path: Path = DATABASE_PATH,
    backup_dir: Path = BACKUPS_PATH,
    reason: str = "write",
) -> Path:
    backup_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    backup_path = backup_dir / f"cc_lab_before_{reason}_{timestamp}.sqlite"
    with closing(sqlite3.connect(database_path)) as source, closing(
        sqlite3.connect(backup_path)
    ) as destination:
        source.backup(destination)
    if TRANSACTION_BACKUP_RETENTION > 0:
        backups = sorted(
            backup_dir.glob("cc_lab_before_*.sqlite"),
            key=lambda path: path.stat().st_mtime_ns,
            reverse=True,
        )
        for expired in backups[TRANSACTION_BACKUP_RETENTION:]:
            expired.unlink()
    return backup_path


def _split_sql_statements(sql: str) -> list[str]:
    statements: list[str] = []
    buffer = ""
    for line in sql.splitlines(keepends=True):
        buffer += line
        if sqlite3.complete_statement(buffer):
            statement = buffer.strip()
            if statement:
                statements.append(statement)
            buffer = ""
    if buffer.strip():
        raise ValueError("La migración contiene una sentencia SQL incompleta.")
    return statements


def applied_migrations(database_path: Path = DATABASE_PATH) -> set[str]:
    with closing(sqlite3.connect(database_path)) as connection:
        exists = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'schema_migrations'"
        ).fetchone()
        if not exists:
            return set()
        return {
            str(row[0])
            for row in connection.execute("SELECT migration_id FROM schema_migrations")
        }


def apply_pending_migrations(
    database_path: Path = DATABASE_PATH,
    migrations_path: Path = MIGRATIONS_PATH,
    backup_dir: Path = BACKUPS_PATH,
) -> list[str]:
    migration_files = sorted(migrations_path.glob("[0-9][0-9][0-9]_*.sql"))
    already_applied = applied_migrations(database_path)
    pending = [path for path in migration_files if path.stem not in already_applied]
    if not pending:
        return []

    create_database_backup(database_path, backup_dir, "migration")
    applied: list[str] = []
    with closing(sqlite3.connect(database_path)) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            connection.execute("BEGIN IMMEDIATE")
            for migration_path in pending:
                sql = migration_path.read_text(encoding="utf-8")
                for statement in _split_sql_statements(sql):
                    connection.execute(statement)
                connection.execute(
                    "INSERT INTO schema_migrations(migration_id, file_hash) VALUES (?, ?)",
                    (migration_path.stem, file_sha256(migration_path)),
                )
                applied.append(migration_path.stem)
            connection.commit()
        except Exception:
            connection.rollback()
            raise
    return applied
