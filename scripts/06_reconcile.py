"""Dual-reader reconciliation for the scan era (tesseract x GOT-OCR 2.0).

Per (commodity, marketing_year, column) group, per cell:
  - readers agree            -> qa_status 'ok' (dual-verified)
  - readers disagree/missing -> the reader whose column satisfies the balance
    identities wins ('corrected' if it changes the stored value);
    both-pass-but-conflict or neither-passes -> 'quarantined' + worklist row
  - GOT-only cells whose group passes identities are inserted ('corrected')

Worklist: data/exports/ocr_worklist.csv (release, cell, both readings).
Resumable: GOT page texts cache under data/raw/got_text/.
"""

from __future__ import annotations

import argparse
import sys
from itertools import product
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from wasde_data import db
from wasde_data.config import load_config
from wasde_data.got_ocr import ocr_page_cached
from wasde_data.identity import identities_pass
from wasde_data.normalize import normalize_cells
from wasde_data.parsers.ocr_parser import OcrPage, locate_pages, parse_page
from wasde_data.registry import Registry

AGREE_TOL = 0.051
MAX_DISPUTED = 8  # exhaustive reader-choice search bound (2^8 combos)


def arbitrate_group(t_vals: dict, g_vals: dict,
                    commodity: str) -> dict[str, tuple[float | None, str]]:
    """Per-cell resolution for one column group: {attr: (value, qa_status)}.

    Agreed cells anchor the group. For disputed cells (readers differ, or only
    one read the cell), every combination of reader choices is tested against
    the identity system; a UNIQUE passing combination decides every disputed
    cell at once. Ambiguity or no passing combination -> quarantine.
    """
    attrs = sorted(set(t_vals) | set(g_vals))
    agreed, disputed = {}, []
    for attr in attrs:
        t, v = t_vals.get(attr), g_vals.get(attr)
        if t is not None and v is not None and abs(t - v) <= AGREE_TOL:
            agreed[attr] = t
        elif t is None and v is None:
            continue
        else:
            disputed.append(attr)

    out = {a: (val, "ok") for a, val in agreed.items()}
    if not disputed:
        return out
    winners = _winning_combos(agreed, disputed, t_vals, g_vals, commodity) \
        if len(disputed) <= MAX_DISPUTED else []
    if len(winners) == 1:
        for attr, pick in zip(disputed, winners[0], strict=True):
            chosen = (t_vals.get(attr), g_vals.get(attr))[pick]
            changed = (attr not in t_vals
                       or t_vals.get(attr) is None
                       or abs((t_vals.get(attr) or 0) - chosen) > AGREE_TOL)
            out[attr] = (chosen, "corrected" if changed else "ok")
    else:
        for attr in disputed:
            keep = t_vals.get(attr) if t_vals.get(attr) is not None \
                else g_vals.get(attr)
            out[attr] = (keep, "quarantined")
    return out


def _winning_combos(agreed, disputed, t_vals, g_vals, commodity):
    winners = []
    for combo in product((0, 1), repeat=len(disputed)):
        trial = dict(agreed)
        for attr, pick in zip(disputed, combo, strict=True):
            trial[attr] = (t_vals.get(attr), g_vals.get(attr))[pick]
        if any(trial.get(a) is None for a in disputed):
            continue  # a combination that leaves a hole can't be verified
        if identities_pass(trial, commodity):
            winners.append(combo)
    return winners


def got_cells_for_release(release_id, report_month, pdf_path, registry, cfg):
    """Run GOT-OCR over the release's located pages -> normalized value dicts."""
    import fitz
    doc = fitz.open(pdf_path)
    tess_cache = cfg.paths.raw / "ocr_text"
    got_cache = cfg.paths.raw / "got_text"
    pages = locate_pages(doc, cache_dir=tess_cache, release_id=release_id)
    cells = []
    for page in pages:
        text = ocr_page_cached(Path(pdf_path), page.page_no, got_cache, release_id)
        cells.extend(parse_page(OcrPage(page.page_no, page.table_slug, text),
                                "united_states", str(report_month), registry))
    if not cells:
        return {}
    nr = normalize_cells(cells, release_id, str(report_month), registry,
                         cfg.priority_tables, strict=False)
    groups: dict = {}
    for _, r in nr.observations.iterrows():
        key = (r.table_slug, r.commodity, r.marketing_year, r.forecast_month)
        groups.setdefault(key, {})[r.attribute] = \
            None if pd.isna(r.value) else float(r.value)
    return groups


