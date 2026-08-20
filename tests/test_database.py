from __future__ import annotations

import unittest

from utils.config import DATABASE_PATH, EXPECTED_TABLES
from utils.database import (
    database_integrity,
    existing_tables,
    query_scalar,
    readonly_connection,
)


class DatabaseTests(unittest.TestCase):
    def test_database_copy_exists(self) -> None:
        self.assertTrue(DATABASE_PATH.is_file())

    def test_database_opens_in_readonly_mode(self) -> None:
        with readonly_connection() as connection:
            query_only = connection.execute("PRAGMA query_only").fetchone()[0]
        self.assertEqual(query_only, 1)

    def test_integrity_check(self) -> None:
        self.assertEqual(database_integrity(), "ok")

    def test_required_tables_exist(self) -> None:
        self.assertTrue(EXPECTED_TABLES.issubset(existing_tables()))

    def test_expected_document_volume(self) -> None:
        count = int(query_scalar("SELECT COUNT(*) FROM documents"))
        self.assertGreaterEqual(count, 24_607)


if __name__ == "__main__":
    unittest.main()
