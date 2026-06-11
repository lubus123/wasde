"""Full QA sweep. Nonzero exit on 'fail'-severity findings (cron-friendly).

Writes data/exports/qa_report.csv and refreshes the qa_exceptions table.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from wasde_data import db
from wasde_data.config import load_config
from wasde_data.qa import run_all


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--all-tables", action="store_true",
                        help="run identity checks on every table, not just priority")
    args = parser.parse_args(argv)

    cfg = load_config()
    con = db.connect(cfg.paths.db)
    identity_tables = cfg.priority_tables + ["us_wheat", "world_corn",
                                             "world_soybeans", "world_soybean_meal",
                                             "world_soybean_oil", "world_wheat"]
    if args.all_tables:
        identity_tables = [r[0] for r in con.execute(
            "SELECT DISTINCT table_slug FROM observations").fetchall()]

    exceptions = run_all(con, cfg.priority_tables, identity_tables)
    con.execute("DELETE FROM qa_exceptions")
    if not exceptions.empty:
        db.upsert(con, "qa_exceptions", exceptions.drop_duplicates(
            subset=["release_id", "table_slug", "check_name", "detail"]),
            ["release_id", "table_slug", "check_name", "detail"])

    out = cfg.paths.exports / "qa_report.csv"
    out.parent.mkdir(parents=True, exist_ok=True)
    exceptions.to_csv(out, index=False)

    n_fail = int((exceptions.severity == "fail").sum())
    n_warn = int((exceptions.severity == "warn").sum())
    print(f"qa: {n_fail} fails, {n_warn} warns -> {out}")
    if len(exceptions):
        summary = exceptions.groupby(["check_name", "severity"]).size()
        print(summary.to_string())
        worst = exceptions[exceptions.severity == "fail"].head(15)
        if len(worst):
            print("\nsample failures:")
            for _, r in worst.iterrows():
                print(f"  {r.release_id} {r.table_slug} {r.check_name}: {r.detail}")
    return 1 if n_fail else 0


if __name__ == "__main__":
    raise SystemExit(main())
