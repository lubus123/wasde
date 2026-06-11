"""Live smoke tests — deselect with -m 'not integration'."""

import pytest

pytestmark = pytest.mark.integration


def test_esmis_catalog_page_live(tmp_path):
    from wasde_data.esmis import EsmisClient
    client = EsmisClient("https://esmis.nal.usda.gov", tmp_path)
    page = client.catalog_page(0, force=True)
    assert page["pager"]["total_results"] >= 699
    releases = [r for r in page["results"]]
    assert releases and releases[0]["identifier"] == ["wasde"]
