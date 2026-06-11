"""The common parser output contract.

Every parser (xml, txt, ocr) yields ParsedCell streams; normalize.py resolves
raw labels through the registry into canonical observation rows. Parsers never
touch the database.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ParsedCell:
    table_slug: str          # resolved table, e.g. 'us_corn'
    region: str              # 'united_states', country slug, or 'world'
    raw_commodity: str       # commodity label exactly as printed ('' if implied by table)
    raw_attribute: str       # attribute label exactly as printed
    marketing_year: str      # '2026/27'
    year_status: str         # 'actual' | 'estimate' | 'projection'
    forecast_month: str      # 'May'/'Jun' for projection columns; '' otherwise
    value: float | None      # as published; None for missing/NA cells
    raw_value: str           # cell text exactly as printed (audit trail)
    unit_hint: str | None    # unit string in effect for this row, as printed
    source_format: str       # 'xml' | 'txt' | 'ocr'
    commodity: str | None = None  # slug when structurally known (matrix position /
                                  # table default); None -> resolve raw_commodity
