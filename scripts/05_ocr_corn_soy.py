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
from wasde_data.identity import repair_group
from wasde_data.normalize import normalize_cells
from wasde_data.parsers.ocr_parser import parse_pdf
from wasde_data.registry import Registry


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
