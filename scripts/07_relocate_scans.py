"""Paddle-led page relocation for scan releases tesseract couldn't read.

49 structured-era releases (1980-93) have headers too degraded for tesseract
to match the corn/soy page titles. PaddleOCR re-attempts location by reading
the top strip of every page, then fully OCRs matched pages.

Phase A (--locate, default; DB-free — runs alongside the reconcile batch):
  strip-scan -> full page OCR -> cache under data/raw/paddle_text/ + manifest
  data/raw/paddle_text/relocated.json
Phase B (--ingest; needs the DB lock):
  parse cached pages -> identity repair -> insert observations
  (single-reader policy: identity-backed pass -> ok / repaired -> corrected /
   identity fail -> quarantined / outside identities -> warn)
"""

from __future__ import annotations

import argparse
import io
import json
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from wasde_data.config import load_config
from wasde_data.identity import identities_for, repair_group
from wasde_data.normalize import normalize_cells
from wasde_data.paddle_ocr import _load, ocr_page_cached
from wasde_data.parsers.ocr_parser import _PAGE_TITLES, OcrPage, parse_page
from wasde_data.registry import Registry


def candidates_from_parquet(cfg) -> list[tuple[str, str, str]]:
    """(release_id, report_month, pdf_path) for structured-era scan releases
    with no observations — computed from the parquet exports so phase A can
    run while another process holds the DuckDB lock."""
    releases = pd.read_parquet(cfg.paths.exports / "releases.parquet")
    releases["report_month"] = pd.to_datetime(releases["report_month"])
    obs = pd.read_parquet(cfg.paths.exports / "observations.parquet",
                          columns=["release_id"])
    have = set(obs.release_id.unique())
    sel = releases[(releases.format_era == "pdf_scan")
                   & (releases.report_month >= pd.Timestamp("1980-01-01"))
                   & (~releases.release_id.isin(have))]
    out = []
    for r in sel.itertuples():
        month_dir = cfg.paths.releases / f"{r.report_month:%Y-%m}"
        pdf = month_dir / f"{r.release_id}.pdf"
        if pdf.exists():
            out.append((r.release_id, f"{r.report_month:%Y-%m-%d}"[:7] + "-01",
                        str(pdf)))
    return sorted(out)


def locate_with_paddle(pdf_path: str, max_pages: int = 34) -> list[tuple[str, int]]:
    """(table_slug, page_no) via top-strip OCR of each page."""
    import fitz
    import numpy as np
    from PIL import Image
    ocr = _load()
    doc = fitz.open(pdf_path)
    found = []
    for pno in range(min(len(doc), max_pages)):
        pix = doc[pno].get_pixmap(dpi=200, colorspace=fitz.csGRAY)
        img = Image.open(io.BytesIO(pix.tobytes("png")))
        strip = np.array(img.crop((0, 0, img.width,
                                   int(img.height * 0.16))).convert("RGB"))
        result = ocr.predict(strip)[0]
        header = " ".join(result["rec_texts"])
        for slug, pattern in _PAGE_TITLES.items():
            if pattern.search(header):
                found.append((slug, pno))
                break
    return found


def phase_locate(cfg, manifest_path: Path, limit: int | None) -> None:
    manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}
    todo = candidates_from_parquet(cfg)
    if limit:
        todo = todo[:limit]
    print(f"{len(todo)} releases to relocate", flush=True)
    cache_dir = cfg.paths.raw / "paddle_text"
    for i, (release_id, report_month, pdf_path) in enumerate(todo, 1):
        if release_id in manifest:
            continue
        pages = locate_with_paddle(pdf_path)
        for _slug, pno in pages:
            ocr_page_cached(Path(pdf_path), pno, cache_dir, release_id)
        manifest[release_id] = dict(report_month=report_month, pdf=pdf_path,
                                    pages=pages)
        manifest_path.write_text(json.dumps(manifest, indent=1))
        print(f"  {i}/{len(todo)} {release_id}: pages={pages}", flush=True)
    print("locate done", flush=True)


def phase_ingest(cfg, manifest_path: Path) -> None:
    from wasde_data import db
    registry = Registry()
    con = db.connect(cfg.paths.db)
    manifest = json.loads(manifest_path.read_text())
    cache_dir = cfg.paths.raw / "paddle_text"
    totals = dict(releases=0, ok=0, corrected=0, warn=0, quarantined=0)
    for release_id, info in sorted(manifest.items()):
        cells = []
        for slug, pno in info["pages"]:
            text = (cache_dir / f"{release_id}-p{pno:02d}.txt").read_text()
            cells.extend(parse_page(OcrPage(pno, slug, text), "united_states",
                                    info["report_month"], registry))
        if not cells:
            continue
        nr = normalize_cells(cells, release_id, info["report_month"], registry,
                             cfg.priority_tables, strict=False)
        obs = nr.observations
        if obs.empty:
            continue
        obs = obs.set_index(["commodity", "marketing_year", "forecast_month",
                             "attribute"]).sort_index()
        for (commodity, my, fm), g in obs.groupby(level=[0, 1, 2]):
            vals = {a: (None if pd.isna(v) else float(v))
                    for (_, _, _, a), v in g.value.items()}
            corrected, quarantined = repair_group(vals, commodity)
            id_members = set().union(*identities_for(commodity))
            for attr in vals:
                key = (commodity, my, fm, attr)
                if key not in obs.index:
                    continue
                obs.loc[key, "value"] = vals[attr]
                if attr in quarantined:
                    status = "quarantined"
                elif attr in corrected:
                    status = "corrected"
                elif attr in id_members:
                    status = "ok"       # survived the identity system
                else:
                    status = "warn"     # single reader, outside identities
                obs.loc[key, "qa_status"] = status
                totals[status] += 1
        obs = obs.reset_index()
        con.execute("DELETE FROM observations WHERE release_id = ?", [release_id])
        db.upsert(con, "observations", obs,
                  ["release_id", "table_slug", "region", "commodity",
                   "attribute", "marketing_year", "forecast_month"])
        totals["releases"] += 1
    db.recompute_is_latest(con)
    print(f"ingest done: {totals}", flush=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ingest", action="store_true",
                        help="phase B: write located pages into the DB")
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args(argv)
    cfg = load_config()
    manifest_path = cfg.paths.raw / "paddle_text" / "relocated.json"
    if args.ingest:
        phase_ingest(cfg, manifest_path)
    else:
        phase_locate(cfg, manifest_path, args.limit)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
