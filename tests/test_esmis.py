import json
from datetime import datetime

import httpx
import respx

from wasde_data.esmis import EsmisClient, parse_release

API = "https://esmis.nal.usda.gov/api/v1/release/findByIdentifier/wasde"


def _fixture_page(fixtures_dir):
    return json.loads((fixtures_dir / "esmis_page.json").read_text())


def test_parse_release_modern(fixtures_dir):
    page = _fixture_page(fixtures_dir)
    rel = parse_release(page["results"][0])
    assert rel.esmis_id == "795937"
    assert rel.release_datetime == datetime(2026, 6, 11, 12, 0)
    assert rel.release_id == "wasde-2026-06-11"
    assert rel.report_month == "2026-06-01"
    assert rel.version == 1
    assert rel.format_era == "xml"
    assert rel.file_for("xml").endswith("wasde0626.xml")
    assert rel.file_for("doc") is None


def test_parse_release_v2_revision(fixtures_dir):
    rel = parse_release(_fixture_page(fixtures_dir)["results"][1])
    assert rel.version == 2
    assert rel.release_id == "wasde-2026-05-12-v2"
    assert rel.format_era == "xml"


def test_parse_release_legacy_eras(fixtures_dir):
    page = _fixture_page(fixtures_dir)
    txt_rel = parse_release(page["results"][2])
    assert txt_rel.release_id == "wasde-1995-06-12"
    assert txt_rel.format_era == "txt"
    pdf_rel = parse_release(page["results"][3])
    assert pdf_rel.format_era == "pdf_scan"


@respx.mock
def test_all_releases_single_page(tmp_path, fixtures_dir):
    respx.get(API, params={"page": 0}).mock(
        return_value=httpx.Response(200, json=_fixture_page(fixtures_dir)))
    client = EsmisClient("https://esmis.nal.usda.gov", tmp_path)
    releases = client.all_releases()
    assert [r.esmis_id for r in releases] == ["795937", "795903", "95857", "96138"]


@respx.mock
def test_all_releases_dedups_pagination_overlap(tmp_path, fixtures_dir):
    page0 = _fixture_page(fixtures_dir)
    page0["pager"]["total_pages"] = 2
    page1 = _fixture_page(fixtures_dir)
    page1["pager"]["total_pages"] = 2
    page1["results"] = page0["results"][2:]  # overlap: same releases shifted down
    respx.get(API, params={"page": 0}).mock(return_value=httpx.Response(200, json=page0))
    respx.get(API, params={"page": 1}).mock(return_value=httpx.Response(200, json=page1))
    client = EsmisClient("https://esmis.nal.usda.gov", tmp_path)
    releases = client.all_releases()
    assert len(releases) == 4  # not 6


def test_latest_release_ids_prefers_higher_version():
    from wasde_data.esmis import Release, latest_release_ids
    v1 = Release("795903", "WASDE", datetime(2026, 5, 12, 12, 0),
                 files=("https://x.test/wasde0526.xml",))
    v2 = Release("795903", "WASDE", datetime(2026, 5, 12, 16, 0),
                 files=("https://x.test/wasde0526v2.xml",))
    other = Release("795937", "WASDE", datetime(2026, 6, 11, 12, 0),
                    files=("https://x.test/wasde0626.xml",))
    assert latest_release_ids([v1, v2, other]) == {"wasde-2026-05-12-v2",
                                                   "wasde-2026-06-11"}
