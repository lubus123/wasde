import pandas as pd


def test_connect_creates_all_tables(tmp_db):
    tables = {r[0] for r in tmp_db.execute(
        "SELECT table_name FROM information_schema.tables").fetchall()}
    assert {"releases", "release_files", "observations", "qa_exceptions",
            "agmanager_obs", "unmapped_labels"} <= tables


def _obs_row(**overrides) -> dict:
    row = dict(
        release_id="wasde-2026-06-11", report_month="2026-06-01",
        table_slug="us_corn", region="united_states", commodity="corn",
        attribute="ending_stocks", marketing_year="2026/27",
        year_status="projection", forecast_month="Jun",
        value=1750.0, unit="million_bushels",
        raw_attribute="Ending Stocks", raw_commodity="Corn",
        source_format="xml", qa_status="ok", parsed_at=pd.Timestamp.now(),
    )
    row.update(overrides)
    return row


OBS_KEYS = ["release_id", "table_slug", "region", "commodity",
            "attribute", "marketing_year", "forecast_month"]


def test_upsert_is_idempotent(tmp_db):
    from wasde_data.db import upsert
    df = pd.DataFrame([_obs_row()])
    assert upsert(tmp_db, "observations", df, OBS_KEYS) == 1
    assert upsert(tmp_db, "observations", df, OBS_KEYS) == 1
    count = tmp_db.execute("SELECT count(*) FROM observations").fetchone()[0]
    assert count == 1


def test_upsert_replaces_value_on_same_key(tmp_db):
    from wasde_data.db import upsert
    upsert(tmp_db, "observations", pd.DataFrame([_obs_row(value=100.0)]), OBS_KEYS)
    upsert(tmp_db, "observations", pd.DataFrame([_obs_row(value=200.0)]), OBS_KEYS)
    value = tmp_db.execute("SELECT value FROM observations").fetchone()[0]
    assert value == 200.0


def test_upsert_collapses_incoming_duplicates(tmp_db):
    from wasde_data.db import upsert
    df = pd.DataFrame([_obs_row(value=1.0), _obs_row(value=2.0)])
    assert upsert(tmp_db, "observations", df, OBS_KEYS) == 1
    value = tmp_db.execute("SELECT value FROM observations").fetchone()[0]
    assert value == 2.0


def test_vintage_current_view_picks_report_own_month(tmp_db):
    from wasde_data.db import upsert
    upsert(tmp_db, "releases", pd.DataFrame([dict(
        release_id="wasde-2026-06-11", esmis_id="795937", title="WASDE",
        release_datetime=pd.Timestamp("2026-06-11 12:00:00"),
        report_month="2026-06-01", version=1, is_latest=True, format_era="xml",
    )]), ["release_id"])
    rows = [
        _obs_row(forecast_month="May", value=52.4),
        _obs_row(forecast_month="Jun", value=99.9),
        _obs_row(marketing_year="2024/25", year_status="actual",
                 forecast_month="", value=42.3),
    ]
    upsert(tmp_db, "observations", pd.DataFrame(rows), OBS_KEYS)
    got = dict(tmp_db.execute(
        "SELECT marketing_year, value FROM vintage_current").fetchall())
    assert got == {"2026/27": 99.9, "2024/25": 42.3}  # May column excluded


def test_observations_latest_excludes_superseded_versions(tmp_db):
    from wasde_data.db import upsert
    releases = pd.DataFrame([
        dict(release_id="wasde-2026-05-12", esmis_id="795903", title="WASDE",
             release_datetime=pd.Timestamp("2026-05-12 12:00:00"),
             report_month="2026-05-01", version=1, is_latest=False, format_era="xml"),
        dict(release_id="wasde-2026-05-12-v2", esmis_id="795903", title="WASDE",
             release_datetime=pd.Timestamp("2026-05-12 16:00:00"),
             report_month="2026-05-01", version=2, is_latest=True, format_era="xml"),
    ])
    upsert(tmp_db, "releases", releases, ["release_id"])
    rows = [_obs_row(release_id="wasde-2026-05-12", value=1.0),
            _obs_row(release_id="wasde-2026-05-12-v2", value=2.0)]
    upsert(tmp_db, "observations", pd.DataFrame(rows), OBS_KEYS)
    got = tmp_db.execute("SELECT release_id, value FROM observations_latest").fetchall()
    assert got == [("wasde-2026-05-12-v2", 2.0)]
