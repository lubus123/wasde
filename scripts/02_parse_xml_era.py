"""Parse every XML-era release (2010-07 -> present) into observations.

Idempotent: each release's observations and unmapped_labels are fully replaced
on re-parse, so registry/alias changes propagate by re-running this script.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from wasde_data import db
from wasde_data.config import load_config
from wasde_data.normalize import PriorityLabelMiss, normalize_cells
from wasde_data.parsers.xml_parser import XmlStructureError, parse_xml
from wasde_data.registry import Registry


def parse_release_xml(con, registry, cfg, release_id: str, report_month,
                      local_path: str, strict: bool) -> dict:
    content = Path(local_path).read_bytes()
    result = parse_xml(content, registry)
    nr = normalize_cells(result.cells, release_id, str(report_month), registry,
                         cfg.priority_tables, strict=strict)
    unmapped = nr.unmapped.copy()
    if result.unknown_tables:
        unknown = pd.DataFrame([dict(release_id=release_id, table_slug="",
                                     raw_label=t, kind="table")
                                for t in sorted(set(result.unknown_tables))])
        unmapped = pd.concat([unmapped, unknown], ignore_index=True)

    con.execute("DELETE FROM observations WHERE release_id = ?", [release_id])
    con.execute("DELETE FROM unmapped_labels WHERE release_id = ?", [release_id])
    n_obs = db.upsert(con, "observations", nr.observations,
                      ["release_id", "table_slug", "region", "commodity",
                       "attribute", "marketing_year", "forecast_month"])
    if not unmapped.empty:
        db.upsert(con, "unmapped_labels", unmapped,
                  ["release_id", "table_slug", "raw_label", "kind"])
    return dict(obs=n_obs, unmapped=len(unmapped),
                unknown_tables=len(result.unknown_tables))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release-id", default=None, help="parse a single release")
    parser.add_argument("--lenient", action="store_true",
                        help="don't hard-fail on priority-table label misses "
                             "(survey mode while growing the registry)")
    args = parser.parse_args(argv)

    cfg = load_config()
    registry = Registry()
    con = db.connect(cfg.paths.db)

    where = "format_era = 'xml'"
    params: list = []
    if args.release_id:
        where += " AND release_id = ?"
        params.append(args.release_id)
    todo = con.execute(
        f"SELECT r.release_id, r.report_month, f.local_path "
        f"FROM releases r JOIN release_files f USING (release_id) "
        f"WHERE {where} AND f.ext = 'xml' ORDER BY r.report_month", params).fetchall()
    print(f"{len(todo)} XML releases to parse")

    failures, total_obs, total_unmapped = [], 0, 0
    for i, (release_id, report_month, local_path) in enumerate(todo, 1):
        try:
            stats = parse_release_xml(con, registry, cfg, release_id, report_month,
                                      local_path, strict=not args.lenient)
            total_obs += stats["obs"]
            total_unmapped += stats["unmapped"]
        except (XmlStructureError, PriorityLabelMiss) as exc:
            failures.append((release_id, str(exc)))
            print(f"  FAIL {release_id}: {exc}", file=sys.stderr)
        if i % 25 == 0:
            print(f"  {i}/{len(todo)} parsed (obs={total_obs} unmapped={total_unmapped})")

    db.recompute_is_latest(con)
    print(f"done: parsed={len(todo) - len(failures)} failed={len(failures)} "
          f"observations={total_obs} unmapped_labels={total_unmapped}")
    if total_unmapped:
        top = con.execute(
            "SELECT kind, raw_label, count(*) AS n FROM unmapped_labels "
            "GROUP BY kind, raw_label ORDER BY n DESC LIMIT 25").fetchall()
        print("top unmapped labels (kind, label, releases):")
        for kind, label, n in top:
            print(f"  {kind:10s} {label!r}  x{n}")
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
