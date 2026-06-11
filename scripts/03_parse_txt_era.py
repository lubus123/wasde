"""Parse every TXT-era release (1995-01 -> 2010-06) into observations.

Idempotent: each release's observations and unmapped_labels are fully replaced
on re-parse. Structure errors and unknown tables are recorded per release in
unmapped_labels (kind='table'), never silently dropped.
"""

from __future__ import annotations

import argparse
import sys
import zipfile
from io import BytesIO
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from wasde_data import db
from wasde_data.config import load_config
from wasde_data.normalize import PriorityLabelMiss, normalize_cells
from wasde_data.parsers.txt_parser import parse_txt
from wasde_data.registry import Registry


def read_txt(local_path: str) -> bytes:
    path = Path(local_path)
    if path.suffix.lower() == ".zip":
        with zipfile.ZipFile(BytesIO(path.read_bytes())) as zf:
            names = [n for n in zf.namelist() if n.lower().endswith(".txt")]
            if not names:
                raise FileNotFoundError(f"no .txt inside {path.name}")
            return zf.read(names[0])
    return path.read_bytes()


def parse_release_txt(con, registry, cfg, release_id: str, report_month,
                      local_path: str, strict: bool) -> dict:
    result = parse_txt(read_txt(local_path), registry, str(report_month))
    nr = normalize_cells(result.cells, release_id, str(report_month), registry,
                         cfg.priority_tables, strict=strict)
    unmapped = nr.unmapped.copy()
    extra = [dict(release_id=release_id, table_slug="", raw_label=t, kind="table")
             for t in sorted(set(result.unknown_tables))]
    extra += [dict(release_id=release_id, table_slug="", raw_label=e,
                   kind="structure") for e in sorted(set(result.structure_errors))]
    if extra:
        unmapped = pd.concat([unmapped, pd.DataFrame(extra)], ignore_index=True)

    con.execute("DELETE FROM observations WHERE release_id = ?", [release_id])
    con.execute("DELETE FROM unmapped_labels WHERE release_id = ?", [release_id])
    n_obs = db.upsert(con, "observations", nr.observations,
                      ["release_id", "table_slug", "region", "commodity",
                       "attribute", "marketing_year", "forecast_month"])
    if not unmapped.empty:
        db.upsert(con, "unmapped_labels", unmapped,
                  ["release_id", "table_slug", "raw_label", "kind"])
    return dict(obs=n_obs, unmapped=len(unmapped))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release-id", default=None)
    parser.add_argument("--lenient", action="store_true",
                        help="don't hard-fail on priority-table label misses")
    args = parser.parse_args(argv)

    cfg = load_config()
    registry = Registry()
    con = db.connect(cfg.paths.db)

    where = ("format_era = 'txt' AND f.ext IN ('txt', 'zip') "
             "AND r.report_month < DATE '2010-07-01'")
    params: list = []
    if args.release_id:
        where += " AND release_id = ?"
        params.append(args.release_id)
    todo = con.execute(
        f"SELECT r.release_id, r.report_month, min(f.local_path) "
        f"FROM releases r JOIN release_files f USING (release_id) "
        f"WHERE {where} GROUP BY 1, 2 ORDER BY r.report_month", params).fetchall()
    print(f"{len(todo)} TXT releases to parse")

    failures, total_obs, total_unmapped = [], 0, 0
    for i, (release_id, report_month, local_path) in enumerate(todo, 1):
        try:
            stats = parse_release_txt(con, registry, cfg, release_id, report_month,
                                      local_path, strict=not args.lenient)
            total_obs += stats["obs"]
            total_unmapped += stats["unmapped"]
        except (PriorityLabelMiss, FileNotFoundError) as exc:
            failures.append((release_id, str(exc)))
            print(f"  FAIL {release_id}: {exc}", file=sys.stderr)
        if i % 25 == 0:
            print(f"  {i}/{len(todo)} parsed (obs={total_obs} unmapped={total_unmapped})")

    print(f"done: parsed={len(todo) - len(failures)} failed={len(failures)} "
          f"observations={total_obs} unmapped_labels={total_unmapped}")
    if total_unmapped:
        top = con.execute(
            "SELECT kind, raw_label, count(*) AS n FROM unmapped_labels u "
            "JOIN releases r USING (release_id) WHERE r.format_era = 'txt' "
            "GROUP BY kind, raw_label ORDER BY n DESC LIMIT 30").fetchall()
        print("top unmapped labels:")
        for kind, label, n in top:
            print(f"  {kind:10s} {label[:70]!r}  x{n}")
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
