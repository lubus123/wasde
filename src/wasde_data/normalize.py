"""ParsedCell streams -> canonical observation rows.

All label resolution happens here, through the registry, with misses recorded.
A miss in a priority table (corn/soy) is a hard failure: PriorityLabelMiss.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import pandas as pd

from wasde_data.records import ParsedCell
from wasde_data.registry import Registry

_NUM_RE = re.compile(r"^-?[\d,]*\.?\d+$")
_RANGE_RE = re.compile(r"^(-?[\d,]*\.?\d+)\s*-\s*(-?[\d,]*\.?\d+)$")
_NA_TOKENS = {"", "na", "n/a", "--", "---", "tr", "neg"}


def clean_number(raw: str) -> float | None:
    """'1,233.20' -> 1233.2; '106.2 *' -> 106.2; 'NA'/'---' -> None.

    Price ranges ('2.20-2.30', printed in pre-2007 reports) become the midpoint —
    the printed text survives in raw_value (docs/DECISIONS.md).
    """
    s = raw.strip()
    s = re.sub(r"\s*\d{1,2}/\s*$", "", s)   # trailing footnote '2/'
    s = s.replace("*", "").strip()
    if s.casefold() in _NA_TOKENS:
        return None
    if m := _RANGE_RE.match(s.replace(" ", "")):
        lo = float(m.group(1).replace(",", ""))
        hi = float(m.group(2).replace(",", ""))
        return (lo + hi) / 2
    if _NUM_RE.match(s):
        return float(s.replace(",", ""))
    return None


class PriorityLabelMiss(Exception):
    """An unmapped label in a zero-error table. Parsing must not proceed."""

    def __init__(self, misses: list[dict]):
        self.misses = misses
        labels = ", ".join(f"{m['table_slug']}:{m['kind']}:'{m['raw_label']}'"
                           for m in misses[:10])
        super().__init__(f"{len(misses)} unmapped label(s) in priority tables: {labels}")


@dataclass
class NormalizeResult:
    observations: pd.DataFrame
    unmapped: pd.DataFrame  # columns: release_id, table_slug, raw_label, kind


def normalize_cells(cells: list[ParsedCell], release_id: str, report_month: str,
                    registry: Registry, priority_tables: list[str],
                    strict: bool = True) -> NormalizeResult:
    rows, misses = [], {}

    def miss(table_slug: str, raw_label: str, kind: str) -> None:
        misses[(table_slug, raw_label, kind)] = dict(
            release_id=release_id, table_slug=table_slug,
            raw_label=raw_label, kind=kind)

    for c in cells:
        attribute = registry.resolve_attribute(c.raw_attribute)
        if attribute is None:
            miss(c.table_slug, c.raw_attribute, "attribute")
            continue
        commodity = c.commodity
        if commodity is None:
            commodity = registry.resolve_commodity(c.raw_commodity)
            if commodity is None:
                miss(c.table_slug, c.raw_commodity, "commodity")
                continue
        unit = None
        if c.unit_hint:
            unit = registry.resolve_unit(c.unit_hint)
            if unit is None:
                miss(c.table_slug, c.unit_hint, "unit")  # warn-level: row still loads
        rows.append(dict(
            release_id=release_id, report_month=report_month,
            table_slug=c.table_slug, region=c.region, commodity=commodity,
            attribute=attribute, marketing_year=c.marketing_year,
            year_status=c.year_status, forecast_month=c.forecast_month,
            value=c.value, unit=unit,
            raw_attribute=c.raw_attribute.strip(), raw_commodity=c.raw_commodity.strip(),
            source_format=c.source_format, qa_status="ok",
            parsed_at=pd.Timestamp.now()))

    unmapped = pd.DataFrame(list(misses.values()),
                            columns=["release_id", "table_slug", "raw_label", "kind"])
    if strict:
        blocking = [m for m in misses.values()
                    if m["table_slug"] in priority_tables and m["kind"] != "unit"]
        if blocking:
            raise PriorityLabelMiss(blocking)
    return NormalizeResult(observations=pd.DataFrame(rows), unmapped=unmapped)
