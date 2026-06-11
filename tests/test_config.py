from wasde_data.config import PROJECT_ROOT, load_config


def test_load_config_resolves_paths():
    cfg = load_config()
    assert cfg.paths.db.is_absolute()
    assert cfg.paths.db == PROJECT_ROOT / "data" / "wasde.duckdb"
    assert cfg.paths.releases == PROJECT_ROOT / "data" / "raw" / "releases"
    assert cfg.paths.state == PROJECT_ROOT / "data" / "state.json"


def test_config_values():
    cfg = load_config()
    assert cfg.esmis.base_url == "https://esmis.nal.usda.gov"
    assert cfg.esmis.identifier == "wasde"
    assert "us_corn" in cfg.priority_tables
    assert cfg.eras.xml_start == "2010-07-01"
