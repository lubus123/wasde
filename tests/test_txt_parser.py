import pandas as pd
import pytest

from wasde_data.config import load_config
from wasde_data.normalize import normalize_cells
from wasde_data.parsers.txt_parser import parse_txt
from wasde_data.registry import Registry


@pytest.fixture(scope="module")
def registry_m():
    return Registry()


def _golden_check(fixtures_dir, golden_dir, registry, fname, release_id,
                  report_month, golden_name):
    res = parse_txt((fixtures_dir / fname).read_bytes(), registry, report_month)
    nr = normalize_cells(res.cells, release_id, report_month, registry,
                         load_config().priority_tables)
    got = (nr.observations.drop(columns=["parsed_at"])
           [lambda d: d.table_slug.isin(["us_corn", "us_soybeans"])]
           .reset_index(drop=True))
    expected = pd.read_csv(golden_dir / golden_name, keep_default_na=False,
                           dtype={"forecast_month": str, "raw_commodity": str})
    pd.testing.assert_frame_equal(got.astype({"value": float}),
                                  expected.assign(value=expected.value.astype(float)),
                                  check_dtype=False)
    return res, nr


def test_golden_2005(fixtures_dir, golden_dir, registry_m):
    res, nr = _golden_check(fixtures_dir, golden_dir, registry_m,
                            "wasde-06-10-2005.txt", "wasde-2005-06-10",
                            "2005-06-01", "wasde2005_us_corn_soy.csv")
    assert nr.unmapped.empty
    assert res.structure_errors == []


def test_golden_1995(fixtures_dir, golden_dir, registry_m):
    res, nr = _golden_check(fixtures_dir, golden_dir, registry_m,
                            "wasde-06-12-1995.txt", "wasde-1995-06-12",
                            "1995-06-01", "wasde1995_us_corn_soy.csv")
    assert nr.unmapped.empty


def test_2005_corn_values_match_print(fixtures_dir, registry_m):
    """Hand-checked against the printed June 2005 report."""
    res = parse_txt((fixtures_dir / "wasde-06-10-2005.txt").read_bytes(),
                    registry_m, "2005-06-01")
    corn = {(c.marketing_year, c.forecast_month): c.value for c in res.cells
            if c.table_slug == "us_corn" and c.commodity == "corn"
            and c.raw_attribute == "Ending stocks, total"}
    assert corn == {("2003/04", ""): 958.0, ("2004/05", ""): 2215.0,
                    ("2005/06", "May"): 2540.0, ("2005/06", "Jun"): 2540.0}


def test_1995_wrapped_price_ranges_combine(fixtures_dir, registry_m):
    res = parse_txt((fixtures_dir / "wasde-06-12-1995.txt").read_bytes(),
                    registry_m, "1995-06-01")
    prices = {c.forecast_month: (c.value, c.raw_value) for c in res.cells
              if c.table_slug == "us_soybeans" and c.commodity == "soybeans"
              and "price" in c.raw_attribute.lower()
              and c.marketing_year == "1995/96"}
    assert prices == {"May": (5.6, "5.10 - 6.10"), "Jun": (5.75, "5.25 - 6.25")}


def test_world_corn_contd_may_june_pairs(fixtures_dir, registry_m):
    """Cont'd world tables carry May/June vintage pair rows."""
    res = parse_txt((fixtures_dir / "wasde-06-10-2005.txt").read_bytes(),
                    registry_m, "2005-06-01")
    us = {(c.marketing_year, c.year_status, c.forecast_month): c.value
          for c in res.cells if c.table_slug == "world_corn"
          and c.region == "united_states" and c.raw_attribute == "ending_stocks"}
    assert us == {("2003/04", "actual", ""): 24.34,
                  ("2004/05", "estimate", ""): 56.27,
                  ("2005/06", "projection", "May"): 64.53,
                  ("2005/06", "projection", "Jun"): 64.53}


def test_no_us_table_swallowed(fixtures_dir, registry_m):
    """Every US balance-sheet table present in the 2005 report parses."""
    res = parse_txt((fixtures_dir / "wasde-06-10-2005.txt").read_bytes(),
                    registry_m, "2005-06-01")
    tables = {c.table_slug for c in res.cells}
    assert {"us_wheat", "us_corn", "us_feed_coarse", "us_rice", "us_soybeans",
            "us_sugar", "us_cotton", "world_wheat", "world_coarse_grains",
            "world_corn", "world_soybeans", "world_soybean_meal",
            "world_soybean_oil"} <= tables
