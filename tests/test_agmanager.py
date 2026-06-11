from pathlib import Path

import pytest

from wasde_data.agmanager import _my_from_short, parse_corn, parse_soybeans

AGM_DIR = Path(__file__).resolve().parents[1] / "data" / "raw" / "agmanager"


@pytest.mark.parametrize("short,my", [
    ("73/74", "1973/74"),
    ("99/00", "1999/00"),
    ("00/01", "2000/01"),
    ("25/26", "2025/26"),
    ("Year", None),
])
def test_my_from_short(short, my):
    assert _my_from_short(short) == my


@pytest.mark.skipif(not (AGM_DIR / "CornSupplyDemand_64.xls").exists(),
                    reason="AgManager corn workbook not downloaded")
def test_parse_corn_real_workbook():
    df = parse_corn(AGM_DIR / "CornSupplyDemand_64.xls")
    assert df.marketing_year.min() == "1973/74"
    es = df[(df.attribute == "ending_stocks")].set_index("marketing_year").value
    assert 1973 + 53 > len(es) > 50  # one row per MY since 73/74
    # known final numbers (USDA feed grains database)
    assert es["1995/96"] == pytest.approx(426, abs=1)
    assert es["2012/13"] == pytest.approx(821, abs=1)


@pytest.mark.skipif(not (AGM_DIR / "SoybeanAnnualBalanceSheet_63.xls").exists(),
                    reason="AgManager soybean workbook not downloaded")
def test_parse_soybeans_real_workbook():
    df = parse_soybeans(AGM_DIR / "SoybeanAnnualBalanceSheet_63.xls")
    assert df.marketing_year.min() == "1973/74"
    ap = df[df.attribute == "area_planted"].set_index("marketing_year").value
    assert ap["1973/74"] == pytest.approx(56.5, abs=0.1)
    crush = df[df.attribute == "crush"].set_index("marketing_year").value
    assert len(crush) > 45
