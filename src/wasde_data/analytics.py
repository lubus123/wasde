"""Pure vintage-analytics over WASDE observation slices.

Every function is DataFrame-in / DataFrame-out with no I/O and no Streamlit so the
app layer stays thin and this logic sits under the coverage gate. Inputs are long
slices of the `observations` export for one or more series; a *series* is keyed by
``(table_slug, commodity, region, attribute)`` — slugs are not single-commodity, and
unit is deliberately NOT part of the key (TXT-era rows mislabel price/area units;
no cell is ever printed twice in different units within a release, verified
2026-06-11 against the full export).

Semantics encoded here (see docs/DECISIONS.md):

- A report's *headline* value for a cell is the row with ``forecast_month == ''``
  (actual/estimate/finalized) or ``forecast_month == <report month abbrev>``
  (current projection column). When both exist for the same cell — a 2010-H2
  artifact where world-table `actual` rows carry month labels — the unlabelled
  row wins.
- Month-over-month changes never use month arithmetic: the previous value is the
  *other printed column in the same release* (projections; handles the Oct-2013
  shutdown gap, whose Nov report reprints September) or the *previous available
  report's* headline (estimate/actual rows, single-column sub-tables like the
  us_wheat by-class blocks).
- "Final" is the last value WASDE ever printed for a (marketing_year, attribute)
  cell — the right benchmark for projection error, even though later NASS/ERS
  revisions may differ from it.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

SERIES_COLS = ("table_slug", "commodity", "region", "attribute")

_MIN_WILCOXON_N = 5


def _key_cols(obs: pd.DataFrame) -> list[str]:
    return [c for c in SERIES_COLS if c in obs.columns]


def _month_abbrev(report_month: pd.Series) -> pd.Series:
    return pd.to_datetime(report_month).dt.strftime("%b")


def current_values(obs: pd.DataFrame) -> pd.DataFrame:
    """One row per (series, marketing_year, report_month): the report's headline value.

    Keeps ``forecast_month == ''`` rows and current-month projection columns, resolves
    the labelled-duplicate artifact in favour of the unlabelled row, and drops cells
    the report did not actually print (null value).
    """
    keys = _key_cols(obs)
    abbrev = _month_abbrev(obs["report_month"])
    mask = (obs["forecast_month"] == "") | (obs["forecast_month"] == abbrev)
    cur = obs.loc[mask].copy()
    cur["_labelled"] = (cur["forecast_month"] != "").astype(int)
    cur = (
        cur.sort_values([*keys, "marketing_year", "report_month", "_labelled"])
        .drop_duplicates([*keys, "marketing_year", "report_month"], keep="first")
        .drop(columns="_labelled")
        .dropna(subset=["value"])
    )
    carry = [c for c in ("release_id", "year_status", "unit", "qa_status") if c in cur.columns]
    out = cur[[*keys, "marketing_year", "report_month", *carry, "value"]]
    return out.sort_values([*keys, "marketing_year", "report_month"]).reset_index(drop=True)


def vintage_series(
    obs: pd.DataFrame, marketing_years: list[str] | None = None
) -> pd.DataFrame:
    """Headline progression of each series, optionally restricted to given MYs."""
    cur = current_values(obs)
    if marketing_years is not None:
        cur = cur[cur["marketing_year"].isin(marketing_years)]
    return cur.reset_index(drop=True)


def mom_changes(obs: pd.DataFrame) -> pd.DataFrame:
    """Month-over-month revision per (series, marketing_year, report_month).

    Two regimes, recorded in ``method``:

    - ``within_report`` — projection rows: the previous value is the other printed
      projection column in the same release (NaN placeholder on a marketing year's
      first print → ``is_first_print``).
    - ``cross_release`` — estimate/actual rows and single-column projections: the
      previous *available* report's headline value.

    ``reprint_revision`` cross-checks the reprinted prev column against the previous
    report's headline — non-zero values are unflagged revisions, surfaced not hidden.
    ``pct_change`` uses an absolute-value denominator so negative-capable attributes
    keep a meaningful sign, and guards prev == 0 with NaN.
    """
    keys = _key_cols(obs)
    cur = current_values(obs)
    grp = cur.groupby([*keys, "marketing_year"], sort=False)
    cur["_chain_prev"] = grp["value"].shift(1)

    abbrev = _month_abbrev(obs["report_month"])
    stale = obs.loc[
        (obs["forecast_month"] != "") & (obs["forecast_month"] != abbrev)
    ].copy()
    stale = stale.sort_values("forecast_month").drop_duplicates(
        [*keys, "marketing_year", "report_month"], keep="first"
    )
    stale = stale[[*keys, "marketing_year", "report_month", "value"]].rename(
        columns={"value": "_companion"}
    )
    stale["_has_companion"] = True

    mom = cur.merge(stale, how="left", on=[*keys, "marketing_year", "report_month"])
    mom["_has_companion"] = mom["_has_companion"].fillna(False).astype(bool)

    within = (mom["year_status"] == "projection") & mom["_has_companion"]
    mom["method"] = np.where(within, "within_report", "cross_release")
    mom["value_prev"] = np.where(within, mom["_companion"], mom["_chain_prev"])
    mom["is_first_print"] = mom["value_prev"].isna()
    mom["delta"] = mom["value"] - mom["value_prev"]
    denom = mom["value_prev"].abs()
    mom["pct_change"] = mom["delta"] / denom.where(denom > 0)
    mom["reprint_revision"] = np.where(
        within, mom["_companion"] - mom["_chain_prev"], np.nan
    )
    mom = mom.drop(columns=["_chain_prev", "_companion", "_has_companion"])
    return mom.sort_values([*keys, "marketing_year", "report_month"]).reset_index(
        drop=True
    )


def final_values(obs: pd.DataFrame) -> pd.DataFrame:
    """Last value WASDE ever printed per (series, marketing_year).

    ``final_year_status`` lets callers grey out marketing years that never reached
    ``actual`` (recent years still in flight); ``n_reports`` counts prints.
    """
    keys = _key_cols(obs)
    cur = current_values(obs)
    grp = cur.groupby([*keys, "marketing_year"], sort=False)
    last = grp.tail(1).rename(
        columns={
            "value": "final_value",
            "report_month": "final_report_month",
            "year_status": "final_year_status",
        }
    )
    counts = grp.size().rename("n_reports").reset_index()
    cols = [*keys, "marketing_year", "final_value", "final_report_month", "final_year_status"]
    return last[cols].merge(counts, on=[*keys, "marketing_year"]).reset_index(drop=True)


def attach_errors(cur: pd.DataFrame, finals: pd.DataFrame) -> pd.DataFrame:
    """Join headline values to their final outcome.

    Adds ``pct_error = (value - final) / final`` (NaN when final is 0 or missing),
    ``horizon_months`` (calendar months until the final print) and ``calendar_month``.
    """
    keys = _key_cols(cur)
    err = cur.merge(finals, how="left", on=[*keys, "marketing_year"])
    final = err["final_value"]
    err["pct_error"] = (err["value"] - final) / final.where(final != 0)
    rm = pd.to_datetime(err["report_month"])
    fm = pd.to_datetime(err["final_report_month"])
    err["horizon_months"] = (fm.dt.year - rm.dt.year) * 12 + (fm.dt.month - rm.dt.month)
    err["calendar_month"] = rm.dt.month
    return err


def bias_summary(errors: pd.DataFrame, by: str = "calendar_month") -> pd.DataFrame:
    """Bias stats per (series, bucket): mean/median pct_error, MAE, hit rate, tests.

    ``hit_rate_over`` is the share of observations strictly above final. The Wilcoxon
    signed-rank p-value (two-sided, vs zero median) is NaN below 5 non-zero errors —
    too few pairs for the test to mean anything. Callers should pre-filter to
    finalized marketing years and ``horizon_months > 0`` (the final print itself has
    zero error by construction).
    """
    from scipy import stats  # heavy optional dep ([app] extra), loaded lazily

    keys = _key_cols(errors)

    def _stats(g: pd.Series) -> pd.Series:
        x = g.dropna().to_numpy()
        n = len(x)
        mean = x.mean() if n else np.nan
        std = x.std(ddof=1) if n > 1 else np.nan
        t_stat = mean / (std / np.sqrt(n)) if n > 1 and std > 0 else np.nan
        nonzero = x[x != 0]
        if len(nonzero) >= _MIN_WILCOXON_N:
            wilcoxon_p = stats.wilcoxon(nonzero).pvalue
        else:
            wilcoxon_p = np.nan
        return pd.Series(
            {
                "n": n,
                "mean_pct_error": mean,
                "median_pct_error": np.median(x) if n else np.nan,
                "mae_pct": np.abs(x).mean() if n else np.nan,
                "hit_rate_over": (x > 0).mean() if n else np.nan,
                "t_stat": t_stat,
                "wilcoxon_p": wilcoxon_p,
            }
        )

    out = (
        errors.groupby([*keys, by], sort=True)["pct_error"]
        .apply(_stats)
        .unstack()
        .reset_index()
    )
    out["n"] = out["n"].astype(int)
    return out


def derive_stocks_to_use(obs: pd.DataFrame) -> pd.DataFrame:
    """Synthesize a ``stocks_to_use`` pseudo-attribute (ending_stocks / use_total).

    Operates column-by-column (joins on forecast_month too) so the derived rows flow
    through :func:`current_values` / :func:`mom_changes` like any printed attribute.
    Zero or missing use_total yields NaN, never inf.
    """
    keys = [c for c in _key_cols(obs) if c != "attribute"]
    join = [
        *keys,
        "release_id",
        "report_month",
        "marketing_year",
        "year_status",
        "forecast_month",
    ]
    join = [c for c in join if c in obs.columns]
    es = obs[obs["attribute"] == "ending_stocks"][[*join, "value"]]
    ut = obs[obs["attribute"] == "use_total"][[*join, "value"]].rename(
        columns={"value": "_use"}
    )
    out = es.merge(ut, on=join, how="inner")
    out["value"] = out["value"] / out["_use"].where(out["_use"] != 0)
    out = out.drop(columns="_use")
    out["attribute"] = "stocks_to_use"
    out["unit"] = "ratio"
    return out.reset_index(drop=True)
