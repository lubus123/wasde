"""WASDE Vintage Explorer — latest report at a glance."""

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from lib import (
    PALETTE,
    analytics,
    apply_layout,
    attributes,
    diverging,
    fetch_series,
    page_header,
    pretty,
    release_calendar,
    sidebar_dataset_picker,
    unit_label,
)

st.set_page_config(page_title="WASDE Vintage Explorer", page_icon="🌽", layout="wide")
page_header(
    "WASDE Vintage Explorer",
    "Report-vintage view of every USDA WASDE since 1995 — what each report said, "
    "how it changed, and how those beliefs resolved. Data: data/exports/*.parquet.",
)

slug, commodity, region = sidebar_dataset_picker()
cal = release_calendar()
latest = cal.iloc[-1]
st.caption(
    f"Latest report: **{latest['release_id']}** · {len(cal)} reports loaded "
    f"({cal['report_month'].min():%b %Y} → {cal['report_month'].max():%b %Y})."
)

CARD_ATTRS = ["production", "exports", "ending_stocks", "stocks_to_use", "farm_price"]
attrs_meta = attributes(slug, commodity, region)
obs = fetch_series(slug, commodity, region, CARD_ATTRS)
mom = analytics.mom_changes(obs)
latest_month = mom["report_month"].max()
lm = mom[mom["report_month"] == latest_month]
# Newest marketing year with a projection in the latest report; else newest estimate.
proj = lm[lm["year_status"] == "projection"]
target_my = (proj if not proj.empty else lm)["marketing_year"].max()
lm = lm[lm["marketing_year"] == target_my]

st.subheader(f"{latest_month:%B %Y} report — {target_my}")
cards = st.columns(len(CARD_ATTRS))
# Latest-era display unit per attribute (TXT-era unit labels are unreliable upstream).
units = dict(zip(attrs_meta["attribute"], attrs_meta["unit"], strict=True))
for col, attr in zip(cards, CARD_ATTRS, strict=True):
    row = lm[lm["attribute"] == attr]
    if row.empty:
        col.metric(pretty(attr), "—")
        continue
    r = row.iloc[0]
    fmt = "{:,.3f}" if attr == "stocks_to_use" else "{:,.2f}"
    dfmt = "{:+,.4f}" if attr == "stocks_to_use" else "{:+,.2f}"
    delta = None if pd.isna(r["delta"]) or r["delta"] == 0 else dfmt.format(r["delta"])
    col.metric(
        f"{pretty(attr)} ({unit_label(units.get(attr))})",
        fmt.format(r["value"]),
        delta=delta,
        help=f"vs previous print ({r['method'].replace('_', ' ')}); "
             "no arrow = unchanged",
    )

# -------------------------------------------------------------------- waterfall
left, right = st.columns([1.1, 1])
wf_obs = fetch_series(
    slug, commodity, region,
    ["beginning_stocks", "production", "imports", "domestic_total", "exports",
     "ending_stocks"],
)
wf_mom = analytics.mom_changes(wf_obs)
wf = wf_mom[(wf_mom["report_month"] == latest_month)
            & (wf_mom["marketing_year"] == target_my)]
deltas = wf.set_index("attribute")["delta"]
have = {"beginning_stocks", "production", "imports", "domestic_total", "exports",
        "ending_stocks"} <= set(deltas.dropna().index)
with left:
    st.subheader("What moved the balance sheet")
    if not have:
        st.info("Full balance-sheet identity not published for this dataset/report "
                "(first print of a new marketing year, or non-balance table).")
    else:
        supply = ["beginning_stocks", "production", "imports"]
        use = ["domestic_total", "exports"]
        x = [*(pretty(a) for a in supply), *(f"− {pretty(a)}" for a in use),
             "= Ending stocks"]
        y = [*(deltas[a] for a in supply), *(-deltas[a] for a in use), None]
        wfig = go.Figure(go.Waterfall(
            x=x, y=y,
            measure=["relative"] * 5 + ["total"],
            increasing_marker_color=PALETTE["raise"],
            decreasing_marker_color=PALETTE["cut"],
            totals_marker_color=PALETTE["grain"],
            connector_line_color=PALETTE["muted"],
        ))
        apply_layout(wfig, height=380, yaxis_title="Δ vs previous print")
        st.plotly_chart(wfig, width="stretch")
        residual = (deltas["beginning_stocks"] + deltas["production"]
                    + deltas["imports"] - deltas["domestic_total"]
                    - deltas["exports"] - deltas["ending_stocks"])
        st.caption(
            f"Identity residual: **{residual:+,.2f}** (should be ~0; the dataset's "
            "QA contract checks supply = use + ending stocks on every report)."
        )

# ---------------------------------------------------------------- pulse heatmap
PULSE = [
    ("us_corn", "corn"), ("us_soybeans", "soybeans"),
    ("us_soybeans", "soybean_meal"), ("us_soybeans", "soybean_oil"),
    ("us_wheat", "wheat"), ("us_rice", "rice"), ("us_cotton", "cotton"),
    ("us_sugar", "sugar"), ("us_feed_coarse", "sorghum"),
    ("us_feed_coarse", "barley"), ("us_feed_coarse", "oats"),
]
PULSE_ATTRS = ["production", "exports", "ending_stocks", "farm_price"]
with right:
    st.subheader("All US tables, this report")
    rows = []
    for p_slug, p_com in PULSE:
        p_obs = fetch_series(p_slug, p_com, "united_states", PULSE_ATTRS)
        if p_obs.empty:
            continue
        p_mom = analytics.mom_changes(p_obs)
        p_lm = p_mom[p_mom["report_month"] == latest_month]
        p_proj = p_lm[p_lm["year_status"] == "projection"]
        if p_proj.empty:
            p_proj = p_lm
        if p_proj.empty:
            continue
        p_my = p_proj["marketing_year"].max()
        for _, r in p_proj[p_proj["marketing_year"] == p_my].iterrows():
            rows.append({"dataset": p_com.replace("_", " "),
                         "attribute": r["attribute"],
                         "pct": r["pct_change"] * 100})
    pulse = pd.DataFrame(rows)
    if pulse.empty:
        st.info("No data for the latest report month.")
    else:
        pz = pulse.pivot(index="dataset", columns="attribute", values="pct")
        pz = pz.reindex(columns=[a for a in PULSE_ATTRS if a in pz.columns])
        lim = max(abs(pz.fillna(0).values).max(), 0.1)
        pfig = go.Figure(go.Heatmap(
            z=pz.values, x=[pretty(a) for a in pz.columns], y=pz.index.tolist(),
            colorscale=diverging(), zmin=-lim, zmax=lim,
            text=[["" if pd.isna(v) else f"{v:+.1f}%" for v in row] for row in pz.values],
            texttemplate="%{text}", hoverongaps=False,
            colorbar=dict(title="%"),
        ))
        apply_layout(pfig, height=380)
        st.plotly_chart(pfig, width="stretch")
        st.caption("New-crop marketing year, % change vs previous print. "
                   "Blank = not published this month.")
