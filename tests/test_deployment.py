from __future__ import annotations

import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from scripts.backup_database import create_external_backup
from utils import config


class DeploymentTests(unittest.TestCase):
    def test_default_paths_are_portable(self) -> None:
        configured = [
            config.CREDIT_NOTE_XML_PATH,
            config.CONSTRUCTION_SOURCE_PATH,
            config.ORIGINAL_DATABASE_PATH,
            config.PAYMENTS_SOURCE_PATH,
        ]
        self.assertTrue(
            all(path.is_relative_to(config.OPTIONAL_SOURCES_PATH) for path in configured)
        )

    def test_production_streamlit_settings(self) -> None:
        content = (config.PROJECT_ROOT / ".streamlit" / "config.toml").read_text(
            encoding="utf-8"
        )
        self.assertIn('address = "0.0.0.0"', content)
        self.assertIn("runOnSave = false", content)

    def test_external_backup_is_valid_and_rotated(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "source.sqlite"
            destination = root / "offsite"
            with closing(sqlite3.connect(source)) as connection:
                connection.execute("CREATE TABLE sample(value TEXT)")
                connection.execute("INSERT INTO sample VALUES ('ok')")
                connection.commit()

            first = create_external_backup(source, destination, retention=1)
            second = create_external_backup(source, destination, retention=1)
            self.assertTrue(second.is_file())
            self.assertLessEqual(len(list(destination.glob("cc_lab_*.sqlite"))), 1)
            with closing(sqlite3.connect(second)) as connection:
                self.assertEqual(connection.execute("PRAGMA integrity_check").fetchone()[0], "ok")
                self.assertEqual(connection.execute("SELECT value FROM sample").fetchone()[0], "ok")
            self.assertTrue(first == second or not first.exists())


if __name__ == "__main__":
    unittest.main()
