"""Shared data access + styling for the WASDE Vintage Explorer (read-only).

Reads the parquet exports in data/exports/ via in-memory DuckDB — never the live
wasde.duckdb file, which the OCR backfill may be writing. Caches bust automatically
when scripts/12_export.py regenerates the exports (mtime keyed).
"""

from __future__ import annotations

import sys
from pathlib import Path

import duckdb
import pandas as pd
import streamlit as st

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from wasde_data import analytics  # noqa: E402  (needs the sys.path line above)

EXPORTS = ROOT / "data" / "exports"
OBS = EXPORTS / "observations.parquet"
RELEASES = EXPORTS / "releases.parquet"

PALETTE = {
    "grain": "#D4A93D",   # grain amber (primary; sibling of dairy's butterfat gold)
    "prev": "#5B9BD5",    # prior-year blue
    "cut": "#C0504D",     # number revised down
    "raise": "#2E8B57",   # number revised up
    "muted": "#888888",
    "bg2": "#1A1F2B",
}

GREYS = ["#4a4f5a", "#565c69", "#636a78", "#717987", "#7f8896"]

# WASDE cycle order: a marketing year's first projection prints in May.
MONTH_CYCLE = ["May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
               "Jan", "Feb", "Mar", "Apr"]
MONTH_NUM = {m: i for i, m in enumerate(
    ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct",
     "Nov", "Dec"], start=1)}

# Balance-sheet display order; unknown attributes append alphabetically after these.
ATTR_ORDER = [
    "area_planted", "area_harvested", "yield_per_harvested_acre",
    "beginning_stocks", "production", "imports", "supply_total",
    "feed_residual", "feed_and_residual", "food_seed_industrial",
    "ethanol_for_fuel", "crush", "seed", "residual", "domestic_total",
    "exports", "use_total", "ending_stocks", "stocks_to_use", "farm_price",
]

PRICE_UNITS = {
    "usd_per_bushel", "usd_per_cwt", "usd_per_short_ton", "usd_per_metric_ton",
    "cents_per_pound", "usd_per_pound",
}

# Raising these raises supply → bearish for price; used by the price-impact toggle.
SUPPLY_SIDE = {
    "area_planted", "area_harvested", "yield_per_harvested_acre",
    "beginning_stocks", "production", "imports", "supply_total",
}

UNIT_LABELS = {
    "million_bushels": "mil bu", "bushels_per_acre": "bu/acre",
    "usd_per_bushel": "$/bu", "million_acres": "mil acres",
    "million_metric_tons": "MMT", "metric_tons_per_hectare": "t/ha",
    "million_cwt": "mil cwt", "usd_per_cwt": "$/cwt",
    "million_480lb_bales": "mil bales", "cents_per_pound": "c/lb",
    "thousand_short_tons": "k short tons", "usd_per_short_ton": "$/short ton",
    "million_pounds": "mil lb", "million_hectares": "mil ha",
    "thousand_acres": "k acres", "ratio": "ratio",
}

REGION_PINS = ["united_states", "world", "total_foreign", "china", "brazil",
               "argentina", "european_union", "major_exporters",
               "major_importers"]


def _exports_mtime() -> float:
    return max(p.stat().st_mtime for p in EXPORTS.glob("*.parquet"))


@st.cache_data(ttl=600)
def _query(sql: str, params: tuple, _mtime: float) -> pd.DataFrame:
    con = duckdb.connect()
    try:
        return con.execute(sql, list(params)).fetchdf()
    finally:
        con.close()


def query(sql: str, params: tuple = ()) -> pd.DataFrame:
    """Run SQL against the parquet exports; cache busts when exports regenerate."""
    return _query(sql, params, _exports_mtime())


# ------------------------------------------------------------------- dimensions


def pretty(name: str) -> str:
    return name.replace("_", " ").capitalize()


def unit_label(unit: str | None) -> str:
    if not unit or pd.isna(unit):
        return ""
    return UNIT_LABELS.get(unit, unit.replace("_", " "))


def dataset_label(slug: str, commodity: str) -> str:
    scope = "US" if slug.startswith("us_") else "World"
    label = f"{scope} — {commodity.replace('_', ' ').title()}"
    if slug.startswith("world_us_"):
        label += " (world & US)"
    return label


def datasets() -> pd.DataFrame:
    df = query(
        f"""
        SELECT table_slug, commodity, count(*) AS n
        FROM read_parquet('{OBS}')
        GROUP BY 1, 2
        ORDER BY (table_slug NOT LIKE 'us_%'), table_slug, commodity
        """
    )
    df["label"] = [dataset_label(s, c)
                   for s, c in zip(df["table_slug"], df["commodity"], strict=True)]
    return df


def regions(slug: str, commodity: str) -> list[str]:
    df = query(
        f"""
        SELECT DISTINCT region FROM read_parquet('{OBS}')
        WHERE table_slug = ? AND commodity = ?
        """,
        (slug, commodity),
    )
    found = set(df["region"])
    pinned = [r for r in REGION_PINS if r in found]
    rest = sorted(found - set(pinned))
    return pinned + rest


