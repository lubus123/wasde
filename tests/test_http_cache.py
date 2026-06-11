import httpx
import pytest
import respx

from wasde_data.http_cache import TransientHTTPError, cache_key, cached_get_bytes, cached_get_json


def test_cache_key_stable_and_param_sensitive():
    k1 = cache_key("https://x.test/a", {"p": 1})
    assert k1 == cache_key("https://x.test/a", {"p": 1})
    assert k1 != cache_key("https://x.test/a", {"p": 2})
    assert k1 != cache_key("https://x.test/b", {"p": 1})


@respx.mock
def test_cached_get_bytes_hits_network_once(tmp_path):
    route = respx.get("https://x.test/file").mock(
        return_value=httpx.Response(200, content=b"payload"))
    assert cached_get_bytes("https://x.test/file", tmp_path, suffix=".bin") == b"payload"
    assert cached_get_bytes("https://x.test/file", tmp_path, suffix=".bin") == b"payload"
    assert route.call_count == 1


@respx.mock
def test_force_bypasses_cache(tmp_path):
    route = respx.get("https://x.test/file").mock(
        return_value=httpx.Response(200, content=b"v2"))
    (tmp_path / f"{cache_key('https://x.test/file', None)}.bin").write_bytes(b"v1")
    assert cached_get_bytes("https://x.test/file", tmp_path, suffix=".bin",
                            force=True) == b"v2"
    assert route.call_count == 1


@respx.mock
def test_retries_transient_5xx_then_succeeds(tmp_path, monkeypatch):
    monkeypatch.setattr("wasde_data.http_cache._get.retry.wait", lambda *a, **k: 0)
    route = respx.get("https://x.test/flaky")
    route.side_effect = [httpx.Response(500), httpx.Response(200, content=b"ok")]
    assert cached_get_bytes("https://x.test/flaky", tmp_path) == b"ok"
    assert route.call_count == 2


@respx.mock
def test_404_raises_immediately(tmp_path):
    route = respx.get("https://x.test/missing").mock(return_value=httpx.Response(404))
    with pytest.raises(httpx.HTTPStatusError):
        cached_get_bytes("https://x.test/missing", tmp_path)
    assert route.call_count == 1


@respx.mock
def test_exhausted_retries_reraise(tmp_path, monkeypatch):
    monkeypatch.setattr("wasde_data.http_cache._get.retry.stop.max_attempt_number", 2)
    monkeypatch.setattr("wasde_data.http_cache._get.retry.wait", lambda *a, **k: 0)
    route = respx.get("https://x.test/down").mock(return_value=httpx.Response(503))
    with pytest.raises(TransientHTTPError):
        cached_get_bytes("https://x.test/down", tmp_path)
    assert route.call_count == 2


@respx.mock
def test_cached_get_json(tmp_path):
    respx.get("https://x.test/api").mock(
        return_value=httpx.Response(200, json={"results": [1, 2]}))
    assert cached_get_json("https://x.test/api", tmp_path) == {"results": [1, 2]}


def test_parsed_cell_contract():
    from wasde_data.records import ParsedCell
    cell = ParsedCell(table_slug="us_corn", region="united_states",
                      raw_commodity="Corn", raw_attribute="Ending Stocks",
                      marketing_year="2026/27", year_status="projection",
                      forecast_month="Jun", value=1750.0, raw_value="1,750",
                      unit_hint="Million bushels", source_format="xml")
    assert cell.value == 1750.0
    with pytest.raises(AttributeError):  # frozen
        cell.value = 0