def reconcile_release(con, release_id, report_month, got_groups, worklist):
    tess = con.execute("""
        SELECT table_slug, commodity, marketing_year, forecast_month,
               attribute, value, qa_status, region, year_status, unit
        FROM observations WHERE release_id = ? AND source_format = 'ocr'
    """, [release_id]).fetchdf()
    if tess.empty:
        return dict(ok=0, corrected=0, quarantined=0, inserted=0)

    stats = dict(ok=0, corrected=0, quarantined=0, inserted=0)
    updates, inserts = [], []
    for (slug, commodity, my, fm), g in tess.groupby(
            ["table_slug", "commodity", "marketing_year", "forecast_month"]):
        t_vals = {r.attribute: (None if pd.isna(r.value) else float(r.value))
                  for r in g.itertuples()}
        g_vals = got_groups.get((slug, commodity, my, fm), {})
        template = g.iloc[0]

        resolution = arbitrate_group(t_vals, g_vals, commodity)
        for attr, (value, status) in resolution.items():
            if attr in t_vals:
                updates.append((value, status,
                                release_id, slug, commodity, my, fm, attr))
            elif value is not None and status != "quarantined":
                inserts.append(dict(
                    release_id=release_id, report_month=str(report_month),
                    table_slug=slug, region=template.region,
                    commodity=commodity, attribute=attr, marketing_year=my,
                    year_status=template.year_status, forecast_month=fm,
                    value=value, unit=template.unit,
                    raw_attribute="(GOT-OCR, identity-verified)",
                    raw_commodity="", source_format="ocr",
                    qa_status=status, parsed_at=pd.Timestamp.now()))
                stats["inserted"] += 1
            stats["ok" if status == "ok" else
                  "corrected" if status == "corrected" else "quarantined"] += 1
            if status == "quarantined":
                worklist.append(dict(release_id=release_id, table_slug=slug,
                                     commodity=commodity, marketing_year=my,
                                     forecast_month=fm, attribute=attr,
                                     tesseract=t_vals.get(attr),
                                     got_ocr=g_vals.get(attr)))

    for value, status, *key in updates:
        con.execute("""
            UPDATE observations SET value = ?, qa_status = ?
            WHERE release_id = ? AND table_slug = ? AND commodity = ?
              AND marketing_year = ? AND forecast_month = ? AND attribute = ?
        """, [value, status, *key])
    if inserts:
        db.upsert(con, "observations", pd.DataFrame(inserts),
                  ["release_id", "table_slug", "region", "commodity",
                   "attribute", "marketing_year", "forecast_month"])
    return stats


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release-id", default=None)
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args(argv)

    cfg = load_config()
    registry = Registry()
    con = db.connect(cfg.paths.db)
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

    totals = dict(ok=0, corrected=0, quarantined=0, inserted=0, releases=0)
    worklist: list[dict] = []
    for i, (release_id, report_month, local_path) in enumerate(todo, 1):
        got_groups = got_cells_for_release(release_id, report_month, local_path,
                                           registry, cfg)
        if not got_groups:
            continue
        stats = reconcile_release(con, release_id, report_month, got_groups,
                                  worklist)
        for k, v in stats.items():
            totals[k] += v
        totals["releases"] += 1
        if i % 5 == 0:
            print(f"  {i}/{len(todo)}: {totals}", flush=True)

    out = cfg.paths.exports / "ocr_worklist.csv"
    out.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(worklist).to_csv(out, index=False)
    print(f"done: {totals}; worklist -> {out} ({len(worklist)} rows)", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
