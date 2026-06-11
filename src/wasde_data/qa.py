"""Quality assurance: a number is trusted because it survived checks.

Checks (each returns a DataFrame of exceptions: release_id, table_slug,
check_name, severity, detail, observed, expected):

- identities: Supply = Beginning + Production + Imports; Use = Domestic +
  Exports; Ending = Supply - Use. Tolerance scales with the printed rounding.
- mom_continuity: report N's prior-month projection column must equal report
  N-1's own-month column for the same cell (each number is printed twice).
- coverage: every release parsed into observations; priority tables present.
- unmapped: unmapped labels in priority tables (hard QA fail).
"""

from __future__ import annotations

import pandas as pd
import yaml

from wasde_data.agmanager import check_cross_source
from wasde_data.config import PROJECT_ROOT

# identity definitions per (table, commodity); attribute slug lists
_SUPPLY = ["beginning_stocks", "production", "imports"]
_SOY_USE = ["crush", "exports", "seed", "residual"]
# soybeans-era variants: some years print 'seed_and_residual' merged


def _decimals(v) -> int:
    if pd.isna(v) or float(v) == int(v):
        return 0
    return min(len(f"{float(v):.10f}".rstrip("0").split(".")[-1]), 6)


def _tolerance(values: pd.Series) -> float:
    """Half a unit of the coarsest printed decimal among the inputs, plus
    rounding headroom: components each round independently."""
    decimals = values.dropna().map(
        lambda v: 0 if float(v) == int(v) else len(str(v).split(".")[-1]))
    step = 10.0 ** -(decimals.min() if len(decimals) else 0)
    return step * (len(values) + 1) / 2


def _emit(rows: list, key: tuple, check: str, observed, expected, parts) -> None:
    if observed is None or expected is None:
        return
    tol = _tolerance(pd.Series(parts))
    if abs(observed - expected) > tol:
        rows.append(dict(
            release_id=key[0], table_slug=key[1], check_name=check,
            severity="fail",
            detail=(f"{key[2]}/{key[3]} {key[4]} {key[5] or 'base'}: "
                    f"|{observed} - {expected:.4g}| > {tol:.4g}"),
            observed=observed, expected=expected))


def _check_group(rows: list, key: tuple, vals: dict) -> None:
    def total(parts):
        present = [vals[p] for p in parts if vals.get(p) is not None]
        return sum(present) if present else None

    supply = vals.get("supply_total")
    # requires every component: wheat by-class prints supply WITH imports
    # folded in ('Supply, Total 3/') and no imports row — unverifiable there
    if supply is not None and all(vals.get(p) is not None for p in _SUPPLY):
        _emit(rows, key, "supply_identity", supply, total(_SUPPLY),
              [vals.get(p) for p in _SUPPLY])
    use = vals.get("use_total")
    if use is not None:
        if vals.get("domestic_total") is not None:
            _emit(rows, key, "use_identity", use,
                  total(["domestic_total", "exports"]),
                  [vals.get("domestic_total"), vals.get("exports")])
        elif vals.get("crush") is not None:
            parts = [p for p in (_SOY_USE + ["seed_and_residual"]) if p in vals]
            _emit(rows, key, "use_identity", use, total(parts),
                  [vals.get(p) for p in parts])
    ending = vals.get("ending_stocks")
    if ending is not None and supply is not None and use is not None:
        _emit(rows, key, "ending_identity", ending, supply - use, [supply, use])


def check_identities(obs: pd.DataFrame) -> pd.DataFrame:
    """Balance-sheet identities per (release, table, commodity, region, MY, month)."""
    rows: list[dict] = []
    key_cols = ["release_id", "table_slug", "commodity", "region",
                "marketing_year", "forecast_month"]
    for key, g in obs.groupby(key_cols):
        _check_group(rows, key, dict(zip(g.attribute, g.value, strict=False)))
    return pd.DataFrame(rows, columns=["release_id", "table_slug", "check_name",
                                       "severity", "detail", "observed", "expected"])


