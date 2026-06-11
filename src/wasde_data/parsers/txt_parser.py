"""Parser for the fixed-width TXT era (1995-01 -> 2010-06).

Two printed shapes (verified on wasde-06-12-1995.txt / wasde-06-10-2005.txt):

US tables — attribute rows x marketing-year columns:

                                :         :         :      2005/06  Projections
            Item                : 2003/04 : 2004/05 :==========================
                                :         :   Est.  :       May          June
    ===========================================================================
    CORN                        :
    Area                        :               Million acres
     Planted                    :   78.6       80.9          81.4 *      81.4 *

  Column anchors are read from the header (never assumed): full-year columns
  from 'NNNN/NN' tokens, projection months from the last header line. Values
  map to the nearest anchor, so short rows (e.g. CCC inventory, printed only
  for finalized years) land in the right columns.

World tables — country rows x fixed attribute columns, one block per year:

                          :Beginning:Produc-:       : Feed : Total  :Exports:
    ==========================================================================
                          :                 2004/05 (Estimated)
    United States         :   27.60  256.28    0.36  147.27 211.72   48.18 ...

  Column meanings come from config/registry/tables.yaml `txt_columns`,
  validated against the printed header (count + keywords) — a mismatch is a
  TxtStructureError, never a misassignment.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace

from wasde_data.normalize import clean_number
from wasde_data.records import ParsedCell
from wasde_data.registry import Registry, TableSpec, normalize_label, slugify_region

_SEP_RE = re.compile(r"^={20,}\s*$")
_MY_RE = re.compile(r"\d{4}/\d{2,4}")
_VALUE_RE = re.compile(
    r"[\d,]+\.?\d*\s{0,2}-\s{0,2}[\d,]+\.?\d*"  # range '2.20- 2.30' / '4.95 - 5.95'
    r"|-?[\d,]+\.?\d*"                           # plain number
    r"|NA|N/A|---+|--", re.IGNORECASE)
# A range split across lines prints as '5.10 -' with the '6.10' on the next
# line (narrow 1995-era tables). Detected per token, completed from the
# continuation line by column anchor.
_OPEN_RANGE_RE = re.compile(r"^\s{0,2}-(?!\d)")
_MONTH_NAMES = ["January", "February", "March", "April", "May", "June", "July",
                "August", "September", "October", "November", "December"]
_MONTHS = {m.casefold(): m[:3] for m in _MONTH_NAMES}
_MONTHS.update({m[:3].casefold(): m[:3] for m in _MONTH_NAMES})
_MONTHS["sept"] = "Sep"
_MONTH_TOKEN_RE = re.compile(
    r"January|February|March|April|May|June|July|August|September|October|"
    r"November|December", re.IGNORECASE)
_MONTH_ANY_RE = re.compile(
    r"Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|"
    r"Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?",
    re.IGNORECASE)
_YEAR_BLOCK_RE = re.compile(
    r"^(\d{4}/\d{2,4})\s*(?:\((Estimated|Projected)\))?\s*$")


class TxtStructureError(Exception):
    """Header does not match what the table config promises."""


@dataclass
class TxtParseResult:
    cells: list[ParsedCell] = field(default_factory=list)
    unknown_tables: list[str] = field(default_factory=list)
    structure_errors: list[str] = field(default_factory=list)


@dataclass
class _Column:
    center: int
    marketing_year: str
    year_status: str
    forecast_month: str  # '' unless projection


def _clean_ws(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def parse_txt(content: bytes, registry: Registry, report_month: str) -> TxtParseResult:
    """report_month ('2005-06-01') names the vintage of world-table
    '(Projected)' blocks, which print no month of their own."""
    text = content.decode("utf-8", errors="replace")
    lines = text.splitlines()
    result = TxtParseResult()
    current_abbr = _month_abbr(report_month)

    i = 0
    while i < len(lines):
        if not _SEP_RE.match(lines[i]):
            i += 1
            continue
        title, title_unit = _find_title(lines, i, registry)
        header, h_end = _header_block(lines, i)
        if header is None:
            i += 1
            continue
        nonspace = [c for c in title if not c.isspace()]
        if title and (title.startswith("=")
                      or sum(c.isdigit() for c in nonspace) > len(nonspace) * 0.4):
            title = ""              # stray separator inside a data block
        spec = registry.resolve_table(title) if title and ":" not in title else None
        if spec is None:
            if title and ":" not in title and _looks_like_table_header(header):
                result.unknown_tables.append(_clean_ws(title))
            i += 1          # advance one line only: a 'header' seen from a stray
            continue        # separator may contain the next table's real opener
        if spec.skip or spec.txt_skip:
            i = h_end + 1
            continue
        body, b_end = _body_block(lines, h_end)
        try:
            if spec.txt_columns:
                cells = _parse_world(body, header, spec, current_abbr, title_unit)
            else:
                cells = _parse_us(body, header, spec, registry)
            result.cells.extend(cells)
            i = b_end + 1
        except TxtStructureError as exc:
            result.structure_errors.append(f"{spec.slug}: {exc}")
            i = h_end + 1
    return result


def _find_title(lines: list[str], sep_idx: int,
                registry: Registry) -> tuple[str, str | None]:
    """Title is the nearest preceding non-blank line; unit lines directly above
    the separator ('(Million Metric Tons)' or bare 'Million Metric Tons')
    belong to the table, not the title."""
    title, title_unit = "", None
    for j in range(sep_idx - 1, max(sep_idx - 5, -1), -1):
        stripped = lines[j].strip()
        if not stripped:
            continue
        m = re.fullmatch(r"\(([^)]+)\)", stripped)
        if m and title_unit is None:
            title_unit = m.group(1)   # subtitle: unit or basis note
            continue
        if title_unit is None and registry.resolve_unit(stripped):
            title_unit = stripped     # bare unit line
            continue
        title = stripped
        break
    return title, title_unit


def _month_abbr(report_month: str) -> str:
    months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
              "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    return months[int(report_month[5:7]) - 1]


def _header_block(lines: list[str], sep_idx: int) -> tuple[list[str] | None, int]:
    """Header lines between this separator and the next one (within 8 lines)."""
    for j in range(sep_idx + 1, min(sep_idx + 9, len(lines))):
        if _SEP_RE.match(lines[j]):
            block = lines[sep_idx + 1:j]
            return (block, j) if block else (None, j)
    return None, sep_idx


def _looks_like_table_header(header: list[str]) -> bool:
    return any(":" in ln for ln in header)


def _body_block(lines: list[str], h_end: int) -> tuple[list[str], int]:
    body = []
    for j in range(h_end + 1, len(lines)):
        if _SEP_RE.match(lines[j]) or lines[j].lstrip().startswith("Note:"):
            return body, j
        body.append(lines[j])
    return body, len(lines)


# --- US-style ---------------------------------------------------------------

def _us_columns(header: list[str]) -> list[_Column]:
    cols: list[_Column] = []
    year_line = max(header, key=lambda ln: len(_MY_RE.findall(ln)))
    proj = None
    for ln in header:
        if m := re.search(r"(\d{4}/\d{2,4})\s+Proj", ln):
            proj = m.group(1)
            break
    status_line = header[-1]
    for m in _MY_RE.finditer(year_line):
        center = (m.start() + m.end()) // 2
        zone = status_line[max(0, m.start() - 4):m.end() + 4]
        status = "estimate" if "Est" in zone else "actual"
        cols.append(_Column(center, m.group(0), status, ""))
    if proj:
        for m in _MONTH_TOKEN_RE.finditer(status_line):
            month = _MONTHS[m.group(0).casefold()]
            center = (m.start() + m.end()) // 2
            cols.append(_Column(center, proj, "projection", month))
    if not cols:
        raise TxtStructureError("no marketing-year columns found in header")
    return sorted(cols, key=lambda c: c.center)


def _nearest(cols: list[_Column], center: int) -> _Column:
    return min(cols, key=lambda c: abs(c.center - center))


class _UsTable:
    """Line-by-line state machine for one US-style table body."""

    def __init__(self, header: list[str], spec: TableSpec, registry: Registry):
        self.cols = _us_columns(header)
        self.spec, self.registry = spec, registry
        self.cells: list[ParsedCell] = []
        self.commodity = spec.txt_initial_commodity or spec.commodity
        self.unit: str | None = None
        self.pending_label = ""
        self.open_ranges: dict[int, tuple[str, ParsedCell]] = {}

    def _assign(self, zone_start: int, values: list[re.Match]) -> list[int]:
        """Column index per value. Full rows zip positionally — header year
        tokens sit left of the right-aligned numbers in narrow-layout files,
        which defeats nearest-anchor matching. Short rows (CCC inventory etc.)
        use nearest-anchor with order-preserving uniqueness."""
        if len(values) == len(self.cols):
            return list(range(len(self.cols)))
        out, prev = [], -1
        for m in values:
            center = zone_start + (m.start() + m.end()) // 2
            candidates = [k for k in range(len(self.cols)) if k > prev]
            if not candidates:
                candidates = [len(self.cols) - 1]
            idx = min(candidates, key=lambda k: abs(self.cols[k].center - center))
            out.append(idx)
            prev = idx
        return out

    def _drop_footnote_tokens(self, values: list[re.Match]) -> list[re.Match]:
        """'15,613 3   15,695' / '93 _3/  93_3/': bare 1-2 digit tokens glued to
        the previous value are printed footnote refs, not values. Only dropped
        while they explain an excess over the column count."""
        while len(values) > len(self.cols):
            for i in range(1, len(values)):
                tok = values[i].group(0)
                gap = values[i].start() - values[i - 1].end()
                if re.fullmatch(r"\d{1,2}", tok) and gap <= 2:
                    values = values[:i] + values[i + 1:]
                    break
            else:
                return values
        return values

    def _flush_open(self) -> None:
        self.cells.extend(cell for _, cell in self.open_ranges.values())
        self.open_ranges = {}

    def _complete_ranges(self, values: list[re.Match]) -> None:
        """Continuation line ('7.25   7.30') completing wrapped ranges: pair
        k-th value with k-th pending open range, in column order."""
        pending = sorted(self.open_ranges.items())
        for (idx, (first, cell)), m in zip(pending, values, strict=False):
            raw = f"{first} - {m.group(0).strip()}"
            self.cells.append(replace(cell, value=clean_number(raw), raw_value=raw))
            del self.open_ranges[idx]
        self._flush_open()

    def _no_value_line(self, label: str, zone: str) -> None:
        zone_text = _clean_ws(zone)
        label_text = _clean_ws(label).rstrip(":")
        if label_text:
            section = self.spec.txt_sections.get(normalize_label(label_text)) \
                or self.registry.resolve_commodity(label_text)
            if section:
                self.commodity = section
                self.pending_label = ""
                if zone_text and self.registry.resolve_unit(zone_text):
                    self.unit = zone_text
                return
        if zone_text and self.registry.resolve_unit(zone_text):
            self.unit = zone_text
            if label_text:  # 'Yield per harvested :  Bushels' carries both
                self.pending_label = label_text
            return
        if label_text and not zone_text:
            # wrapped label ('Yield per harvested' / '    acre')
            self.pending_label = label_text if not self.pending_label \
                else f"{self.pending_label} {label_text}"

    def _value_line(self, label: str, zone: str, zone_start: int,
                    values: list[re.Match]) -> None:
        label_text = _clean_ws(label)
        if self.pending_label:
            label_text = f"{self.pending_label} {label_text}" if label_text \
                else self.pending_label
            self.pending_label = ""
        if not label_text:
            return
        values = self._drop_footnote_tokens(values)
        for m, idx in zip(values, self._assign(zone_start, values), strict=True):
            col = self.cols[idx]
            cell = ParsedCell(
                table_slug=self.spec.slug, region=self.spec.region,
                raw_commodity="", raw_attribute=label_text,
                marketing_year=col.marketing_year, year_status=col.year_status,
                forecast_month=col.forecast_month,
                value=clean_number(m.group(0)), raw_value=m.group(0).strip(),
                unit_hint=self.unit, source_format="txt", commodity=self.commodity)
            if _OPEN_RANGE_RE.match(zone[m.end():]):
                self.open_ranges[idx] = (m.group(0).strip(), cell)
            else:
                self.cells.append(cell)

    def feed(self, ln: str) -> None:
        if ":" not in ln:
            return
        label = ln.split(":", 1)[0].rstrip()
        zone_start = ln.index(":") + 1
        zone = ln[zone_start:]
        values = list(_VALUE_RE.finditer(zone))
        if self.open_ranges and not _clean_ws(label) and values:
            self._complete_ranges(values)
            return
        self._flush_open()
        if values:
            self._value_line(label, zone, zone_start, values)
        else:
            self._no_value_line(label, zone)


def _parse_us(body: list[str], header: list[str], spec: TableSpec,
              registry: Registry) -> list[ParsedCell]:
    table = _UsTable(header, spec, registry)
    for ln in body:
        table.feed(ln)
    table._flush_open()
    return table.cells


# --- world-style ------------------------------------------------------------

# prefix forms: July/Aug-1995 headers truncate words ('Beginni', 'Expor')
_WORLD_KEYWORDS = ("Beginni", "Expor")


def _world_row_label(label: str, month: str, status: str,
                     current_region: str | None) -> tuple[str, str, str | None]:
    """Resolve a data row's region and vintage month. Cont'd projected tables
    print May/June pairs, either as month-only sub-rows under a region header
    or with the month appended to the region ('Argentina      May')."""
    if status != "projection" or not label:
        return label, month, (label or current_region)
    if _MONTH_ANY_RE.fullmatch(label):
        return current_region or "", _MONTHS[label.casefold()], current_region
    if m2 := re.fullmatch(r"(.+?)\s+(" + _MONTH_ANY_RE.pattern + r")",
                          label, re.IGNORECASE):
        return m2.group(1), _MONTHS[m2.group(2).casefold()], m2.group(1)
    return label, month, label


def _parse_world(body: list[str], header: list[str], spec: TableSpec,
                 current_abbr: str, unit: str | None) -> list[ParsedCell]:
    # leaf delimiter line: the header line with the most colons
    leaf = max(header, key=lambda ln: ln.count(":"))
    n_leaf = leaf.count(":") - 1  # between-colon columns
    has_trailing = any(ln.rstrip()[-1:] not in (":", "") and "stock" in ln.casefold()
                       for ln in header)
    n_cols = n_leaf + (1 if has_trailing else 0)
    if n_cols != len(spec.txt_columns):
        raise TxtStructureError(
            f"{n_cols} printed columns vs {len(spec.txt_columns)} configured")
    blob = " ".join(header)
    for kw in _WORLD_KEYWORDS:
        if kw.casefold() not in blob.casefold():
            raise TxtStructureError(f"header lacks expected keyword '{kw}'")

    # column anchors: midpoints between colon positions; trailing column after last
    colon_pos = [m.start() for m in re.finditer(":", leaf)]
    anchors: list[int] = [(a + b) // 2 for a, b in zip(colon_pos, colon_pos[1:],
                                                       strict=False)]
    if has_trailing:
        anchors.append(colon_pos[-1] + 5)

    label_end = colon_pos[0]
    cells: list[ParsedCell] = []
    year, status, month = None, "actual", ""
    current_region: str | None = None  # carried into May/June vintage pair rows

    for ln in body:
        label = _clean_ws(ln[:label_end].replace(":", " "))
        zone = ln[label_end:]
        if ym := _YEAR_BLOCK_RE.match(_clean_ws(zone.replace(":", " "))):
            year = ym.group(1)
            marker = (ym.group(2) or "").casefold()
            status = {"estimated": "estimate", "projected": "projection"}.get(
                marker, "actual")
            month = current_abbr if status == "projection" else ""
            if label:  # 1995 Cont'd: 'World 2/ : 1994/95 (Projected)'
                current_region = label
            continue
        values = list(_VALUE_RE.finditer(zone))
        if not values:
            if label and year is not None:
                current_region = label  # pair header: 'World 3/ :' before May/June rows
            continue
        row_label, row_month, current_region = _world_row_label(
            label, month, status, current_region)
        if not row_label or year is None:
            continue
        for m in values:
            center = label_end + (m.start() + m.end()) // 2
            idx = min(range(len(anchors)), key=lambda k: abs(anchors[k] - center))
            cells.append(ParsedCell(
                table_slug=spec.slug, region=slugify_region(row_label),
                raw_commodity="", raw_attribute=spec.txt_columns[idx],
                marketing_year=year, year_status=status,
                forecast_month=row_month if status == "projection" else "",
                value=clean_number(m.group(0)), raw_value=m.group(0).strip(),
                unit_hint=unit, source_format="txt", commodity=spec.commodity))
    return cells


