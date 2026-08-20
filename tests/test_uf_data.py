from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

import pandas as pd

from utils.config import DATABASE_PATH
from utils.legacy_data import load_cost_control
from utils.uf_data import get_uf_coverage, parse_sii_uf_html, save_uf_rates


class UfDataTests(unittest.TestCase):
    def test_sii_html_parser_reads_daily_values(self) -> None:
        html = """
        <div class="meses" id="mes_enero"><table><tbody>
          <tr><th><strong>1</strong></th><td>29.070,33</td>
              <th><strong>11</strong></th><td>29.101,98</td></tr>
        </tbody></table></div>
        """
        frame = parse_sii_uf_html(html, 2021)
        self.assertEqual(frame["uf_date"].tolist(), ["2021-01-01", "2021-01-11"])
        self.assertEqual(frame["uf_clp"].tolist(), [29_070.33, 29_101.98])

    def test_rates_are_saved_without_duplicating_dates(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            database = Path(temp_dir) / "cc_lab.sqlite"
            shutil.copy2(DATABASE_PATH, database)
            rates = pd.DataFrame(
                {
                    "uf_date": ["2030-01-01"],
                    "uf_clp": [50_000.0],
                    "source_name": ["Prueba"],
                    "source_url": ["https://example.test/uf"],
                }
            )
            first = save_uf_rates(rates, database)
            second = save_uf_rates(rates, database)
            self.assertEqual(first.inserted, 1)
            self.assertEqual(second.inserted, 0)
            self.assertEqual(second.unchanged, 1)

    def test_master_database_covers_all_used_dates(self) -> None:
        coverage = get_uf_coverage()
        self.assertGreaterEqual(coverage["rate_count"], 2_000)
        self.assertEqual(coverage["missing_date_count"], 0)

    def test_operational_ledger_uses_sqlite_uf_table(self) -> None:
        ledger = load_cost_control()
        self.assertFalse(ledger["UF-F"].isna().any())
        self.assertGreater(ledger["NET-UF-F"].abs().sum(), 0)


if __name__ == "__main__":
    unittest.main()
