"""AgManager (K-State) final balance sheets: the cross-source for QA.

These workbooks hold USDA's *current* (final/latest-revised) numbers per
marketing year back to 1973/74 — not report vintages. They therefore validate
the finalized 'actual' columns of historical reports; projection-column
vintages are validated by identities + consecutive-report continuity instead
(docs/DECISIONS.md).

Corn workbook: 'Annual Raw Data' sheet, attributes as rows x years as columns.
Soybean workbook: 'Annual Sheet', years as rows x attributes as columns.
"""

from __future__ import annotations

import re
from pathlib import Path

import pandas as pd

CORN_URL = "https://www.agmanager.info/sites/default/files/spreadsheets/CornSupplyDemand_64.xls"
SOY_URL = ("https://www.agmanager.info/sites/default/files/spreadsheets/"
           "SoybeanAnnualBalanceSheet_63.xls")

_CORN_ROWS = {
    "Planted Acres (millions)": ("area_planted", "million_acres"),
    "Harvested Acres (Millions)": ("area_harvested", "million_acres"),
    "Production (millions)": ("production", "million_bushels"),
    "Stocks (millions)": ("beginning_stocks", "million_bushels"),
    "Imports (million)": ("imports", "million_bushels"),
    "TOTAL SUPPLY (millions)": ("supply_total", "million_bushels"),
    "ethanol for fuel": ("ethanol_for_fuel", "million_bushels"),
    "Feed & Residual": ("feed_and_residual", "million_bushels"),
    "ALL DOM. USE": ("domestic_total", "million_bushels"),
    "EXPORTS": ("exports", "million_bushels"),
    "TOTAL USAGE": ("use_total", "million_bushels"),
    "ENDING STOCKS": ("ending_stocks", "million_bushels"),
    "Avg Farm Price ($/Bu)": ("farm_price", "usd_per_bushel"),
}

_SOY_COLS = {
    "Planted Acres": ("area_planted", "million_acres"),
    "Harvested Acres": ("area_harvested", "million_acres"),
    "Production": ("production", "million_bushels"),
    "Stocks": ("beginning_stocks", "million_bushels"),
    "Imports": ("imports", "million_bushels"),
    "Total Supply": ("supply_total", "million_bushels"),
    "Crush": ("crush", "million_bushels"),
    "Seed": ("seed", "million_bushels"),
    "Exports": ("exports", "million_bushels"),
    "Total Usage": ("use_total", "million_bushels"),
    "Ending Stocks": ("ending_stocks", "million_bushels"),
    "Avg Farm Price": ("farm_price", "usd_per_bushel"),
}


def _my_from_short(short: str) -> str | None:
    """'73/74' -> '1973/74' (pivot: 73 -> 1973, 25 -> 2025)."""
    m = re.fullmatch(r"(\d{2})/(\d{2})", str(short).strip())
    if not m:
        return None
    start = int(m.group(1))
    return f"{1900 + start if start >= 73 else 2000 + start}/{m.group(2)}"


def parse_corn(path: Path) -> pd.DataFrame:
    df = pd.ExcelFile(path).parse("Annual Raw Data", header=None)
    year_row = df[df.iloc[:, 1] == "SEP/AUG YEAR #"].index[0]
    years = {col: _my_from_short(df.iat[year_row, col])
             for col in range(2, df.shape[1])}
    years = {c: y for c, y in years.items() if y}
    note = str(df.iat[1, 1])
    rows = []
    for _, row in df.iterrows():
        label = str(row.iloc[1]).strip()
        if label not in _CORN_ROWS:
            continue
        attribute, unit = _CORN_ROWS[label]
        for col, my in years.items():
            v = row.iloc[col]
            if pd.notna(v) and isinstance(v, int | float):
                rows.append(dict(commodity="corn", attribute=attribute,
                                 marketing_year=my, value=float(v), unit=unit,
                                 source_note=note))
    return pd.DataFrame(rows)


def parse_soybeans(path: Path) -> pd.DataFrame:
    df = pd.ExcelFile(path).parse("Annual Sheet", header=None)
    header_row = df[df.iloc[:, 1] == "Year"].index[0]
    headers = {col: str(df.iat[header_row, col]).strip()
               for col in range(df.shape[1])}
    rows = []
    for i in range(header_row + 1, len(df)):
        year = df.iat[i, 1]
        if pd.isna(year) or not str(year).strip().isdigit():
            continue
        y = int(year)
        my = f"{y}/{str(y + 1)[2:]}"
        for col, label in headers.items():
            if label not in _SOY_COLS:
                continue
            attribute, unit = _SOY_COLS[label]
            v = df.iat[i, col]
            if pd.notna(v) and isinstance(v, int | float):
                rows.append(dict(commodity="soybeans", attribute=attribute,
                                 marketing_year=my, value=float(v), unit=unit,
                                 source_note="SoybeanAnnualBalanceSheet"))
    return pd.DataFrame(rows)


def check_cross_source(con, tolerance_pct: float = 0.5) -> pd.DataFrame:
    """Final 'actual'-column observations vs AgManager, latest releases only.

    Only the LAST report of each marketing year's life prints the truly-final
    number, so compare each agmanager value against the most recent
    observation of that (commodity, attribute, MY) with year_status='actual'.
    """
    df = con.execute("""
        WITH actuals AS (
          SELECT o.commodity, o.attribute, o.marketing_year, o.value,
                 row_number() OVER (PARTITION BY o.commodity, o.attribute,
                                    o.marketing_year
                                    ORDER BY o.report_month DESC) AS rn
          FROM observations_latest o
          WHERE o.table_slug IN ('us_corn', 'us_soybeans')
            AND o.commodity IN ('corn', 'soybeans')
            AND o.year_status = 'actual' AND o.value IS NOT NULL
        )
        SELECT a.commodity, a.attribute, a.marketing_year,
               a.value AS ours, g.value AS agmanager
        FROM actuals a JOIN agmanager_obs g
          ON (a.commodity, a.attribute, a.marketing_year)
           = (g.commodity, g.attribute, g.marketing_year)
        WHERE a.rn = 1
    """).fetchdf()
    if df.empty:
        return pd.DataFrame(columns=["release_id", "table_slug", "check_name",
                                     "severity", "detail", "observed", "expected"])
    rel_diff = ((df.ours - df.agmanager).abs()
                / df.agmanager.abs().clip(lower=1e-9) * 100)
    bad = df[rel_diff > tolerance_pct]
    return pd.DataFrame(dict(
        release_id="(final)", table_slug="us_" + bad.commodity,
        check_name="cross_source_agmanager", severity="warn",
        detail=(bad.commodity + " " + bad.attribute + " " + bad.marketing_year),
        observed=bad.ours, expected=bad.agmanager))
