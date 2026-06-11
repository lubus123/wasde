"""Cron entrypoint: poll ESMIS, ingest any new WASDE release end-to-end.

Safe to run daily; a no-op run costs one API request. Exit codes:
0 = clean (incl. no-op), 1 = ingest errors (state NOT advanced, rerun safe),
2 = QA failures after ingest (data loaded + quarantine-visible, investigate).
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from wasde_data import db
from wasde_data.config import load_config
from wasde_data.qa import run_all
from wasde_data.registry import Registry
from wasde_data.update import run_update


def main() -> int:
    cfg = load_config()
    registry = Registry()
    con = db.connect(cfg.paths.db)

    summary = run_update(con, cfg, registry, progress=print)
    print(f"update: seen={summary.seen} new={len(summary.new)} "
          f"ingested={len(summary.ingested)} errors={len(summary.errors)}")
    if summary.errors:
        for e in summary.errors:
            print(f"  error: {e}", file=sys.stderr)
        return 1

    if summary.ingested:
        exceptions = run_all(con, cfg.priority_tables)
        n_fail = int((exceptions.severity == "fail").sum())
        if n_fail:
            print(f"QA: {n_fail} failures on newly ingested data", file=sys.stderr)
            print(exceptions[exceptions.severity == "fail"]
                  .head(20).to_string(), file=sys.stderr)
            return 2
        print("QA clean on new data")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
