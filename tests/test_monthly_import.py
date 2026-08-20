from __future__ import annotations

import shutil
import sqlite3
import tempfile
import unittest
from contextlib import closing
from io import BytesIO
from pathlib import Path

import pandas as pd

from utils.config import DATABASE_PATH
from utils.monthly_import import import_monthly_files, preview_monthly_files


class MonthlyImportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.database = Path(self.temp_dir.name) / "cc_lab.sqlite"
        shutil.copy2(DATABASE_PATH, self.database)
        self.period = "202606"
        self.sii_data = (
            "Nro;Tipo Doc;Tipo Compra;RUT Proveedor;Razon Social;Folio;Fecha Docto;"
            "Fecha Recepcion;Fecha Acuse;Monto Exento;Monto Neto;Monto IVA Recuperable;"
            "Monto Iva No Recuperable;Codigo IVA No Rec.;Monto Total\n"
            "1;33;Del Giro;11111111-1;PROVEEDOR PRUEBA;990001;15-06-2026;"
            "16-06-2026;;0;100000;19000;;;119000\n"
        ).encode("utf-8")
        payment = pd.DataFrame(
            [
                {
                    "RUT": "11111111",
                    "DV": "1",
                    "SUPPLIER-F": "PROVEEDOR PRUEBA",
                    "DATE-PAYMENT": "20-06-2026",
                    "INVOICE": "990001",
                    "NET-CLP": 100000,
                    "VAT-CLP": 19000,
                    "GROSS-CLP": 119000,
                    "PAID-CLP": 119000,
                    "CAT": 300,
                    "SUB-CAT": 301,
                    "UF": 39000,
                    "NET-UF": 100000 / 39000,
                    "VAT-UF": 19000 / 39000,
                    "GROSS-UF": 119000 / 39000,
                    "PAID-UF": 119000 / 39000,
                }
            ]
        )
        output = BytesIO()
        with pd.ExcelWriter(output, engine="openpyxl") as writer:
            payment.to_excel(writer, sheet_name="202606", index=False)
        self.payment_data = output.getvalue()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_preview_and_incremental_import(self) -> None:
        preview = preview_monthly_files(
            self.period,
            self.sii_data,
            "RCV_202606.csv",
            self.payment_data,
            "PAGOS_202606.xlsx",
            self.database,
        )
        self.assertEqual(preview.sii_documents, 1)
        self.assertEqual(preview.valid_payments, 1)
        result = import_monthly_files(
            self.period,
            self.sii_data,
            "RCV_202606.csv",
            self.payment_data,
            "PAGOS_202606.xlsx",
            self.database,
        )
        self.assertEqual(result.new_documents, 1)
        self.assertEqual(result.valid_payments, 1)
        with closing(sqlite3.connect(self.database)) as connection:
            active_imports = connection.execute(
                "SELECT COUNT(*) FROM payment_imports WHERE is_active = 1"
            ).fetchone()[0]
            monthly = connection.execute(
                "SELECT COUNT(*) FROM payment_imports WHERE source_period = '202606'"
            ).fetchone()[0]
            document = connection.execute(
                "SELECT COUNT(*) FROM documents WHERE document_key = '11111111|33|990001'"
            ).fetchone()[0]
        self.assertEqual(active_imports, 2)
        self.assertEqual(monthly, 1)
        self.assertEqual(document, 1)

    def test_same_period_cannot_be_loaded_twice(self) -> None:
        import_monthly_files(
            self.period,
            self.sii_data,
            "RCV_202606.csv",
            self.payment_data,
            "PAGOS_202606.xlsx",
            self.database,
        )
        with self.assertRaisesRegex(ValueError, "ya contiene"):
            preview_monthly_files(
                self.period,
                self.sii_data,
                "RCV_202606.csv",
                self.payment_data,
                "PAGOS_202606.xlsx",
                self.database,
            )


if __name__ == "__main__":
    unittest.main()