def attributes(slug: str, commodity: str, region: str) -> pd.DataFrame:
    """Attributes of a dataset with display unit (latest era — TXT-era units on
    price/area rows are mislabelled upstream) and first-published date."""
    df = query(
        f"""
        SELECT attribute,
               arg_max(unit, report_month) AS unit,
               min(report_month) AS first_report,
               max(report_month) AS last_report,
               count(*) AS n
        FROM read_parquet('{OBS}')
        WHERE table_slug = ? AND commodity = ? AND region = ?
        GROUP BY attribute
        """,
        (slug, commodity, region),
    )
    have = set(df["attribute"])
    if {"ending_stocks", "use_total"} <= have:
        es = df[df["attribute"] == "ending_stocks"].iloc[0]
        df = pd.concat(
            [df, pd.DataFrame([{
                "attribute": "stocks_to_use", "unit": "ratio",
                "first_report": es["first_report"], "last_report": es["last_report"],
                "n": es["n"],
            }])],
            ignore_index=True,
        )
    rank = {a: i for i, a in enumerate(ATTR_ORDER)}
    df["_rank"] = df["attribute"].map(lambda a: rank.get(a, len(ATTR_ORDER)))
    df = df.sort_values(["_rank", "attribute"]).drop(columns="_rank")
    return df.reset_index(drop=True)


def is_price(attribute: str, unit: str | None) -> bool:
    return attribute == "farm_price" or (unit in PRICE_UNITS if unit else False)


def marketing_years(slug: str, commodity: str, region: str) -> list[str]:
    df = query(
        f"""
        SELECT DISTINCT marketing_year FROM read_parquet('{OBS}')
        WHERE table_slug = ? AND commodity = ? AND region = ?
        ORDER BY marketing_year DESC
        """,
        (slug, commodity, region),
    )
    return df["marketing_year"].tolist()


def fetch_series(
    slug: str,
    commodity: str,
    region: str,
    attrs: list[str] | None = None,
    include_quarantined: bool = False,
) -> pd.DataFrame:
    """Long observation slice for a dataset; derives stocks_to_use on demand.

    Quarantined rows are excluded by default but available on demand — never
    silently dropped from the dataset itself.
    """
    want_stu = attrs is not None and "stocks_to_use" in attrs
    fetch_attrs = None
    if attrs is not None:
        fetch_attrs = [a for a in attrs if a != "stocks_to_use"]
        if want_stu:
            fetch_attrs = sorted({*fetch_attrs, "ending_stocks", "use_total"})
    sql = f"""
        SELECT release_id, report_month, table_slug, region, commodity, attribute,
               marketing_year, year_status, forecast_month, value, unit, qa_status
        FROM read_parquet('{OBS}')
        WHERE table_slug = ? AND commodity = ? AND region = ?
    """
    params: list = [slug, commodity, region]
    if fetch_attrs is not None:
        sql += f" AND attribute IN ({','.join('?' * len(fetch_attrs))})"
        params += fetch_attrs
    if not include_quarantined:
        sql += " AND qa_status IN ('ok', 'corrected')"
    df = query(sql, tuple(params))
    df["report_month"] = pd.to_datetime(df["report_month"])
    if want_stu:
        stu = analytics.derive_stocks_to_use(df)
        stu["qa_status"] = "ok"
        df = pd.concat([df, stu], ignore_index=True)
        keep = set(attrs) if attrs else None
        if keep:
            df = df[df["attribute"].isin(keep)]
    return df.reset_index(drop=True)


def release_calendar() -> pd.DataFrame:
    df = query(
        f"""
        SELECT release_id, report_month, release_datetime, format_era
        FROM read_parquet('{RELEASES}')
        WHERE is_latest ORDER BY report_month
        """
    )
    df["report_month"] = pd.to_datetime(df["report_month"])
    return df


# ------------------------------------------------------------------------ UI


def page_header(title: str, subtitle: str) -> None:
    st.title(title)
    st.caption(subtitle)


def sidebar_dataset_picker(world_default: bool = False) -> tuple[str, str, str]:
    """Global dataset/region selectors shared across pages via session_state keys."""
    ds = datasets()
    labels = ds["label"].tolist()
    default = labels.index("US — Corn") if "US — Corn" in labels else 0
    with st.sidebar:
        label = st.selectbox("Dataset", labels, index=default, key="ds_label")
        row = ds[ds["label"] == label].iloc[0]
        slug, commodity = row["table_slug"], row["commodity"]
        regs = regions(slug, commodity)
        if len(regs) > 1:
            region = st.selectbox(
                "Region", regs, format_func=pretty, key=f"ds_region_{slug}_{commodity}"
            )
        else:
            region = regs[0]
        st.toggle("Colour-blind safe palette", key="cb_safe")
        st.caption(
            "Diverging colours show the **direction of the revision** "
            "(red = number cut, green/blue = number raised), not price impact."
        )
    return slug, commodity, region


def diverging() -> list | str:
    """App-wide diverging colorscale, anchored at 0 via zmid at the call site."""
    if st.session_state.get("cb_safe"):
        return "RdBu"  # red = cut, blue = raise
    return [[0.0, PALETTE["cut"]], [0.5, PALETTE["bg2"]], [1.0, PALETTE["raise"]]]


STATUS_MARKERS = {
    "projection": "circle-open",
    "estimate": "circle",
    "actual": "diamond",
}


def apply_layout(fig, height: int = 420, **kwargs):
    fig.update_layout(
        height=height,
        margin=dict(t=40, b=10),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        legend=dict(orientation="h"),
        **kwargs,
    )
    return fig
