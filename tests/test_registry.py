import pytest

from wasde_data.registry import normalize_label


@pytest.mark.parametrize("raw,expected", [
    ("Ending Stocks", "ending stocks"),
    ("Ending stocks, total        ", "ending stocks, total"),
    ("Food, seed & industrial", "food, seed & industrial"),
    ("Food, Seed &amp; Industrial", "food, seed & industrial"),
    ("Ethanol for fuel 2/", "ethanol for fuel"),
    ("Avg. farm price ($/bu) 3/", "avg. farm price ($/bu)"),
    ("Planted *", "planted"),
    ("  Supply,   total  :", "supply, total"),
    ("Area Harvested ", "area harvested"),
])
def test_normalize_label(raw, expected):
    assert normalize_label(raw) == expected


@pytest.mark.parametrize("raw,slug", [
    ("Ending Stocks", "ending_stocks"),
    ("Ending stocks, total", "ending_stocks"),
    ("Food, Seed &amp; Industrial", "food_seed_industrial"),
    ("Food, seed & industrial", "food_seed_industrial"),
    ("Feed and Residual", "feed_and_residual"),
    ("Avg. farm price ($/bu) 3/", "farm_price"),
    ("Yield per Harvested Acre", "yield_per_harvested_acre"),
    ("    Supply, Total", "supply_total"),
])
def test_resolve_attribute(registry, raw, slug):
    assert registry.resolve_attribute(raw) == slug


def test_unknown_attribute_returns_none(registry):
    assert registry.resolve_attribute("Some Never Seen Label") is None


def test_resolve_commodity(registry):
    assert registry.resolve_commodity("Soybean Meal") == "soybean_meal"
    assert registry.resolve_commodity("CORN") == "corn"
    assert registry.resolve_commodity("Total Grains 4/") == "total_grains"


def test_resolve_unit(registry):
    assert registry.resolve_unit("Million bushels") == "million_bushels"
    assert registry.resolve_unit("Million Metric Tons") == "million_metric_tons"


def test_resolve_table_by_title(registry):
    spec = registry.resolve_table("U.S. Feed Grain and Corn Supply and Use  1/")
    assert spec is not None and spec.slug == "us_corn"
    spec = registry.resolve_table(
        "U.S. Soybeans and Products Supply and Use (Domestic Measure)  1/")
    assert spec is not None and spec.slug == "us_soybeans"
    spec = registry.resolve_table("World Soybean Meal Supply and Use  1/")
    assert spec is not None and spec.slug == "world_soybean_meal"


def test_resolve_table_txt_era_headers(registry):
    assert registry.resolve_table("CORN").slug == "us_corn"
    assert registry.resolve_table("SOYBEANS AND PRODUCTS").slug == "us_soybeans"


def test_unknown_table_returns_none(registry):
    assert registry.resolve_table("Completely Unknown Table") is None


def test_alias_collision_detection(tmp_path):
    from wasde_data.registry import Registry
    d = tmp_path / "registry"
    d.mkdir()
    (d / "attributes.yaml").write_text(
        "attributes:\n  a:\n    aliases: [\"Same\"]\n  b:\n    aliases: [\"same\"]\n")
    (d / "commodities.yaml").write_text("commodities: {}\n")
    (d / "units.yaml").write_text("units: {}\n")
    (d / "tables.yaml").write_text("tables: {}\n")
    with pytest.raises(ValueError, match="maps to both"):
        Registry(d)
