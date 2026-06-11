"""Label registry: 50 years of printed-label drift resolved to canonical slugs.

Resolution is normalize-then-exact-match against curated alias lists — never
fuzzy. A miss returns None and is recorded by the caller in unmapped_labels;
for priority tables a miss is a hard parse failure. Aliases are added
deliberately, by a human reading the QA report.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

from wasde_data.config import PROJECT_ROOT

# Trailing footnote markers as printed across eras: 'Exports 2/', 'Planted *',
# stacked refs 'Exports 2/7/', 'Ending stocks 2/10/'
_FOOTNOTE_RE = re.compile(r"(\s+\d{1,2}/|(?<=/)\d{1,2}/|\s*\*+)+\s*$")


def normalize_label(raw: str) -> str:
    """Casefold, collapse whitespace, strip footnote markers and edge punctuation."""
    s = raw.replace("&amp;", "&")
    s = _FOOTNOTE_RE.sub("", s)
    s = re.sub(r"\s+", " ", s).strip().strip(":").strip()
    s = s.rstrip(".,").strip()
    return s.casefold()


# Leading '/2'-style markers appear in world-table labels ('Total /2 Domestic');
# normalize_label only strips trailing 'N/' forms.
_INNER_FOOTNOTE_RE = re.compile(r"\s*/\d{1,2}\s*|\s*\d{1,2}/\s*")


def slugify_region(raw: str) -> str:
    """Deterministic region slug: countries/aggregates need no curated registry.

    '        Argentina' -> 'argentina'; 'World  3/' -> 'world';
    '    World Less China' -> 'world_less_china'; 'C. Amer & Carib  8/' -> 'c_amer_carib'.
    """
    s = normalize_label(raw)
    s = _INNER_FOOTNOTE_RE.sub(" ", s)
    s = re.sub(r"[^a-z0-9]+", "_", s).strip("_")
    return s


class TableSpec:
    def __init__(self, slug: str, spec: dict):
        self.slug = slug
        self.region = spec.get("region", "united_states")
        self.commodity = spec.get("commodity")  # implied commodity, if single-commodity table
        self.sr_hint = spec.get("sr_hint")
        self.patterns = [re.compile(p, re.IGNORECASE) for p in spec["title_patterns"]]
        # Multi-matrix US tables: commodity is positional in the XML. Each entry
        # may pin a unit token that the matrix's quantity-unit row must contain,
        # so silent positional drift becomes a hard parse error.
        self.matrices: list[dict] = spec.get("matrices", [])
        # TXT-era world tables: fixed column order, validated by count+keywords
        self.txt_columns: list[str] = spec.get("txt_columns", [])
        self.txt_skip = bool(spec.get("txt_skip", False))  # shape not yet supported
        # commodity in effect before any section header (1996 soy tables print
        # the soybeans block with no 'SOYBEANS:' line)
        self.txt_initial_commodity: str | None = spec.get("txt_initial_commodity")
        # TXT-era table-local section headers -> commodity slugs
        # (rice: 'TOTAL' / 'LONG GRAIN' / 'MEDIUM & SHORT GRAIN')
        self.txt_sections: dict[str, str] = {
            normalize_label(k): v for k, v in spec.get("txt_sections", {}).items()}
        self.default_quantity_unit = spec.get("default_quantity_unit")
        self.price_unit = spec.get("price_unit")  # for price labels that omit the unit
        self.skip = bool(spec.get("skip", False))

    def matches(self, title: str) -> bool:
        t = re.sub(r"\s+", " ", title).strip()
        return any(p.search(t) for p in self.patterns)


class Registry:
    def __init__(self, registry_dir: Path | None = None):
        d = registry_dir or PROJECT_ROOT / "config" / "registry"
        self._attributes = self._load_aliases(d / "attributes.yaml", "attributes")
        self._commodities = self._load_aliases(d / "commodities.yaml", "commodities")
        self._units = self._load_aliases(d / "units.yaml", "units")
        tables_raw = yaml.safe_load((d / "tables.yaml").read_text())["tables"]
        self.tables = [TableSpec(slug, spec) for slug, spec in tables_raw.items()]

    @staticmethod
    def _load_aliases(path: Path, key: str) -> dict[str, str]:
        """Flatten {slug: {aliases: [...]}} into {normalized_alias: slug}.
        Canonical slugs resolve to themselves, so parser-supplied slugs
        (e.g. world-table txt_columns) pass through normalize_cells."""
        data = yaml.safe_load(path.read_text())[key]
        mapping: dict[str, str] = {slug: slug for slug in data}
        for slug, spec in data.items():
            for alias in spec["aliases"]:
                norm = normalize_label(alias)
                existing = mapping.get(norm)
                if existing is not None and existing != slug:
                    raise ValueError(
                        f"{path.name}: alias '{alias}' maps to both '{existing}' and '{slug}'")
                mapping[norm] = slug
        return mapping

    def resolve_attribute(self, raw: str) -> str | None:
        return self._attributes.get(normalize_label(raw))

    def resolve_commodity(self, raw: str) -> str | None:
        return self._commodities.get(normalize_label(raw))

    def resolve_unit(self, raw: str) -> str | None:
        return self._units.get(normalize_label(raw))

    def resolve_table(self, title: str, sr_hint: str | None = None) -> TableSpec | None:
        hits = [t for t in self.tables if t.matches(title)]
        if len(hits) > 1 and sr_hint:
            hinted = [t for t in hits if t.sr_hint == sr_hint]
            if hinted:
                hits = hinted
        return hits[0] if hits else None
