import json

import httpx
import pytest
import respx

from wasde_data.config import load_config
from wasde_data.esmis import EsmisClient
from wasde_data.registry import Registry
from wasde_data.update import run_update

API = "https://esmis.nal.usda.gov/api/v1/release/findByIdentifier/wasde"


@pytest.fixture
def cfg(tmp_path):
    cfg = load_config()
    cfg.paths.db = tmp_path / "test.duckdb"
    cfg.paths.raw = tmp_path / "raw"
    cfg.paths.exports = tmp_path / "exports"
    cfg.esmis.sleep_seconds = 0
    return cfg


def _page_with_xml_release(fixtures_dir):
    page = json.loads((fixtures_dir / "esmis_page.json").read_text())
    page["results"] = [page["results"][0]]  # June 2026, xml era
    return page


def _fake_download(fixtures_dir):
    xml = (fixtures_dir / "wasde0626_trimmed.xml").read_bytes()

    def download(url):
        return xml if url.endswith(".xml") else b"placeholder-" + url.encode()
    return download


@respx.mock
def test_run_update_ingests_new_release(tmp_db, cfg, fixtures_dir):
    respx.get(API, params={"page": 0}).mock(
        return_value=httpx.Response(200, json=_page_with_xml_release(fixtures_dir)))
    client = EsmisClient(cfg.esmis.base_url, cfg.paths.raw / "esmis_api")
    summary = run_update(tmp_db, cfg, Registry(), client=client,
                         download=_fake_download(fixtures_dir))
    assert summary.ok
    assert summary.ingested == ["wasde-2026-06-11"]
    n = tmp_db.execute("SELECT count(*) FROM observations").fetchone()[0]
    assert n > 700
    assert (cfg.paths.exports / "us_corn_balance.parquet").exists()
    state = json.loads(cfg.paths.state.read_text())
    assert "wasde-2026-06-11" in state["seen_release_ids"]


@respx.mock
def test_run_update_is_noop_second_time(tmp_db, cfg, fixtures_dir):
    respx.get(API, params={"page": 0}).mock(
        return_value=httpx.Response(200, json=_page_with_xml_release(fixtures_dir)))
    client = EsmisClient(cfg.esmis.base_url, cfg.paths.raw / "esmis_api")
    download = _fake_download(fixtures_dir)
    first = run_update(tmp_db, cfg, Registry(), client=client, download=download)
    second = run_update(tmp_db, cfg, Registry(), client=client, download=download)
    assert first.ingested and second.ingested == []
    assert second.ok


@respx.mock
def test_v2_revision_supersedes(tmp_db, cfg, fixtures_dir):
    page = json.loads((fixtures_dir / "esmis_page.json").read_text())
    v1 = json.loads(json.dumps(page["results"][0]))
    v1["files"] = [f.replace("0626", "0526").replace("795937", "795903")
                   for f in v1["files"]]
    v1["id"] = "795903"
    v1["release_datetime"] = "2026-05-12T12:00:00+0000"
    v2 = page["results"][1]  # the real May v2 entry
    page1 = dict(page, results=[v1])
    page2 = dict(page, results=[v2, v1])

    route = respx.get(API, params={"page": 0})
    route.side_effect = [httpx.Response(200, json=page1),
                         httpx.Response(200, json=page2)]
    client = EsmisClient(cfg.esmis.base_url, cfg.paths.raw / "esmis_api")
    download = _fake_download(fixtures_dir)
    run_update(tmp_db, cfg, Registry(), client=client, download=download)
    run_update(tmp_db, cfg, Registry(), client=client, download=download)

    rows = dict(tmp_db.execute(
        "SELECT release_id, is_latest FROM releases ORDER BY release_id").fetchall())
    assert rows == {"wasde-2026-05-12": False, "wasde-2026-05-12-v2": True}


@respx.mock
def test_failed_ingest_does_not_advance_state(tmp_db, cfg, fixtures_dir):
    respx.get(API, params={"page": 0}).mock(
        return_value=httpx.Response(200, json=_page_with_xml_release(fixtures_dir)))
    client = EsmisClient(cfg.esmis.base_url, cfg.paths.raw / "esmis_api")

    def broken_download(url):
        raise RuntimeError("network down")

    summary = run_update(tmp_db, cfg, Registry(), client=client,
                         download=broken_download)
    assert not summary.ok
    assert not cfg.paths.state.exists()
    # rerun with working download succeeds (resume)
    summary2 = run_update(tmp_db, cfg, Registry(), client=client,
                          download=_fake_download(fixtures_dir))
    assert summary2.ok and summary2.ingested == ["wasde-2026-06-11"]
