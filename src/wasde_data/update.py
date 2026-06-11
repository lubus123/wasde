"""New-release detection and end-to-end ingest of one release.

The cron path (scripts/10_update.py) calls run_update():
  1. fetch ESMIS catalog page 0 fresh (new releases prepend there)
  2. diff against the releases table + state.json
  3. for each new release: archive raw files, register, parse (XML era),
     upsert, re-export parquet
  4. advance state.json only when the run had zero errors (mars-archive rule)

A vN revision arrives as a new ESMIS file set under the same esmis_id: it
becomes its own release_id and is_latest flips to it for that report_month.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from wasde_data import db
from wasde_data.archive import archive_release
from wasde_data.config import PipelineConfig
from wasde_data.esmis import EsmisClient, Release, parse_release
from wasde_data.export import export_all
from wasde_data.normalize import normalize_cells
from wasde_data.parsers.xml_parser import parse_xml
from wasde_data.registry import Registry


@dataclass
class UpdateSummary:
    seen: int = 0
    new: list[str] = field(default_factory=list)
    ingested: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


def load_state(path: Path) -> dict:
    if path.exists():
        return json.loads(path.read_text())
    return {}


def save_state(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2) + "\n")


def known_release_ids(con) -> set[str]:
    return {r[0] for r in con.execute("SELECT release_id FROM releases").fetchall()}


def ingest_release(con, rel: Release, cfg: PipelineConfig, registry: Registry,
                   download=None) -> int:
    """Archive + register + parse one release. Returns observation count."""
    kwargs = {"download": download} if download else {}
    archived = archive_release(rel, cfg.paths.releases,
                               sleep_seconds=cfg.esmis.sleep_seconds, **kwargs)
    db.upsert(con, "releases", pd.DataFrame([dict(
        release_id=rel.release_id, esmis_id=rel.esmis_id, title=rel.title,
        release_datetime=rel.release_datetime, report_month=rel.report_month,
        version=rel.version, is_latest=True, format_era=rel.format_era)]),
        ["release_id"])

    db.upsert(con, "release_files", pd.DataFrame([dict(
        release_id=a.release_id, ext=a.ext, url=a.url, local_path=str(a.local_path),
        sha256=a.sha256, bytes=a.bytes) for a in archived if a.canonical]),
        ["release_id", "ext"])

    if rel.format_era != "xml":
        db.recompute_is_latest(con)
        return 0  # historic-format release (shouldn't occur on the cron path)
    xml = next(a for a in archived if a.ext == "xml" and a.canonical)
    result = parse_xml(Path(xml.local_path).read_bytes(), registry)
    nr = normalize_cells(result.cells, rel.release_id, rel.report_month,
                         registry, cfg.priority_tables)
    con.execute("DELETE FROM observations WHERE release_id = ?", [rel.release_id])
    n = db.upsert(con, "observations", nr.observations,
                  ["release_id", "table_slug", "region", "commodity",
                   "attribute", "marketing_year", "forecast_month"])
    if not nr.unmapped.empty:
        db.upsert(con, "unmapped_labels", nr.unmapped,
                  ["release_id", "table_slug", "raw_label", "kind"])
    db.recompute_is_latest(con)
    return n


def run_update(con, cfg: PipelineConfig, registry: Registry,
               client: EsmisClient | None = None, download=None,
               progress=lambda msg: None) -> UpdateSummary:
    client = client or EsmisClient(cfg.esmis.base_url, cfg.paths.raw / "esmis_api",
                                   cfg.esmis.identifier)
    summary = UpdateSummary()
    state = load_state(cfg.paths.state)

    page = client.catalog_page(0, force=True)
    releases = [parse_release(e) for e in page["results"]]
    summary.seen = len(releases)

    known = known_release_ids(con) | set(state.get("seen_release_ids", []))
    new = [r for r in releases if r.release_id not in known]
    summary.new = [r.release_id for r in new]
    progress(f"catalog page 0: {len(releases)} releases, {len(new)} new")

    for rel in sorted(new, key=lambda r: r.release_datetime):
        try:
            n = ingest_release(con, rel, cfg, registry, download=download)
            summary.ingested.append(rel.release_id)
            progress(f"  ingested {rel.release_id}: {n} observations")
        except Exception as exc:  # noqa: BLE001 - record, keep state un-advanced
            summary.errors.append(f"{rel.release_id}: {exc}")
            progress(f"  ERROR {rel.release_id}: {exc}")

    if summary.ingested:
        export_all(con, cfg.paths.exports)
        progress("exports refreshed")

    if summary.ok:
        state["seen_release_ids"] = sorted(known | {r.release_id for r in releases})
        state["last_success"] = pd.Timestamp.now().isoformat(timespec="seconds")
        save_state(cfg.paths.state, state)
    return summary
