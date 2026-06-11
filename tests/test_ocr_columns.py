import pytest

from wasde_data.parsers.ocr_parser import _TOLERANCE_START, _derive_columns


def _months(cols):
    return [c["month"] for c in cols if c["status"] == "projection"]


def test_pair_era_june_1985():
    cols = _derive_columns("1985-06-01")
    assert [c["my"] for c in cols] == ["1983/84", "1984/85", "1985/86", "1985/86"]
    assert _months(cols) == ["May", "Jun"]


def test_january_pair_era_spans_year_boundary():
    cols = _derive_columns("1983-01-01")
    assert [c["my"] for c in cols] == ["1980/81", "1981/82", "1982/83", "1982/83"]
    assert _months(cols) == ["Dec", "Jan"]


def test_may_always_single_projection():
    cols = _derive_columns("1990-05-01")
    assert _months(cols) == ["May"]


@pytest.mark.parametrize("rm,n_proj", [
    ("1981-01-01", 1),   # single-column era (verified: 3 printed values)
    ("1981-06-01", 1),   # still single ('+300 to -300' era)
    ("1981-09-01", 2),   # pairs verified from Sep 1981
    ("1982-10-01", 2),
])
def test_single_vs_pair_era_boundary(rm, n_proj):
    assert len(_months(_derive_columns(rm))) == n_proj


@pytest.mark.parametrize("token", [
    "+/-22", "+21/=-21", "+17/", "+300", "to", "/", "±10", "422/-22",
])
def test_tolerance_markers_truncate(token):
    assert _TOLERANCE_START.search(token)


@pytest.mark.parametrize("token", ["4,175", "-15", "81.4", "1,947", "2"])
def test_data_tokens_not_tolerance(token):
    assert not _TOLERANCE_START.search(token)
