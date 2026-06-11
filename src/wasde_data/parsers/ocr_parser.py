"""OCR parser for the scanned-PDF era (1973 -> 1994), corn/soy priority.

Two print eras:
- ~1985-1994 (27-32 pages): per-commodity balance sheets in the same shape the
  TXT era later used ('label : v1 v2 v3 v4'), so located pages are OCRed and
  fed through a TXT-like column parser with digit-confusion repair.
- 1973-~1984 (4-17 pages): compact summary tables — NOT handled here yet
  (docs/DECISIONS.md); releases fall through with zero cells and stay visible
  in coverage.

Trust model: raw OCR is never trusted. Cells survive only via balance-sheet
identities + consecutive-report continuity + AgManager finals (scripts/05);
failures are quarantined for the manual-override workflow.
"""

from __future__ import annotations

import io
import re
from dataclasses import dataclass, field
from pathlib import Path

import fitz
import pytesseract
from PIL import Image

from wasde_data.normalize import clean_number
from wasde_data.records import ParsedCell
from wasde_data.registry import Registry, normalize_label

# page titles of the corn and soy balance-sheet pages across 1985-94 prints;
# patterns tolerate fax-OCR confusions ('U.5.', 'Gra1ns', 'C0rn')
_PAGE_TITLES = {
    "us_corn": re.compile(
        r"U\W{0,3}[S5$]\W{0,3}\s*Feed\s+Gra[il1]ns?\s+and\s+C[o0]rn", re.I),
    "us_soybeans": re.compile(
        r"U\W{0,3}[S5$]\W{0,3}\s*S[o0]ybeans?\s+and\s+Pr[o0]ducts", re.I),
}
_SECTION_RES = {
    "us_corn": [(re.compile(r"^FEED\s*GRAINS", re.I), "feed_grains"),
                (re.compile(r"^CORN", re.I), "corn")],
    "us_soybeans": [(re.compile(r"^SOYBEANS\b", re.I), "soybeans"),
                    (re.compile(r"^SOYBEAN\s*OIL", re.I), "soybean_oil"),
                    (re.compile(r"^SOYBEAN\s*MEAL", re.I), "soybean_meal")],
}

_MY_RE = re.compile(r"(\d{4})\s*/\s*(\d{2})")
_MONTH_RE = re.compile(
    r"Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|"
    r"Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?", re.I)
_MONTHS = {m[:3].casefold(): m[:3] for m in
           ["January", "February", "March", "April", "May", "June", "July",
            "August", "September", "October", "November", "December"]}

# common fax-scan digit confusions, applied only inside numeric-ish tokens
_DIGIT_FIX = str.maketrans({"i": "1", "l": "1", "I": "1", "|": "1", "{": "1",
                            "o": "0", "O": "0", "Q": "0", "D": "0",
                            "S": "5", "s": "5", "B": "8", "Z": "2", "z": "2",
                            "g": "9", "q": "9", "G": "6", "A": "4", "?": "7"})
_NUMERIC_ISH = re.compile(r"^[\dilIoOQDSsBZzgqGA?{|,.\-]+$")
_VALUE_TOKEN = re.compile(r"\S+")
_TOLERANCE_COL = re.compile(r"^[+t]/?-")  # '+/-22' reliability column, dropped


@dataclass
class OcrPage:
    page_no: int
    table_slug: str
    text: str


