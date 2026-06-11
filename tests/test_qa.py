import pandas as pd
import pytest

from wasde_data.qa import check_identities, check_unmapped


def _obs(attribute, value, **overrides):
    row = dict(release_id="wasde-2026-06-11", table_slug="us_corn",
               commodity="corn", region="united_states",
               marketing_year="2026/27", forecast_month="Jun",
               attribute=attribute, value=value)
    row.update(overrides)
    return row


def _balanced_rows(**overrides):
    rows = [_obs("beginning_stocks", 2145.0), _obs("production", 15995.0),
            _obs("imports", 25.0), _obs("supply_total", 18165.0),
            _obs("domestic_total", 13055.0), _obs("exports", 3150.0),
            _obs("use_total", 16205.0), _obs("ending_stocks", 1960.0)]
    for r in rows:
        r.update(overrides)
    return rows


def test_identities_pass_on_balanced_sheet():
    exceptions = check_identities(pd.DataFrame(_balanced_rows()))
    assert exceptions.empty


def test_identities_catch_one_bad_cell():
    rows = _balanced_rows()
    rows[1]["value"] = 15095.0  # production typo: 15995 -> 15095
    exceptions = check_identities(pd.DataFrame(rows))
    assert (exceptions.check_name == "supply_identity").any()
    assert (exceptions.severity == "fail").all()


def test_identities_tolerate_printed_rounding():
    # one-decimal MMT table: components round independently
    rows = [_obs("beginning_stocks", 30.9), _obs("production", 274.9),
            _obs("imports", 2.4), _obs("supply_total", 308.2),
            _obs("domestic_total", 225.7), _obs("exports", 53.7),
            _obs("use_total", 279.4), _obs("ending_stocks", 28.7)]
    exceptions = check_identities(pd.DataFrame(rows))
    assert exceptions.empty


def test_soybean_use_identity_via_crush_components():
    rows = [_obs("beginning_stocks", 340.0, commodity="soybeans"),
            _obs("production", 4435.0, commodity="soybeans"),
            _obs("imports", 25.0, commodity="soybeans"),
            _obs("supply_total", 4800.0, commodity="soybeans"),
            _obs("crush", 2750.0, commodity="soybeans"),
            _obs("exports", 1630.0, commodity="soybeans"),
            _obs("seed", 72.0, commodity="soybeans"),
            _obs("residual", 38.0, commodity="soybeans"),
            _obs("use_total", 4490.0, commodity="soybeans"),
            _obs("ending_stocks", 310.0, commodity="soybeans")]
    exceptions = check_identities(pd.DataFrame(rows))
    assert exceptions.empty


def test_unmapped_priority_fails(tmp_db):
    from wasde_data.db import upsert
    upsert(tmp_db, "unmapped_labels", pd.DataFrame([
        dict(release_id="r1", table_slug="us_corn", raw_label="Mystery Row",
             kind="attribute"),
        dict(release_id="r1", table_slug="us_corn", raw_label="Odd Unit",
             kind="unit"),
        dict(release_id="r1", table_slug="us_sugar", raw_label="Whatever",
             kind="attribute"),
    ]), ["release_id", "table_slug", "raw_label", "kind"])
    exceptions = check_unmapped(tmp_db, ["us_corn"])
    assert len(exceptions) == 2  # sugar not priority
    by_label = dict(zip(exceptions.detail, exceptions.severity, strict=True))
    assert by_label["attribute: Mystery Row"] == "fail"
    assert by_label["unit: Odd Unit"] == "warn"


@pytest.mark.parametrize("prior,current,expect_warn", [
    (52.4, 52.4, False),
    (52.4, 57.2, True),
])
def test_mom_continuity(tmp_db, prior, current, expect_warn):
    from wasde_data.db import upsert
    from wasde_data.qa import check_mom_continuity
    releases = pd.DataFrame([
        dict(release_id="wasde-2026-05-12", report_month="2026-05-01",
             release_datetime=pd.Timestamp("2026-05-12"), version=1,
             is_latest=True, format_era="xml", esmis_id="1", title="WASDE"),
        dict(release_id="wasde-2026-06-11", report_month="2026-06-01",
             release_datetime=pd.Timestamp("2026-06-11"), version=1,
             is_latest=True, format_era="xml", esmis_id="2", title="WASDE"),
    ])
    upsert(tmp_db, "releases", releases, ["release_id"])
    obs = pd.DataFrame([
        # May report, own-month (May) projection column
        dict(release_id="wasde-2026-05-12", report_month="2026-05-01",
             table_slug="us_corn", region="united_states", commodity="corn",
             attribute="ending_stocks", marketing_year="2026/27",
             year_status="projection", forecast_month="May", value=current,
             unit="million_bushels", raw_attribute="", raw_commodity="",
             source_format="xml", qa_status="ok", parsed_at=pd.Timestamp.now()),
        # June report, prior-month (May) column
        dict(release_id="wasde-2026-06-11", report_month="2026-06-01",
             table_slug="us_corn", region="united_states", commodity="corn",
             attribute="ending_stocks", marketing_year="2026/27",
             year_status="projection", forecast_month="May", value=prior,
             unit="million_bushels", raw_attribute="", raw_commodity="",
             source_format="xml", qa_status="ok", parsed_at=pd.Timestamp.now()),
    ])
    upsert(tmp_db, "observations", obs,
           ["release_id", "table_slug", "region", "commodity",
            "attribute", "marketing_year", "forecast_month"])
    exceptions = check_mom_continuity(tmp_db, ["us_corn"])
    assert (len(exceptions) > 0) == expect_warn
