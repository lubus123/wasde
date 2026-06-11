"""Refresh parquet exports under data/exports/."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from wasde_data import db
from wasde_data.config import load_config
from wasde_data.export import export_all


def main() -> int:
    cfg = load_config()
    con = db.connect(cfg.paths.db)
    counts = export_all(con, cfg.paths.exports)
    for name, n in counts.items():
        print(f"  {name}.parquet: {n} rows")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
