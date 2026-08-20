from __future__ import annotations

import calendar
import sqlite3
import ssl
from contextlib import closing
from dataclasses import dataclass
from datetime import date, datetime
from html.parser import HTMLParser
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen

import pandas as pd
import certifi

from utils.config import DATABASE_PATH
from utils.database import query_dataframe
from utils.migrations import create_database_backup


SII_UF_URL = "https://www.sii.cl/valores_y_fechas/uf/uf{year}.htm"
MONTHS = {
    "enero": 1,
    "febrero": 2,
    "marzo": 3,
    "abril": 4,
    "mayo": 5,
    "junio": 6,
    "julio": 7,
    "agosto": 8,
    "septiembre": 9,
    "octubre": 10,
    "noviembre": 11,
    "diciembre": 12,
}


@dataclass(frozen=True)
class UfUpdateResult:
    years: tuple[int, ...]
    fetched: int
    inserted: int
    updated: int
    unchanged: int
    latest_date: str | None


class _SiiUfParser(HTMLParser):
    def __init__(self, year: int) -> None:
        super().__init__(convert_charrefs=True)
        self.year = year
        self.month: int | None = None
        self.month_div_depth = 0
        self.in_row = False
        self.in_cell = False
        self.cell_parts: list[str] = []
        self.row_cells: list[str] = []
        self.values: dict[str, float] = {}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        if tag == "div":
            if self.month_div_depth:
                self.month_div_depth += 1
            elif "meses" in (attributes.get("class") or "").split():
                month_name = (attributes.get("id") or "").removeprefix("mes_")
                self.month = MONTHS.get(month_name)
                self.month_div_depth = 1
        if self.month is None:
            return
        if tag == "tr":
            self.in_row = True
            self.row_cells = []
        elif self.in_row and tag in {"th", "td"}:
            self.in_cell = True
            self.cell_parts = []

    def handle_data(self, data: str) -> None:
        if self.in_cell:
            self.cell_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if self.month is not None and self.in_row and self.in_cell and tag in {"th", "td"}:
            self.row_cells.append("".join(self.cell_parts).strip().replace("\xa0", ""))
            self.in_cell = False
            self.cell_parts = []
        elif self.month is not None and self.in_row and tag == "tr":
            self._save_row()
            self.in_row = False
        if tag == "div" and self.month_div_depth:
            self.month_div_depth -= 1
            if self.month_div_depth == 0:
                self.month = None

    def _save_row(self) -> None:
        if self.month is None:
            return
        for position in range(0, len(self.row_cells) - 1, 2):
            day_text, value_text = self.row_cells[position : position + 2]
            if not day_text.isdigit() or not value_text:
                continue
            day = int(day_text)
            if day > calendar.monthrange(self.year, self.month)[1]:
                continue
            normalized = value_text.replace(".", "").replace(",", ".")
            try:
                uf_value = float(normalized)
            except ValueError:
                continue
            uf_date = date(self.year, self.month, day).isoformat()
            self.values[uf_date] = uf_value


def parse_sii_uf_html(html: str, year: int) -> pd.DataFrame:
    parser = _SiiUfParser(year)
    parser.feed(html)
    frame = pd.DataFrame(
        sorted(parser.values.items()), columns=["uf_date", "uf_clp"]
    )
    if frame.empty:
        raise ValueError(f"El SII no entregó valores UF para {year}.")
    frame["source_name"] = "SII"
    frame["source_url"] = SII_UF_URL.format(year=year)
    return frame


def fetch_sii_uf_year(year: int, timeout: int = 20) -> pd.DataFrame:
    url = SII_UF_URL.format(year=year)
    request = Request(url, headers={"User-Agent": "C&C Lab/1.0"})
    ssl_context = ssl.create_default_context(cafile=certifi.where())
    try:
        with urlopen(request, timeout=timeout, context=ssl_context) as response:
            html = response.read().decode("utf-8-sig", errors="replace")
    except (OSError, URLError) as exc:
        raise RuntimeError(f"No fue posible consultar la UF {year} en el SII: {exc}") from exc
    return parse_sii_uf_html(html, year)


def save_uf_rates(
    rates: pd.DataFrame,
    database_path: Path = DATABASE_PATH,
) -> UfUpdateResult:
    required = ["uf_date", "uf_clp", "source_name", "source_url"]
    if not set(required).issubset(rates.columns):
        raise ValueError("La actualización UF no contiene las columnas requeridas.")

    clean = rates[required].copy()
    clean["uf_date"] = pd.to_datetime(clean["uf_date"], errors="coerce").dt.date.astype("string")
    clean["uf_clp"] = pd.to_numeric(clean["uf_clp"], errors="coerce")
    clean = clean.dropna(subset=["uf_date", "uf_clp"])
    clean = clean[clean["uf_clp"] > 0].drop_duplicates("uf_date", keep="last")
    if clean.empty:
        raise ValueError("No hay valores UF válidos para guardar.")

    with closing(sqlite3.connect(database_path)) as connection:
        existing = dict(
            connection.execute(
                "SELECT uf_date, uf_clp FROM uf_daily WHERE uf_date BETWEEN ? AND ?",
                (clean["uf_date"].min(), clean["uf_date"].max()),
            ).fetchall()
        )
    inserted = int((~clean["uf_date"].isin(existing)).sum())
    updated = int(
        clean.apply(
            lambda row: row["uf_date"] in existing
            and abs(float(existing[row["uf_date"]]) - float(row["uf_clp"])) > 0.001,
            axis=1,
        ).sum()
    )
    unchanged = len(clean) - inserted - updated
    if inserted or updated:
        create_database_backup(
            database_path,
            database_path.parent / "backups",
            "uf_update",
        )
        now = datetime.now().isoformat(timespec="seconds")
        rows = [
            (row.uf_date, float(row.uf_clp), row.source_name, row.source_url, now)
            for row in clean.itertuples(index=False)
        ]
        with closing(sqlite3.connect(database_path)) as connection:
            connection.execute("PRAGMA busy_timeout = 5000")
            connection.executemany(
                """
                INSERT INTO uf_daily(uf_date, uf_clp, source_name, source_url, imported_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(uf_date) DO UPDATE SET
                    uf_clp = excluded.uf_clp,
                    source_name = excluded.source_name,
                    source_url = excluded.source_url,
                    imported_at = excluded.imported_at
                """,
                rows,
            )
            connection.commit()

    years = tuple(
        int(year)
        for year in sorted(pd.to_datetime(clean["uf_date"]).dt.year.unique())
    )
    return UfUpdateResult(
        years=years,
        fetched=len(clean),
        inserted=inserted,
        updated=updated,
        unchanged=unchanged,
        latest_date=str(clean["uf_date"].max()),
    )


