import pandas as pd
import pytest

from wasde_data.normalize import clean_number, normalize_cells
from wasde_data.parsers.xml_parser import parse_market_year, parse_xml
from wasde_data.registry import Registry, slugify_region

PRIORITY = ["us_corn", "us_soybeans", "us_soybean_meal", "us_soybean_oil"]


@pytest.fixture(scope="module")
def parsed(fixtures_dir_module):
    reg = Registry()
    content = (fixtures_dir_module / "wasde0626_trimmed.xml").read_bytes()
    return parse_xml(content, reg), reg


@pytest.fixture(scope="module")
def fixtures_dir_module():
    from tests.conftest import FIXTURES
    return FIXTURES


@pytest.mark.parametrize("raw,expected", [
    ("2024/25", ("2024/25", "actual")),
    ("2025/26 Est.", ("2025/26", "estimate")),
    ("2026/27 Proj.", ("2026/27", "projection")),
    ("2026/27  Proj.", ("2026/27", "projection")),
    ("World", None),
    ("", None),
])
def test_parse_market_year(raw, expected):
    assert parse_market_year(raw) == expected


@pytest.mark.parametrize("raw,expected", [
    ("1,233.20", 1233.20),
    ("106.2 *", 106.2),
    ("4.40", 4.40),
    ("NA", None),
    ("---", None),
    ("", None),
    ("2.20-2.30", 2.25),       # pre-2007 price range -> midpoint
    ("1.55- 1.95", 1.75),
    ("-12.5", -12.5),
])
def test_clean_number(raw, expected):
    assert clean_number(raw) == expected


@pytest.mark.parametrize("raw,slug", [
    ("        Argentina", "argentina"),
    ("World  3/", "world"),
    ("    World Less China", "world_less_china"),
    ("C. Amer & Carib  8/", "c_amer_carib"),
    ("United States", "united_states"),
    ("Total /2 Domestic", "total_domestic"),
])
def test_slugify_region(raw, slug):
    assert slugify_region(raw) == slug


def test_parse_xml_covers_all_fixture_tables(parsed):
    result, _ = parsed
    tables = {c.table_slug for c in result.cells}
    assert tables == {"us_corn", "us_soybeans", "world_corn"}
    assert result.unknown_tables == []


def test_golden_us_corn_soy(parsed, golden_dir):
    result, reg = parsed
    from wasde_data.config import load_config
    nr = normalize_cells(result.cells, "wasde-2026-06-11", "2026-06-01",
                         reg, load_config().priority_tables)
    got = (nr.observations.drop(columns=["parsed_at"])
           [lambda d: d.table_slug.isin(["us_corn", "us_soybeans"])]
           .reset_index(drop=True))
    expected = pd.read_csv(golden_dir / "wasde0626_us_corn_soy.csv",
                           keep_default_na=False,
                           dtype={"forecast_month": str, "raw_commodity": str})
    expected["value"] = expected["value"].astype(float)
    got = got.astype({"value": float})
    pd.testing.assert_frame_equal(got.reset_index(drop=True), expected,
                                  check_dtype=False)
    assert len(nr.unmapped) == 0


def test_balance_identities_hold_in_fixture(parsed):
    result, reg = parsed
    from wasde_data.config import load_config
    nr = normalize_cells(result.cells, "wasde-2026-06-11", "2026-06-01",
                         reg, load_config().priority_tables)
    df = nr.observations
    corn = df[(df.table_slug == "us_corn") & (df.commodity == "corn")
              & (df.marketing_year == "2026/27") & (df.forecast_month == "Jun")]
    vals = dict(zip(corn.attribute, corn.value, strict=True))
    assert vals["supply_total"] == pytest.approx(
        vals["beginning_stocks"] + vals["production"] + vals["imports"])
    assert vals["use_total"] == pytest.approx(
        vals["domestic_total"] + vals["exports"])
    assert vals["ending_stocks"] == pytest.approx(
        vals["supply_total"] - vals["use_total"])


def test_world_corn_unit_consistency_with_us_corn(parsed):
    """US ending stocks appear in bushels (sr12) and MMT (sr22) — they must agree."""
    result, reg = parsed
    from wasde_data.config import load_config
    nr = normalize_cells(result.cells, "wasde-2026-06-11", "2026-06-01",
                         reg, load_config().priority_tables)
    df = nr.observations
    sel = (df.marketing_year == "2026/27") & (df.forecast_month == "Jun") \
        & (df.attribute == "ending_stocks")
    bu = df[sel & (df.table_slug == "us_corn") & (df.commodity == "corn")].value.iloc[0]
    mmt = df[sel & (df.table_slug == "world_corn")
             & (df.region == "united_states")].value.iloc[0]
    assert bu * 0.0254 == pytest.approx(mmt, abs=0.05)  # bushel-corn = 25.4 kg


def test_positional_drift_is_a_hard_error(parsed, fixtures_dir):
    """Swap the configured matrix order for us_corn -> unit signature must trip."""
    import yaml

    from wasde_data.config import PROJECT_ROOT
    from wasde_data.parsers.xml_parser import XmlStructureError
    reg_dir = PROJECT_ROOT / "config" / "registry"
    tables = yaml.safe_load((reg_dir / "tables.yaml").read_text())
    tables["tables"]["us_corn"]["matrices"] = list(
        reversed(tables["tables"]["us_corn"]["matrices"]))
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        from pathlib import Path
        d = Path(td)
        (d / "tables.yaml").write_text(yaml.safe_dump(tables))
        for name in ["attributes.yaml", "commodities.yaml", "units.yaml"]:
            (d / name).write_text((reg_dir / name).read_text())
        bad_reg = Registry(d)
        with pytest.raises(XmlStructureError, match="us_corn"):
            parse_xml((fixtures_dir / "wasde0626_trimmed.xml").read_bytes(), bad_reg)
