from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Sequence

import pandas as pd

from utils.config import DATABASE_PATH, EXPECTED_TABLES


class DatabaseUnavailableError(RuntimeError):
    """La copia de desarrollo no está disponible o no puede abrirse."""


def _readonly_uri(path: Path) -> str:
    return f"file:{path.resolve().as_posix()}?mode=ro"


@contextmanager
def readonly_connection(path: Path = DATABASE_PATH) -> Iterator[sqlite3.Connection]:
    if not path.exists():
        raise DatabaseUnavailableError(
            f"No existe la base de C&C Lab: {path}. "
            "Ejecuta scripts/rebuild_database.py."
        )

    try:
        connection = sqlite3.connect(_readonly_uri(path), uri=True)
    except sqlite3.Error as exc:
        raise DatabaseUnavailableError(f"No fue posible abrir SQLite: {exc}") from exc

    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only = ON")
    connection.execute("PRAGMA foreign_keys = ON")
    try:
        yield connection
    finally:
        connection.close()


def query_dataframe(
    sql: str,
    params: Sequence[object] | None = None,
    path: Path = DATABASE_PATH,
) -> pd.DataFrame:
    with readonly_connection(path) as connection:
        return pd.read_sql_query(sql, connection, params=list(params or []))


def query_scalar(
    sql: str,
    params: Sequence[object] | None = None,
    path: Path = DATABASE_PATH,
):
    with readonly_connection(path) as connection:
        row = connection.execute(sql, tuple(params or ())).fetchone()
    return None if row is None else row[0]


def database_integrity(path: Path = DATABASE_PATH) -> str:
    return str(query_scalar("PRAGMA integrity_check", path=path))


def existing_tables(path: Path = DATABASE_PATH) -> set[str]:
    frame = query_dataframe(
        "SELECT name FROM sqlite_master WHERE type = ? ORDER BY name",
        ["table"],
        path,
    )
    return set(frame["name"].tolist())


def missing_required_tables(path: Path = DATABASE_PATH) -> set[str]:
    return EXPECTED_TABLES - existing_tables(path)

