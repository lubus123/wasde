"""DuckDB schema and idempotent loads. All writes go through upsert()."""

from __future__ import annotations

from pathlib import Path

import duckdb
import pandas as pd

SCHEMA = """
CREATE TABLE IF NOT EXISTS releases (
  release_id TEXT PRIMARY KEY,        -- 'wasde-2026-06-11', 'wasde-2010-05-11-v2'
  esmis_id TEXT,
  title TEXT,
  release_datetime TIMESTAMP,         -- the vintage timestamp, from ESMIS
  report_month DATE,                  -- first of the report month
  version INT,                        -- 1 unless a vN revision
  is_latest BOOLEAN,                  -- newest version for its report_month
  format_era TEXT,                    -- 'xml' | 'txt' | 'pdf_scan'
  fetched_at TIMESTAMP);

CREATE TABLE IF NOT EXISTS release_files (
  release_id TEXT, ext TEXT, url TEXT, local_path TEXT,
  sha256 TEXT, bytes BIGINT, fetched_at TIMESTAMP,
  PRIMARY KEY (release_id, ext));

CREATE TABLE IF NOT EXISTS observations (
  release_id TEXT, report_month DATE,
  table_slug TEXT,                    -- 'us_corn', 'us_soybeans', 'world_corn', ...
  region TEXT,                        -- 'united_states', country slug, 'world'
  commodity TEXT,                     -- 'corn', 'soybeans', 'soybean_meal', ...
  attribute TEXT,                     -- 'ending_stocks', 'exports', ...
  marketing_year TEXT,                -- '2026/27'
  year_status TEXT,                   -- 'actual' | 'estimate' | 'projection'
  forecast_month TEXT,                -- 'May'/'Jun' for projection columns; '' otherwise
  value DOUBLE,                       -- as published; no unit conversion
  unit TEXT,                          -- canonical unit slug
  raw_attribute TEXT, raw_commodity TEXT,  -- exact printed labels (audit)
  source_format TEXT,                 -- 'xml' | 'txt' | 'ocr'
  qa_status TEXT DEFAULT 'ok',        -- ok | warn | quarantined | corrected
  parsed_at TIMESTAMP, fetched_at TIMESTAMP,
  PRIMARY KEY (release_id, table_slug, region, commodity,
               attribute, marketing_year, forecast_month));

CREATE TABLE IF NOT EXISTS qa_exceptions (
  release_id TEXT, table_slug TEXT, check_name TEXT,
  severity TEXT,                      -- 'warn' | 'fail'
  detail TEXT, observed DOUBLE, expected DOUBLE,
  fetched_at TIMESTAMP,
  PRIMARY KEY (release_id, table_slug, check_name, detail));

CREATE TABLE IF NOT EXISTS agmanager_obs (
  -- K-State final balance sheets per marketing year (not vintages): validates
  -- the finalized 'actual' columns of historical reports
  commodity TEXT, attribute TEXT, marketing_year TEXT,
  value DOUBLE, unit TEXT, source_note TEXT, fetched_at TIMESTAMP,
  PRIMARY KEY (commodity, attribute, marketing_year));

CREATE TABLE IF NOT EXISTS unmapped_labels (
  release_id TEXT, table_slug TEXT, raw_label TEXT,
  kind TEXT,                          -- attribute | commodity | table | unit
  fetched_at TIMESTAMP,
  PRIMARY KEY (release_id, table_slug, raw_label, kind));

CREATE OR REPLACE VIEW observations_latest AS
  SELECT o.* FROM observations o JOIN releases r USING (release_id)
  WHERE r.is_latest;

-- The headline number per report: finalized-year columns plus the projection
-- column belonging to the report's own month.
CREATE OR REPLACE VIEW vintage_current AS
  SELECT o.* FROM observations o JOIN releases r USING (release_id)
  WHERE r.is_latest
    AND (o.forecast_month = ''
         OR o.forecast_month = strftime(o.report_month, '%b'));

CREATE OR REPLACE VIEW us_corn_balance AS
  SELECT * FROM observations_latest WHERE table_slug = 'us_corn';

CREATE OR REPLACE VIEW us_soybeans_balance AS
  SELECT * FROM observations_latest
  WHERE table_slug IN ('us_soybeans', 'us_soybean_meal', 'us_soybean_oil');
"""


def connect(path: Path | str) -> duckdb.DuckDBPyConnection:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(path))
    con.execute(SCHEMA)
    return con


def recompute_is_latest(con: duckdb.DuckDBPyConnection) -> None:
    """One winner per report_month: prefer releases that actually parsed into
    observations (a replacement *notice* must not outrank the report it
    annotates), then highest version, then newest release_datetime."""
    con.execute("""
        UPDATE releases SET is_latest = (release_id = (
          SELECT r2.release_id FROM releases r2
          LEFT JOIN (SELECT release_id, count(*) AS n
                     FROM observations GROUP BY 1) o USING (release_id)
          WHERE r2.report_month = releases.report_month
          ORDER BY (coalesce(o.n, 0) > 0) DESC,
                   r2.version DESC, r2.release_datetime DESC
          LIMIT 1))""")


def upsert(con: duckdb.DuckDBPyConnection, table: str, df: pd.DataFrame,
           keys: list[str]) -> int:
    """Idempotent load: DELETE rows matching the incoming natural keys, then INSERT.

    Incoming duplicates on the key are collapsed to the last occurrence so a
    single load can never violate the primary key.
    """
    if df.empty:
        return 0
    df = df.drop_duplicates(subset=keys, keep="last").copy()
    df["fetched_at"] = pd.Timestamp.now()

    cols = [r[0] for r in con.execute(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_name = ? ORDER BY ordinal_position", [table]).fetchall()]
    for col in cols:
        if col not in df.columns:
            df[col] = None
    df = df[cols]

    con.register("_incoming", df)
    key_match = " AND ".join(f"t.{k} = i.{k}" for k in keys)
    con.execute(f"DELETE FROM {table} t WHERE EXISTS "
                f"(SELECT 1 FROM _incoming i WHERE {key_match})")
    con.execute(f"INSERT INTO {table} SELECT * FROM _incoming")
    con.unregister("_incoming")
    return len(df)
