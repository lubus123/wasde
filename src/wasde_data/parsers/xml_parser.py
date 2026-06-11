"""Parser for the XML era (2010-07 -> present).

The WASDE XML is a ReportingServices export: each sub-report (srNN) holds one
printed table as nested matrix/group elements. Shapes vary (US tables key cells
by attribute->year->month; world by-country tables use one matrix per marketing
year with country region groups), but every semantic lives in element
*attributes* whose names carry a numeric suffix (attribute1, market_year4,
region_header2, cell_value5...). A single recursive context-walk therefore
covers every shape: collect suffix-stripped context on the way down, emit at
each value-bearing Cell.

Quirks handled here, verified against wasde0626.xml (see docs/DECISIONS.md):
- matrix tag numbering continues across tables (sr28's children are
  matrix3/4/5) -> iterate children by tag pattern, order by numeric suffix.
- Commodity for multi-matrix US tables (sr12 feed grains vs corn) exists only
  positionally -> config/registry/tables.yaml `matrices`, validated against the
  matrix's printed quantity-unit row where one exists.
- Unit header rows are encoded as filler Cells (m1_unit_descr1..3) repeated
  inside every attribute group; multi-word units split across consecutive
  cells ('Million 480 ' + ' Pound Bales').
- forecast_month appears as 'Jun' in some matrices and 'June' in others.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from lxml import etree

from wasde_data.normalize import clean_number
from wasde_data.records import ParsedCell
from wasde_data.registry import Registry, TableSpec, normalize_label, slugify_region

_MATRIX_TAG = re.compile(r"^matrix(\d+)$")
_CTX_KEY = re.compile(r"^(attribute|commodity|region|market_year|forecast_month)\d*$")
_HEADER_KEY = re.compile(r"^(?:region_header|commodity_header)\d*$")
_UNIT_KEY = re.compile(r"^m\d_unit_descr(\d+)$")
_VALUE_KEY = re.compile(r"^cell_value\d*$")
_MY_RE = re.compile(r"^(\d{4}/\d{2})\s*(Est\.?|Proj\.?)?$")

_MONTHS = {m.casefold(): m[:3] for m in
           ["January", "February", "March", "April", "May", "June", "July",
            "August", "September", "October", "November", "December"]}

_PRICE_UNIT_IN_LABEL = [
    (re.compile(r"\(\$/bu\)", re.I), "$/bu"),
    (re.compile(r"\(\$/cwt\)", re.I), "$/cwt"),
    (re.compile(r"\(c/lb\)", re.I), "cents/lb"),
    (re.compile(r"\(\$/s\.t\.\)", re.I), "$/ton"),
]
_AREA_IN_LABEL = re.compile(r"\(mil\.?\s*acres\)|acres \(mil\.?\)", re.I)
_PRICE_LABEL = re.compile(r"price", re.I)

_AREA_SLUGS = {"area_planted", "area_harvested"}
_YIELD_SLUGS = {"yield_per_harvested_acre"}


class XmlStructureError(Exception):
    """The XML does not look like what the table config promises (positional
    drift, missing unit row). Hard stop: silent misclassification is worse."""


@dataclass
class XmlParseResult:
    cells: list[ParsedCell] = field(default_factory=list)
    unknown_tables: list[str] = field(default_factory=list)  # raw titles, not configured
    skipped_tables: list[str] = field(default_factory=list)  # configured skip (reliability)


def _clean_ws(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def parse_market_year(raw: str) -> tuple[str, str] | None:
    m = _MY_RE.match(_clean_ws(raw))
    if not m:
        return None
    marker = (m.group(2) or "").casefold()
    status = "actual" if not marker else (
        "estimate" if marker.startswith("est") else "projection")
    return m.group(1), status


def _norm_month(raw: str) -> str:
    return _MONTHS.get(_clean_ws(raw).casefold(), _clean_ws(raw))


def _matrix_children(sr: etree._Element) -> list[etree._Element]:
    pairs = [(int(_MATRIX_TAG.match(c.tag).group(1)), c)
             for c in sr if isinstance(c.tag, str) and _MATRIX_TAG.match(c.tag)]
    return [el for _, el in sorted(pairs, key=lambda p: p[0])]


def _unit_rows(matrix: etree._Element) -> dict[int, str]:
    """Reassemble printed unit header rows from filler cells, in document order."""
    parts: dict[int, list[str]] = {}
    for cell in matrix.iter("Cell"):
        for key, value in cell.attrib.items():
            m = _UNIT_KEY.match(key)
            if m and value.strip():
                bucket = parts.setdefault(int(m.group(1)), [])
                cleaned = _clean_ws(value)
                if not bucket or bucket[-1] != cleaned:
                    bucket.append(cleaned)
        # distinct-in-order; repeated-per-column duplicates collapse, multi-part
        # unit strings ('Million 480', 'Pound Bales') keep their order
    rows = {}
    for idx, chunk in parts.items():
        seen, ordered = set(), []
        for p in chunk:
            if p not in seen:
                seen.add(p)
                ordered.append(p)
        rows[idx] = _clean_ws(" ".join(ordered))
    return rows


def _classify_units(rows: dict[int, str]) -> dict[str, str | None]:
    """Map unit header rows to roles. 3 rows = area/yield/quantity (US balance
    tables); 1 row = quantity; otherwise ambiguous -> only explicit labels."""
    if len(rows) == 3:
        lo, mid, hi = (rows[k] for k in sorted(rows))
        return {"area": lo, "yield": mid, "qty": hi}
    if len(rows) == 1:
        return {"area": None, "yield": None, "qty": next(iter(rows.values()))}
    return {"area": None, "yield": None, "qty": None}


def _unit_for(raw_attribute: str, attr_slug: str | None, units: dict[str, str | None],
              spec: TableSpec, subtitle_unit: str | None) -> str | None:
    for pattern, unit in _PRICE_UNIT_IN_LABEL:
        if pattern.search(raw_attribute):
            return unit
    if _AREA_IN_LABEL.search(raw_attribute):
        return "Million Acres"
    if _PRICE_LABEL.search(raw_attribute):
        return spec.price_unit
    if attr_slug in _AREA_SLUGS:
        return units["area"]
    if attr_slug in _YIELD_SLUGS:
        return units["yield"]
    return units["qty"] or spec.default_quantity_unit or subtitle_unit


def _subtitle_unit(sr: etree._Element) -> str | None:
    sub = _clean_ws(sr.get("sub_report_subtitle") or "").strip("()")
    return sub or None


def parse_xml(content: bytes, registry: Registry) -> XmlParseResult:
    root = etree.fromstring(content)
    result = XmlParseResult()
    for sr in root.iter("Report"):
        name = sr.get("Name") or ""
        title = sr.get("sub_report_title")
        if not re.match(r"^sr\d+$", name) or title is None:
            continue
        spec = registry.resolve_table(title, sr_hint=name)
        if spec is None:
            result.unknown_tables.append(_clean_ws(title))
            continue
        if spec.skip:
            result.skipped_tables.append(spec.slug)
            continue
        result.cells.extend(_parse_sub_report(sr, spec, registry))
    return result


def _parse_sub_report(sr: etree._Element, spec: TableSpec,
                      registry: Registry) -> list[ParsedCell]:
    cells: list[ParsedCell] = []
    subtitle_unit = _subtitle_unit(sr)
    matrices = _matrix_children(sr)
    if spec.matrices and len(matrices) > len(spec.matrices):
        raise XmlStructureError(
            f"{spec.slug}: {len(matrices)} matrices but only "
            f"{len(spec.matrices)} configured")

    for i, matrix in enumerate(matrices):
        units = _classify_units(_unit_rows(matrix))
        matrix_commodity = None
        if spec.matrices:
            mcfg = spec.matrices[i]
            matrix_commodity = mcfg["commodity"]
            want = mcfg.get("quantity_unit_must_contain")
            if want:
                got = units["qty"] or ""
                if normalize_label(want) not in normalize_label(got):
                    raise XmlStructureError(
                        f"{spec.slug} matrix #{i + 1}: expected quantity unit "
                        f"containing '{want}', found '{got}' — positional "
                        f"commodity mapping cannot be trusted")
        _walk(matrix, {}, cells, spec, registry, matrix_commodity,
              units, subtitle_unit)
    return cells


def _context_updates(el: etree._Element) -> dict:
    updates = {}
    for key, value in el.attrib.items():
        base_match = _CTX_KEY.match(key)
        if base_match:
            base = base_match.group(1)
            if base == "market_year":
                parsed = parse_market_year(value)
                if parsed:
                    updates["market_year"], updates["year_status"] = parsed
            elif base == "forecast_month":
                updates["forecast_month"] = _norm_month(value)
            else:
                updates[base] = value
        elif _HEADER_KEY.match(key) and value.strip():
            parsed = parse_market_year(value)
            if parsed:  # sr18-30: one matrix per marketing-year column
                updates["market_year"], updates["year_status"] = parsed
            else:       # sr08-10: 'World' / 'United States' / 'Foreign 3/'
                updates["region"] = value
    return updates


def _walk(el: etree._Element, ctx: dict, cells: list[ParsedCell], spec: TableSpec,
          registry: Registry, matrix_commodity: str | None,
          units: dict[str, str | None], subtitle_unit: str | None) -> None:
    updates = _context_updates(el)
    if updates:
        ctx = {**ctx, **updates}

    if el.tag == "Cell":
        raw_value = next((v for k, v in el.attrib.items() if _VALUE_KEY.match(k)), None)
        if raw_value is not None and ctx.get("market_year"):
            _emit(ctx, raw_value, cells, spec, registry, matrix_commodity,
                  units, subtitle_unit)
        return

    for child in el:
        if isinstance(child.tag, str):
            _walk(child, ctx, cells, spec, registry, matrix_commodity,
                  units, subtitle_unit)


def _emit(ctx: dict, raw_value: str, cells: list[ParsedCell], spec: TableSpec,
          registry: Registry, matrix_commodity: str | None,
          units: dict[str, str | None], subtitle_unit: str | None) -> None:
    raw_attribute = _clean_ws(ctx.get("attribute", ""))
    if not raw_attribute:
        return  # filler/header cell
    year_status = ctx.get("year_status", "actual")
    forecast_month = ctx.get("forecast_month", "") if year_status == "projection" else ""

    commodity = matrix_commodity or (None if ctx.get("commodity") else spec.commodity)
    raw_commodity = _clean_ws(ctx.get("commodity", ""))

    region = slugify_region(ctx["region"]) if ctx.get("region") else spec.region
    attr_slug = registry.resolve_attribute(raw_attribute)
    unit_hint = _unit_for(raw_attribute, attr_slug, units, spec, subtitle_unit)

    cells.append(ParsedCell(
        table_slug=spec.slug, region=region,
        raw_commodity=raw_commodity, raw_attribute=raw_attribute,
        marketing_year=ctx["market_year"], year_status=year_status,
        forecast_month=forecast_month,
        value=clean_number(raw_value), raw_value=raw_value.strip(),
        unit_hint=unit_hint, source_format="xml", commodity=commodity))