def check_mom_continuity(con, table_slugs: list[str]) -> pd.DataFrame:
    """Report N's prior-month column == report N-1's own-month column.

    Genuine inter-report revisions exist (USDA reprints a revised prior
    column); they surface here as warns to be whitelisted after verification
    against the PDF.
    """
    slugs = ", ".join(f"'{s}'" for s in table_slugs)
    df = con.execute(f"""
        WITH cur AS (
          SELECT o.*, strftime(o.report_month, '%b') AS own_abbr
          FROM observations o JOIN releases r USING (release_id)
          WHERE r.is_latest AND o.table_slug IN ({slugs})
            AND o.year_status = 'projection'
        )
        SELECT a.release_id, a.table_slug,
               a.commodity, a.region, a.marketing_year, a.attribute,
               a.forecast_month AS prior_col_month,
               a.value AS prior_col_value, b.value AS prev_report_value
        FROM cur a
        JOIN cur b
          ON b.report_month = a.report_month - INTERVAL 1 MONTH
         AND b.forecast_month = b.own_abbr
         AND a.forecast_month = b.own_abbr        -- a's prior column
         AND a.forecast_month <> a.own_abbr
         AND (a.table_slug, a.commodity, a.region, a.marketing_year, a.attribute)
           = (b.table_slug, b.commodity, b.region, b.marketing_year, b.attribute)
        WHERE a.value IS DISTINCT FROM b.value
    """).fetchdf()
    if df.empty:
        return pd.DataFrame(columns=["release_id", "table_slug", "check_name",
                                     "severity", "detail", "observed", "expected"])
    # one printed ULP: USDA re-rounds reprinted columns from unrounded
    # internals (45.12 -> 45.13, 1208 -> 1209); not a revision. Rows where one
    # side is missing entirely (a dropped sub-stock line) stay visible.
    ulp = df.apply(lambda r: 10.0 ** -min(_decimals(r.prior_col_value),
                                          _decimals(r.prev_report_value)), axis=1)
    df = df[((df.prior_col_value - df.prev_report_value).abs() > ulp * 1.01)
            | df.prior_col_value.isna() | df.prev_report_value.isna()]
    if df.empty:
        return pd.DataFrame(columns=["release_id", "table_slug", "check_name",
                                     "severity", "detail", "observed", "expected"])
    return pd.DataFrame(dict(
        release_id=df.release_id, table_slug=df.table_slug,
        check_name="mom_continuity", severity="warn",
        detail=(df.commodity + "/" + df.region + " " + df.marketing_year + " "
                + df.attribute + " prior-col(" + df.prior_col_month + ")"),
        observed=df.prior_col_value, expected=df.prev_report_value))


def check_coverage(con, priority_tables: list[str]) -> pd.DataFrame:
    rows = []
    missing = con.execute("""
        SELECT r.release_id, r.format_era FROM releases r
        LEFT JOIN (SELECT DISTINCT release_id FROM observations) o
          USING (release_id)
        WHERE o.release_id IS NULL AND r.format_era IN ('xml', 'txt')
    """).fetchall()
    for release_id, era in missing:
        rows.append(dict(release_id=release_id, table_slug="", check_name="coverage",
                         severity="fail", detail=f"{era} release has no observations",
                         observed=None, expected=None))
    slugs = ", ".join(f"'{s}'" for s in priority_tables)
    no_priority = con.execute(f"""
        SELECT o.release_id, count(DISTINCT o.table_slug)
        FROM observations o GROUP BY 1
        HAVING count(DISTINCT CASE WHEN o.table_slug IN ({slugs})
                                   THEN o.table_slug END) = 0
    """).fetchall()
    for release_id, _ in no_priority:
        rows.append(dict(release_id=release_id, table_slug="", check_name="coverage",
                         severity="fail", detail="no priority-table observations",
                         observed=None, expected=None))
    return pd.DataFrame(rows, columns=["release_id", "table_slug", "check_name",
                                       "severity", "detail", "observed", "expected"])


def check_unmapped(con, priority_tables: list[str]) -> pd.DataFrame:
    slugs = ", ".join(f"'{s}'" for s in priority_tables)
    df = con.execute(f"""
        SELECT release_id, table_slug, raw_label, kind FROM unmapped_labels
        WHERE table_slug IN ({slugs})
    """).fetchdf()
    if df.empty:
        return pd.DataFrame(columns=["release_id", "table_slug", "check_name",
                                     "severity", "detail", "observed", "expected"])
    return pd.DataFrame(dict(
        release_id=df.release_id, table_slug=df.table_slug,
        check_name="unmapped_label",
        severity=df.kind.map(lambda k: "warn" if k == "unit" else "fail"),
        detail=df.kind + ": " + df.raw_label, observed=None, expected=None))


def load_whitelist(path=None) -> list[dict]:
    path = path or PROJECT_ROOT / "config" / "qa_whitelist.yaml"
    if not path.exists():
        return []
    return yaml.safe_load(path.read_text()).get("whitelist", [])


def apply_whitelist(exceptions: pd.DataFrame, whitelist: list[dict]) -> pd.DataFrame:
    """Demote human-verified findings to severity='whitelisted' (still visible)."""
    if exceptions.empty:
        return exceptions
    exceptions = exceptions.copy()
    for entry in whitelist:
        mask = exceptions.check_name == entry["check_name"]
        if entry.get("release_id"):
            mask &= exceptions.release_id == entry["release_id"]
        if entry.get("detail_prefix"):
            mask &= exceptions.detail.str.startswith(entry["detail_prefix"])
        if entry.get("detail_contains"):
            mask &= exceptions.detail.str.contains(entry["detail_contains"],
                                                   regex=False)
        exceptions.loc[mask, "severity"] = "whitelisted"
    return exceptions


def run_all(con, priority_tables: list[str],
            identity_tables: list[str] | None = None) -> pd.DataFrame:
    identity_tables = identity_tables or priority_tables
    slugs = ", ".join(f"'{s}'" for s in identity_tables)
    obs = con.execute(
        f"SELECT * FROM observations WHERE table_slug IN ({slugs})").fetchdf()
    parts = [
        check_identities(obs),
        check_mom_continuity(con, identity_tables),
        check_coverage(con, priority_tables),
        check_unmapped(con, priority_tables),
        check_cross_source(con),
    ]
    return apply_whitelist(pd.concat(parts, ignore_index=True), load_whitelist())