@dataclass
class OcrParseResult:
    cells: list[ParsedCell] = field(default_factory=list)
    pages_found: list[tuple[str, int]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def _render(page: fitz.Page, dpi: int) -> Image.Image:
    pix = page.get_pixmap(dpi=dpi, colorspace=fitz.csGRAY)
    return Image.open(io.BytesIO(pix.tobytes("png")))


def locate_pages(doc: fitz.Document, cache_dir: Path | None = None,
                 release_id: str = "") -> list[OcrPage]:
    """Find corn/soy balance-sheet pages via a fast low-dpi header pass, then
    OCR located pages at full quality. Full-page texts are cached to disk."""
    found: list[OcrPage] = []
    for pno in range(len(doc)):
        cache = (cache_dir / f"{release_id}-p{pno:02d}.txt") if cache_dir else None
        if cache and cache.exists():
            text = cache.read_text()
            header = text[:400]
        else:
            img = _render(doc[pno], 350)  # fax scans need full dpi even for titles
            header = pytesseract.image_to_string(
                img.crop((0, 0, img.width, int(img.height * 0.16))),
                config="--psm 6")
            text = ""
        for slug, pattern in _PAGE_TITLES.items():
            if pattern.search(header):
                if not text:
                    text = pytesseract.image_to_string(
                        _render(doc[pno], 350), config="--psm 6")
                    if cache:
                        cache.parent.mkdir(parents=True, exist_ok=True)
                        cache.write_text(text)
                found.append(OcrPage(pno, slug, text))
                break
    return found


def _fix_token(tok: str) -> str:
    if _NUMERIC_ISH.match(tok) and any(c.isdigit() for c in tok):
        return tok.translate(_DIGIT_FIX)
    return tok


def _ocr_value(tok: str) -> float | None:
    fixed = _fix_token(tok).replace(" ", "")
    # OCR merges thousands separators into periods sometimes: '1.181' with >2
    # decimals is 1,181
    if re.fullmatch(r"\d{1,3}\.\d{3}", fixed):
        fixed = fixed.replace(".", ",")
    return clean_number(fixed)


_ABBRS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
          "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def _derive_columns(report_month: str) -> list[dict]:
    """Column layout from WASDE's fixed convention (OCRed year tokens are too
    noisy to trust: '1983/64'): [base-2 actual, base-1 estimate, base proj x
    prior+current month], where base rolls to the new crop year in May. May
    reports print a single first-projection column."""
    year, month = int(report_month[:4]), int(report_month[5:7])
    base = year if month >= 5 else year - 1
    my = [f"{y}/{str(y + 1)[2:]}" for y in (base - 2, base - 1, base)]
    cols = [dict(my=my[0], status="actual", month=""),
            dict(my=my[1], status="estimate", month="")]
    cur = _ABBRS[month - 1]
    prev = _ABBRS[month - 2]
    if month == 5:
        cols.append(dict(my=my[2], status="projection", month=cur))
    else:
        cols.append(dict(my=my[2], status="projection", month=prev))
        cols.append(dict(my=my[2], status="projection", month=cur))
    return cols


_ROW_LABELS = {  # printed label prefixes -> attribute slug (1985-94 prints)
    "planted": "area_planted",
    "harvested": "area_harvested",
    "yield per harv": "yield_per_harvested_acre",
    "acre": "yield_per_harvested_acre",  # wrap continuation of 'Yield per harv.'
    "beginning stocks": "beginning_stocks",
    "production": "production",
    "imports": "imports",
    "supply, total": "supply_total",
    "supply total": "supply_total",
    "feed and residual": "feed_and_residual",
    "food, seed": "food_seed_industrial",
    "food seed": "food_seed_industrial",
    "crushings": "crush",
    "crush": "crush",
    "domestic, total": "domestic_total",
    "domestic total": "domestic_total",
    "domestic": "domestic_total",
    "exports": "exports",
    "seed": "seed",
    "residual": "residual",
    "use, total": "use_total",
    "use total": "use_total",
    "ending stocks": "ending_stocks",
}


def _row_attribute(label: str) -> str | None:
    norm = normalize_label(label)
    norm = re.sub(r"[^a-z, ]", "", norm).strip()
    for prefix, slug in _ROW_LABELS.items():
        if norm.startswith(prefix):
            return slug
    return None


def parse_page(page: OcrPage, spec_region: str, report_month: str,
               registry: Registry) -> list[ParsedCell]:
    lines = page.text.splitlines()
    cols = _derive_columns(report_month)
    cells: list[ParsedCell] = []
    sections = _SECTION_RES[page.table_slug]
    commodity = sections[0][1] if page.table_slug == "us_corn" else "soybeans"
    unit: str | None = None

    for ln in lines:
        stripped = ln.strip()
        for pattern, slug in sections:
            if pattern.match(stripped):
                commodity = slug
                break
        if ":" not in ln:
            continue
        label, _, zone = ln.partition(":")
        attr = _row_attribute(label)
        if attr is None:
            low = normalize_label(zone)
            if registry.resolve_unit(low.title()) or "million" in low or "bushel" in low:
                unit = _guess_unit(low)
            continue
        toks = [t for t in _VALUE_TOKEN.findall(zone)
                if not _TOLERANCE_COL.match(t)]
        values = [_ocr_value(t) for t in toks]
        values = [v for v in values if v is not None]
        if len(values) > len(cols):
            values = values[:len(cols)]
        for col, v in zip(cols, values, strict=False):
            cells.append(ParsedCell(
                table_slug=page.table_slug, region=spec_region,
                raw_commodity="", raw_attribute=label.strip(),
                marketing_year=col["my"], year_status=col["status"],
                forecast_month=col["month"], value=v,
                raw_value=" ".join(toks)[:60], unit_hint=unit,
                source_format="ocr", commodity=commodity))
    return cells


_UNIT_GUESSES = [
    (("metric",), "Million Metric Tons"),
    (("bushel", "million"), "Million Bushels"),
    (("pound", "million"), "Million Pounds"),
    (("short ton",), "Thousand Short Tons"),
    (("tons",), "Thousand Short Tons"),
    (("acre",), "Million Acres"),
    (("bushel",), "Bushels"),
]


def _guess_unit(low: str) -> str | None:
    for needles, unit in _UNIT_GUESSES:
        if all(n in low for n in needles):
            return unit
    return None


def parse_pdf(pdf_path: Path, registry: Registry, report_month: str,
              cache_dir: Path | None = None, release_id: str = "") -> OcrParseResult:
    result = OcrParseResult()
    doc = fitz.open(pdf_path)
    pages = locate_pages(doc, cache_dir=cache_dir, release_id=release_id)
    result.pages_found = [(p.table_slug, p.page_no) for p in pages]
    for page in pages:
        cells = parse_page(page, "united_states", report_month, registry)
        if not cells:
            result.notes.append(f"{page.table_slug} p{page.page_no}: no header/cols")
        result.cells.extend(cells)
    return result
