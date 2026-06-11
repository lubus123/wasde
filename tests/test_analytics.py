"""Tests for the pure vintage-analytics layer (src/wasde_data/analytics.py).

Golden chain: US corn ending_stocks 2012/13 (drought year) — fixture cut from
data/exports/observations.parquet, expected values hand-verified against the
printed reports (see tests/golden/corn_2012_13_mom_expected.csv header).
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from wasde_data import analytics

FIXTURE = Path(__file__).parent / "fixtures" / "app" / "us_corn_2012_vintage.parquet"
GOLDEN = Path(__file__).parent / "golden" / "corn_2012_13_mom_expected.csv"


@pytest.fixture(scope="module")
def corn() -> pd.DataFrame:
    df = pd.read_parquet(FIXTURE)
    df["report_month"] = pd.to_datetime(df["report_month"])
    return df


@pytest.fixture(scope="module")
def golden() -> pd.DataFrame:
    g = pd.read_csv(GOLDEN, comment="#", parse_dates=["report_month"])
    g["is_first_print"] = g["is_first_print"].astype(bool)
    return g


def _series(df: pd.DataFrame, attribute: str) -> pd.DataFrame:
    return df[df["attribute"] == attribute]


# ---------------------------------------------------------------- current_values


def test_current_values_one_row_per_report(corn):
    cur = analytics.current_values(corn)
    # 29 calendar months May-2012..Sep-2014 minus the Oct-2013 shutdown = 28 reports
    assert len(cur) == 28 * 3  # three attributes in the fixture
    assert not cur.duplicated(["attribute", "marketing_year", "report_month"]).any()
    es = cur[cur["attribute"] == "ending_stocks"].set_index("report_month")["value"]
    assert es[pd.Timestamp("2012-05-01")] == 1881  # May col, not the NaN Apr placeholder
    assert es[pd.Timestamp("2012-09-01")] == 733
    assert es[pd.Timestamp("2014-09-01")] == 821


def test_current_values_dedupes_anomalous_labelled_actuals():
    # 2010-H2 world tables print `actual` rows with a forecast_month label, sometimes
    # alongside an unlabelled row for the same cell. The unlabelled row must win and
    # the cell must appear exactly once.
    obs = pd.DataFrame(
        {
            "release_id": ["r1"] * 2 + ["r2"],
            "report_month": pd.to_datetime(["2010-08-01"] * 2 + ["2010-09-01"]),
            "attribute": ["production"] * 3,
            "marketing_year": ["2009/10"] * 3,
            "year_status": ["actual"] * 3,
            "forecast_month": ["Aug", "", "Sep"],
            "value": [99.0, 100.0, 101.0],
        }
    )
    cur = analytics.current_values(obs)
    assert len(cur) == 2
    by_month = cur.set_index("report_month")["value"]
    assert by_month[pd.Timestamp("2010-08-01")] == 100.0  # '' beats the labelled dup
    assert by_month[pd.Timestamp("2010-09-01")] == 101.0  # labelled-only row still kept


def test_current_values_drops_stale_projection_columns(corn):
    cur = analytics.current_values(_series(corn, "ending_stocks"))
    # The Aug-2012 report prints Jul (1183) and Aug (650); only Aug is the headline.
    assert cur.set_index("report_month")["value"][pd.Timestamp("2012-08-01")] == 650


# ------------------------------------------------------------------ mom_changes


def test_golden_corn_2012_13_mom_chain(corn, golden):
    mom = analytics.mom_changes(_series(corn, "ending_stocks"))
    mom = mom.sort_values("report_month").reset_index(drop=True)
    assert len(mom) == len(golden)
    pd.testing.assert_series_equal(
        mom["report_month"], golden["report_month"], check_names=False, check_dtype=False
    )
    np.testing.assert_allclose(mom["value"], golden["value"])
    np.testing.assert_allclose(mom["value_prev"], golden["value_prev"])
    np.testing.assert_allclose(mom["delta"], golden["delta"])
    assert mom["method"].tolist() == golden["method"].tolist()
    assert mom["is_first_print"].tolist() == golden["is_first_print"].tolist()
    assert mom["year_status"].tolist() == golden["year_status"].tolist()


def test_mom_shutdown_gap_uses_previous_available_report(corn):
    # Oct-2013 report does not exist; Nov-2013 must diff against Sep-2013, not NaN.
    mom = analytics.mom_changes(_series(corn, "ending_stocks"))
    row = mom[mom["report_month"] == "2013-11-01"].iloc[0]
    assert row["value_prev"] == 661
    assert row["delta"] == 163
    assert row["method"] == "cross_release"


def test_mom_first_print_excluded_from_changes(corn):
    mom = analytics.mom_changes(_series(corn, "ending_stocks"))
    first = mom[mom["report_month"] == "2012-05-01"].iloc[0]
    assert first["is_first_print"]
    assert np.isnan(first["delta"]) and np.isnan(first["pct_change"])


def test_mom_pct_change_uses_abs_denominator():
    # Negative-capable attributes (feed_and_residual, world residual) must not flip sign.
    obs = _synthetic_chain([("2020-05-01", -50.0), ("2020-06-01", -25.0)])
    mom = analytics.mom_changes(obs)
    row = mom[mom["report_month"] == "2020-06-01"].iloc[0]
    assert row["delta"] == 25.0
    assert row["pct_change"] == pytest.approx(0.5)


def test_mom_pct_change_zero_prev_guard():
    obs = _synthetic_chain([("2020-05-01", 0.0), ("2020-06-01", 10.0)])
    mom = analytics.mom_changes(obs)
    row = mom[mom["report_month"] == "2020-06-01"].iloc[0]
    assert row["delta"] == 10.0
    assert np.isnan(row["pct_change"])


def test_mom_single_column_projection_falls_back_to_cross_release():
    # us_wheat by-class projections print only one column (forecast_month='').
    obs = pd.DataFrame(
        {
            "release_id": ["r1", "r2"],
            "report_month": pd.to_datetime(["2024-05-01", "2024-06-01"]),
            "attribute": ["production"] * 2,
            "marketing_year": ["2024/25"] * 2,
            "year_status": ["projection"] * 2,
            "forecast_month": ["", ""],
            "value": [500.0, 520.0],
        }
    )
    mom = analytics.mom_changes(obs)
    assert mom["method"].tolist() == ["cross_release", "cross_release"]
    row = mom[mom["report_month"] == "2024-06-01"].iloc[0]
    assert row["delta"] == 20.0
    first = mom[mom["report_month"] == "2024-05-01"].iloc[0]
    assert first["is_first_print"]


def test_mom_reprint_revision_surfaced(corn):
    # The prev column reprinted in report N should equal report N-1's current value;
    # the whole golden era reprints cleanly, so every reprint_revision must be 0/NaN.
    mom = analytics.mom_changes(_series(corn, "ending_stocks"))
    reprints = mom["reprint_revision"].dropna()
    assert (reprints == 0).all()


# ----------------------------------------------------------------- final_values


def test_final_values_golden(corn):
    finals = analytics.final_values(corn)
    es = finals[finals["attribute"] == "ending_stocks"].iloc[0]
    assert es["final_value"] == 821
    assert es["final_report_month"] == pd.Timestamp("2014-09-01")
    assert es["final_year_status"] == "actual"
    assert es["n_reports"] == 28


# ---------------------------------------------------------------- attach_errors


def test_attach_errors_golden(corn):
    cur = analytics.current_values(_series(corn, "ending_stocks"))
    finals = analytics.final_values(_series(corn, "ending_stocks"))
    err = analytics.attach_errors(cur, finals)
    sep = err[err["report_month"] == "2012-09-01"].iloc[0]
    assert sep["pct_error"] == pytest.approx((733 - 821) / 821)
    assert sep["horizon_months"] == 24  # Sep-2012 -> final print Sep-2014
    assert sep["calendar_month"] == 9
    final_row = err[err["report_month"] == "2014-09-01"].iloc[0]
    assert final_row["pct_error"] == 0 and final_row["horizon_months"] == 0


def test_attach_errors_zero_final_guard():
    cur = pd.DataFrame(
        {
            "attribute": ["x"] * 2,
            "marketing_year": ["2020/21"] * 2,
            "report_month": pd.to_datetime(["2020-05-01", "2020-06-01"]),
            "year_status": ["projection", "actual"],
            "value": [5.0, 0.0],
        }
    )
    finals = analytics.final_values(
        cur.assign(release_id=["r1", "r2"], forecast_month=["", ""])
    )
    err = analytics.attach_errors(cur, finals)
    assert err["pct_error"].isna().all()


# ----------------------------------------------------------------- bias_summary


def test_bias_summary_systematic_positive_bias():
    rng = [0.05, 0.04, 0.06, 0.05, 0.07, 0.03, 0.05, 0.06]  # always overshoots
    errors = pd.DataFrame(
        {
            "attribute": ["yield"] * len(rng),
            "calendar_month": [9] * len(rng),
            "horizon_months": [12] * len(rng),
            "marketing_year": [f"{2010 + i}/{11 + i}" for i in range(len(rng))],
            "pct_error": rng,
        }
    )
    out = analytics.bias_summary(errors, by="calendar_month")
    row = out.iloc[0]
    assert row["n"] == 8
    assert row["mean_pct_error"] == pytest.approx(np.mean(rng))
    assert row["hit_rate_over"] == 1.0
    assert row["wilcoxon_p"] < 0.05


def test_bias_summary_unbiased_not_flagged():
    rng = [0.02, -0.02, 0.01, -0.01, 0.03, -0.03, 0.0, 0.005, -0.005, 0.015]
    errors = pd.DataFrame(
        {
            "attribute": ["yield"] * len(rng),
            "calendar_month": [9] * len(rng),
            "horizon_months": [12] * len(rng),
            "marketing_year": [str(y) for y in range(len(rng))],
            "pct_error": rng,
        }
    )
    out = analytics.bias_summary(errors, by="calendar_month")
    assert out.iloc[0]["wilcoxon_p"] > 0.05


def test_bias_summary_tiny_sample_no_pvalue():
    errors = pd.DataFrame(
        {
            "attribute": ["yield"],
            "calendar_month": [9],
            "horizon_months": [12],
            "marketing_year": ["2020/21"],
            "pct_error": [0.05],
        }
    )
    out = analytics.bias_summary(errors, by="calendar_month")
    assert np.isnan(out.iloc[0]["wilcoxon_p"])


# --------------------------------------------------------------- vintage_series


def test_vintage_series_filters_and_sorts(corn):
    v = analytics.vintage_series(corn, ["2012/13"])
    assert set(v["marketing_year"]) == {"2012/13"}
    assert v["report_month"].is_monotonic_increasing or (
        v.groupby("attribute")["report_month"].apply(
            lambda s: s.is_monotonic_increasing
        )
    ).all()


# ------------------------------------------------------------ derived attribute


def test_derive_stocks_to_use():
    obs = pd.DataFrame(
        {
            "release_id": ["r1"] * 4,
            "report_month": pd.to_datetime(["2020-05-01"] * 4),
            "attribute": ["ending_stocks", "use_total", "ending_stocks", "use_total"],
            "marketing_year": ["2020/21", "2020/21", "2019/20", "2019/20"],
            "year_status": ["projection"] * 2 + ["estimate"] * 2,
            "forecast_month": ["May", "May", "", ""],
            "value": [300.0, 1200.0, 250.0, 1000.0],
            "unit": ["million_bushels"] * 4,
        }
    )
    out = analytics.derive_stocks_to_use(obs)
    assert set(out["attribute"]) == {"stocks_to_use"}
    assert set(out["unit"]) == {"ratio"}
    by_my = out.set_index("marketing_year")["value"]
    assert by_my["2020/21"] == pytest.approx(0.25)
    assert by_my["2019/20"] == pytest.approx(0.25)


def test_derive_stocks_to_use_zero_use_guard():
    obs = pd.DataFrame(
        {
            "release_id": ["r1"] * 2,
            "report_month": pd.to_datetime(["2020-05-01"] * 2),
            "attribute": ["ending_stocks", "use_total"],
            "marketing_year": ["2020/21"] * 2,
            "year_status": ["projection"] * 2,
            "forecast_month": ["May", "May"],
            "value": [300.0, 0.0],
            "unit": ["million_bushels"] * 2,
        }
    )
    out = analytics.derive_stocks_to_use(obs)
    assert out["value"].isna().all()


# ----------------------------------------------------------------------- helpers


def _synthetic_chain(points: list[tuple[str, float]]) -> pd.DataFrame:
    """Single-attribute estimate-era chain (cross-release diffs)."""
    months = [p[0] for p in points]
    return pd.DataFrame(
        {
            "release_id": [f"r{i}" for i in range(len(points))],
            "report_month": pd.to_datetime(months),
            "attribute": ["feed_and_residual"] * len(points),
            "marketing_year": ["2019/20"] * len(points),
            "year_status": ["estimate"] * len(points),
            "forecast_month": [""] * len(points),
            "value": [p[1] for p in points],
        }
    )