def update_uf_from_sii(
    years: list[int] | tuple[int, ...],
    database_path: Path = DATABASE_PATH,
) -> UfUpdateResult:
    requested = tuple(sorted(set(int(year) for year in years)))
    if not requested:
        raise ValueError("No hay años UF para actualizar.")
    rates = pd.concat([fetch_sii_uf_year(year) for year in requested], ignore_index=True)
    return save_uf_rates(rates, database_path)


def get_uf_coverage(database_path: Path = DATABASE_PATH) -> dict[str, object]:
    rates = query_dataframe(
        "SELECT uf_date, uf_clp, source_name, imported_at FROM uf_daily ORDER BY uf_date",
        path=database_path,
    )
    required = query_dataframe(
        """
        SELECT issue_date AS required_date, 'SII' AS source_kind
        FROM documents
        WHERE issue_date IS NOT NULL AND issue_date <> ''
        UNION
        SELECT p.payment_date, 'H-P'
        FROM payments p
        JOIN payment_imports pi ON pi.payment_import_id = p.payment_import_id
        WHERE pi.is_active = 1 AND p.payment_date IS NOT NULL AND p.payment_date <> ''
        """,
        path=database_path,
    )
    required["required_date"] = pd.to_datetime(required["required_date"], errors="coerce")
    required = required.dropna(subset=["required_date"])
    rate_dates = set(pd.to_datetime(rates.get("uf_date", pd.Series(dtype=str)), errors="coerce").dropna())
    missing = required[~required["required_date"].isin(rate_dates)].copy()
    missing["required_date"] = missing["required_date"].dt.date.astype(str)
    missing = missing.drop_duplicates().sort_values(["required_date", "source_kind"])
    latest = None if rates.empty else str(rates["uf_date"].max())
    earliest = None if rates.empty else str(rates["uf_date"].min())
    return {
        "rate_count": len(rates),
        "earliest_date": earliest,
        "latest_date": latest,
        "required_date_count": required["required_date"].nunique(),
        "missing_date_count": missing["required_date"].nunique(),
        "missing_dates": missing,
    }


def get_payment_uf_differences(
    tolerance: float = 0.02,
    database_path: Path = DATABASE_PATH,
) -> pd.DataFrame:
    return query_dataframe(
        """
        SELECT p.payment_id, p.payment_date, p.supplier_name,
               p.uf_value AS hp_uf, u.uf_clp AS official_uf,
               ROUND(p.uf_value - u.uf_clp, 2) AS difference
        FROM payments p
        JOIN payment_imports pi ON pi.payment_import_id = p.payment_import_id
        JOIN uf_daily u ON u.uf_date = p.payment_date
        WHERE pi.is_active = 1
          AND p.uf_value > 0
          AND ABS(p.uf_value - u.uf_clp) > ?
        ORDER BY ABS(p.uf_value - u.uf_clp) DESC, p.payment_date DESC
        """,
        [tolerance],
        database_path,
    )


def get_month_uf_rates(
    year: int,
    month: int,
    database_path: Path = DATABASE_PATH,
) -> pd.DataFrame:
    period = f"{int(year):04d}-{int(month):02d}"
    return query_dataframe(
        """
        SELECT uf_date, uf_clp
        FROM uf_daily
        WHERE substr(uf_date, 1, 7) = ?
        ORDER BY uf_date
        """,
        [period],
        database_path,
    )


def get_current_uf_rate(
    reference_date: date | None = None,
    database_path: Path = DATABASE_PATH,
) -> float:
    reference_date = reference_date or date.today()
    frame = query_dataframe(
        "SELECT uf_clp FROM uf_daily WHERE uf_date <= ? ORDER BY uf_date DESC LIMIT 1",
        [reference_date.isoformat()],
        database_path,
    )
    if frame.empty:
        raise ValueError("No hay un valor UF disponible para convertir a CLP.")
    return float(frame.iloc[0]["uf_clp"])


def years_requiring_update(coverage: dict[str, object], today: date | None = None) -> list[int]:
    today = today or date.today()
    missing = coverage["missing_dates"]
    years = set(pd.to_datetime(missing["required_date"], errors="coerce").dt.year.dropna().astype(int))
    latest = coverage["latest_date"]
    if latest is None or date.fromisoformat(str(latest)) < today:
        years.add(today.year)
    return sorted(years)
