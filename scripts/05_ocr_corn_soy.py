"""OCR the 1985-94 scanned releases' corn/soy balance sheets.

Trust pipeline per (commodity, marketing_year, column) group:
1. raw OCR cells (digit-confusion repaired token-wise)
2. identity solver: if exactly one member of a balance identity is missing or
   inconsistent, derive it from the others (qa_status='corrected')
3. groups still inconsistent -> every member qa_status='quarantined'
4. cross-checks (continuity vs neighboring reports, AgManager finals) run via
   scripts/99_qa_report.py

Pre-1985 compact-format releases are skipped with a note (docs/DECISIONS.md).
Resumable: full-page OCR text is cached under data/raw/ocr_text/.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from wasde_data import db
from wasde_data.config import load_config
from wasde_data.normalize import normalize_cells
from wasde_data.parsers.ocr_parser import parse_pdf
from wasde_data.registry import Registry

# identities as signed sums: sum(coef * value) == 0
_ID_SUPPLY = {"beginning_stocks": 1, "production": 1, "imports": 1,
              "supply_total": -1}
_ID_USE_CORN = {"domestic_total": 1, "exports": 1, "use_total": -1}
_ID_USE_SOY = {"crush": 1, "exports": 1, "seed": 1, "residual": 1,
               "use_total": -1}
_ID_END = {"supply_total": 1, "use_total": -1, "ending_stocks": -1}
_TOL = 1.6


def _plausible(derived: float, have: dict) -> bool:
    known = [abs(v) for v in have.values() if v is not None]
    return derived >= 0 and (not known or derived <= 3 * max(known))


def _solve(vals: dict, identity: dict[str, int]) -> list[str] | None:
    """None -> identity untestable (2+ unknowns). [] -> holds (possibly after
    deriving the single unknown). [m] -> m was derived/repaired. list -> the
    inconsistent members (quarantine)."""
    have = {m: vals.get(m) for m in identity}
    missing = [m for m, v in have.items() if v is None]
    if len(missing) >= 2:
        return None
    if len(missing) == 1:
        m = missing[0]
        derived = -sum(c * have[k] for k, c in identity.items() if k != m) \
            / identity[m]
        if _plausible(derived, have):
            vals[m] = derived
            return [m]
        return [k for k in identity]
    residual = sum(c * have[k] for k, c in identity.items())
    if abs(residual) <= _TOL:
        return []
    # exactly one bad member can be re-derived such that the identity holds;
    # accept only if a unique plausible repair exists
    repairs = []
    for m, coef in identity.items():
        derived = have[m] - residual / coef
        if _plausible(derived, {k: v for k, v in have.items() if k != m}):
            repairs.append((m, derived))
    if len(repairs) == 1:
        m, derived = repairs[0]
        vals[m] = derived
        return [m]
    return [k for k in identity]


def repair_group(vals: dict, commodity: str) -> tuple[set[str], set[str]]:
    """Returns (corrected, quarantined) attribute sets for one column group.
    A member of any failed identity is quarantined; a uniquely-derived repair
    is 'corrected'. Iterates so a repair can unlock the next identity."""
    corrected: set[str] = set()
    quarantined: set[str] = set()
    use_id = _ID_USE_SOY if commodity == "soybeans" else _ID_USE_CORN
    for _ in range(3):
        progress = False
        for identity in (_ID_SUPPLY, use_id, _ID_END):
            if all(vals.get(m) is None for m in identity):
                continue
            out = _solve(vals, identity)
            if out is None:
                continue
            if len(out) == 1 and out[0] not in corrected | quarantined:
                corrected.add(out[0])
                progress = True
            elif len(out) > 1:
                quarantined.update(m for m in out if vals.get(m) is not None)
        if not progress:
            break
    corrected -= quarantined
    return corrected, quarantined


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release-id", default=None)
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args(argv)

    cfg = load_config()
    registry = Registry()
    con = db.connect(cfg.paths.db)
    cache_dir = cfg.paths.raw / "ocr_text"

    where = "format_era = 'pdf_scan' AND f.ext = 'pdf'"
    params: list = []
    if args.release_id:
        where += " AND release_id = ?"
        params.append(args.release_id)
    todo = con.execute(
        f"SELECT r.release_id, r.report_month, f.local_path "
        f"FROM releases r JOIN release_files f USING (release_id) "
        f"WHERE {where} ORDER BY r.report_month DESC", params).fetchall()
    if args.limit:
        todo = todo[:args.limit]
    print(f"{len(todo)} scanned releases to OCR", flush=True)

    stats = dict(parsed=0, no_pages=0, cells=0, corrected=0, quarantined=0)
    for i, (release_id, report_month, local_path) in enumerate(todo, 1):
        process_release(con, registry, cfg, cache_dir, release_id,
                        report_month, local_path, stats)
        if i % 10 == 0:
            print(f"  {i}/{len(todo)}: {stats}", flush=True)

    db.recompute_is_latest(con)
    print(f"done: {stats}", flush=True)
    return 0


def process_release(con, registry, cfg, cache_dir, release_id, report_month,
                    local_path, stats) -> None:
    result = parse_pdf(Path(local_path), registry, str(report_month),
                       cache_dir=cache_dir, release_id=release_id)
    if not result.cells:
        stats["no_pages"] += 1
        return
    nr = normalize_cells(result.cells, release_id, str(report_month),
                         registry, cfg.priority_tables, strict=False)
    obs = nr.observations
    if obs.empty:
        stats["no_pages"] += 1
        return
    # identity repair per column group
    obs = obs.set_index(
        ["commodity", "marketing_year", "forecast_month", "attribute"]) \
        .sort_index()
    new_rows = []
    for (commodity, my, fm), g in obs.groupby(level=[0, 1, 2]):
        vals = {a: (None if pd.isna(v) else float(v))
                for (_, _, _, a), v in g.value.items()}
        corrected, quarantined = repair_group(vals, commodity)
        template = g.iloc[0]
        for attr, v in vals.items():
            key = (commodity, my, fm, attr)
            if key not in obs.index:
                if attr in corrected and v is not None:
                    # the printed row was unreadable; the identity recovers it
                    new_rows.append(dict(
                        release_id=release_id, report_month=str(report_month),
                        table_slug=template.table_slug, region=template.region,
                        commodity=commodity, attribute=attr,
                        marketing_year=my, year_status=template.year_status,
                        forecast_month=fm, value=v, unit=template.unit,
                        raw_attribute="(derived from identity)",
                        raw_commodity="", source_format="ocr",
                        qa_status="corrected", parsed_at=pd.Timestamp.now()))
                continue
            obs.loc[key, "value"] = v
            if attr in quarantined:
                obs.loc[key, "qa_status"] = "quarantined"
            elif attr in corrected:
                obs.loc[key, "qa_status"] = "corrected"
        stats["corrected"] += len(corrected)
        stats["quarantined"] += len(quarantined)
    obs = obs.reset_index()
    if new_rows:
        obs = pd.concat([obs, pd.DataFrame(new_rows)], ignore_index=True)
    con.execute("DELETE FROM observations WHERE release_id = ?", [release_id])
    db.upsert(con, "observations", obs,
              ["release_id", "table_slug", "region", "commodity",
               "attribute", "marketing_year", "forecast_month"])
    stats["parsed"] += 1
    stats["cells"] += len(obs)


if __name__ == "__main__":
    raise SystemExit(main())
