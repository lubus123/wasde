"""Parquet exports for cross-project consumption (corn-soy-mvp, dairy-model)."""

from __future__ import annotations

from pathlib import Path

EXPORTS = {
    "observations": "SELECT * FROM observations_latest",
    "observations_all_versions": "SELECT * FROM observations",
    "releases": "SELECT * FROM releases",
    "qa_exceptions": "SELECT * FROM qa_exceptions",
    "us_corn_balance": "SELECT * FROM us_corn_balance",
    "us_soybeans_balance": "SELECT * FROM us_soybeans_balance",
}


def export_all(con, exports_dir: Path) -> dict[str, int]:
    exports_dir.mkdir(parents=True, exist_ok=True)
    counts = {}
    for name, sql in EXPORTS.items():
        target = exports_dir / f"{name}.parquet"
        con.execute(f"COPY ({sql}) TO '{target}' (FORMAT PARQUET)")
        counts[name] = con.execute(f"SELECT count(*) FROM ({sql})").fetchone()[0]
    return counts
