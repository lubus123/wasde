"""Fetch + load AgManager (K-State) final corn/soy balance sheets.

Cross-source for QA only; lives in agmanager_obs, never in observations.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pandas as pd

from wasde_data import db
from wasde_data.agmanager import CORN_URL, SOY_URL, parse_corn, parse_soybeans
from wasde_data.config import load_config
from wasde_data.http_cache import _get


def main() -> int:
    cfg = load_config()
    con = db.connect(cfg.paths.db)
    agm_dir = cfg.paths.raw / "agmanager"
    agm_dir.mkdir(parents=True, exist_ok=True)

    frames = []
    for url, parser in [(CORN_URL, parse_corn), (SOY_URL, parse_soybeans)]:
        target = agm_dir / url.rsplit("/", 1)[-1]
        if not target.exists():
            target.write_bytes(_get(url, None, {"User-Agent": "Mozilla/5.0"}, 120))
            print(f"downloaded {target.name}")
        frames.append(parser(target))

    df = pd.concat(frames, ignore_index=True)
    # one-time migration: agmanager_obs was re-keyed (no report_month)
    cols = {r[0] for r in con.execute(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_name = 'agmanager_obs'").fetchall()}
    if "report_month" in cols:
        con.execute("DROP TABLE agmanager_obs")
        con.execute(db.SCHEMA)
    n = db.upsert(con, "agmanager_obs", df,
                  ["commodity", "attribute", "marketing_year"])
    span = con.execute("SELECT commodity, min(marketing_year), max(marketing_year), "
                       "count(*) FROM agmanager_obs GROUP BY 1").fetchall()
    print(f"loaded {n} rows")
    for c, lo, hi, cnt in span:
        print(f"  {c}: {lo} -> {hi} ({cnt} cells)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
