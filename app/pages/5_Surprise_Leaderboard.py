"""Surprise leaderboard — the biggest prints in three decades, and where today sits."""

import numpy as np
import plotly.graph_objects as go
import streamlit as st
from lib import (
    PALETTE,
    analytics,
    apply_layout,
    attributes,
    fetch_series,
    page_header,
    pretty,
    sidebar_dataset_picker,
)
from plotly.subplots import make_subplots

st.set_page_config(page_title="Surprise leaderboard", page_icon="⚡", layout="wide")
page_header(
    "Surprise leaderboard",
    "The biggest single-report revisions since 1995 — sized within each attribute's "
    "own calendar-month history, because a 5% August yield cut and a 5% February "
    "tweak are different animals.",
)

slug, commodity, region = sidebar_dataset_picker()
attrs_df = attributes(slug, commodity, region)
attr_list = [a for a in attrs_df["attribute"] if a != "stocks_to_use"]

c1, c2 = st.columns([1.6, 1.2])
sel_attrs = c1.multiselect("Attributes", attr_list, default=attr_list,
                           format_func=pretty)
norm = c2.radio("Rank by", ["z-score within calendar month", "% vs prior print"],
                horizontal=True)
if not sel_attrs:
    st.info("Select at least one attribute.")
    st.stop()

obs = fetch_series(slug, commodity, region, sel_attrs)
mom = analytics.mom_changes(obs)
mom = mom[~mom["is_first_print"] & mom["pct_change"].notna()].copy()
mom["pct"] = mom["pct_change"] * 100
mom["cal_month"] = mom["report_month"].dt.month

g = mom.groupby(["attribute", "cal_month"])["pct"]
mom["z"] = (mom["pct"] - g.transform("mean")) / g.transform("std").replace(0, np.nan)
mom["score"] = mom["z"] if norm.startswith("z") else mom["pct"]
mom = mom.dropna(subset=["score"])
if mom.empty:
    st.info("No revisions to rank for this selection.")
    st.stop()

# Sub-0.5% moves can carry huge z (revisions of long-settled years are almost
# always zero, so their std is tiny) — not tradeable surprises; keep them out.
ranked = mom[mom["pct"].abs() >= 0.5]
top = ranked.reindex(ranked["score"].abs().sort_values(ascending=False).index).head(25)
top = top.iloc[::-1]
labels = [
    f"{r.report_month:%b %Y} · {pretty(r.attribute)} · {r.marketing_year}"
    for r in top.itertuples()
]
texts = [f"{r.pct:+.1f}% (z {r.z:+.1f})" for r in top.itertuples()]
lfig = go.Figure(go.Bar(
    x=top["score"], y=labels, orientation="h",
    marker_color=[PALETTE["raise"] if s > 0 else PALETTE["cut"] for s in top["score"]],
    text=texts, textposition="auto", insidetextanchor="middle",
))
apply_layout(lfig, height=70 + 26 * len(top),
             xaxis_title="z-score" if norm.startswith("z") else "% change")
lfig.update_layout(margin=dict(l=10, r=80))
st.plotly_chart(lfig, width="stretch")
st.caption("Revisions smaller than ±0.5% are excluded — late tweaks to settled "
           "years carry inflated z-scores but no tradeable information.")

# ---------------------------------------------------------------- shock gallery
st.divider()
st.subheader("Did the shock stick?")
gallery = top.iloc[::-1].head(6)
sfig = make_subplots(rows=2, cols=3, subplot_titles=[
    f"{r.report_month:%b %Y} {pretty(r.attribute)} {r.marketing_year}"
    for r in gallery.itertuples()
])
full_mom = mom.set_index(["attribute", "marketing_year"]).sort_index()
for i, r in enumerate(gallery.itertuples()):
    row, col = i // 3 + 1, i % 3 + 1
    try:
        v = full_mom.loc[(r.attribute, r.marketing_year)].sort_values("report_month")
    except KeyError:
        continue
    sfig.add_trace(
        go.Scatter(x=v["report_month"], y=v["value"], mode="lines+markers",
                   marker=dict(size=4), line=dict(color="#7f8896", width=1.3),
                   showlegend=False),
        row=row, col=col,
    )
    sfig.add_trace(
        go.Scatter(x=[r.report_month], y=[r.value], mode="markers",
                   marker=dict(size=10, color=PALETTE["grain"], symbol="star"),
                   showlegend=False),
        row=row, col=col,
    )
apply_layout(sfig, height=520)
sfig.update_annotations(font_size=11)
st.plotly_chart(sfig, width="stretch")
st.caption("Each panel is the full vintage path of the shocked cell; the star marks "
           "the shock report. A path that keeps going = the shock stuck; a bounce "
           "back = it mean-reverted.")

# ------------------------------------------------- current report percentile strip
st.divider()
st.subheader("How unusual is the latest report?")
latest_month = mom["report_month"].max()
lat = mom[mom["report_month"] == latest_month]
proj = lat[lat["year_status"] == "projection"]
if proj.empty:
    proj = lat
target_my = proj["marketing_year"].max()
lat = lat[lat["marketing_year"] == target_my]
hist = mom[(mom["cal_month"] == latest_month.month)
           & (mom["report_month"] != latest_month)]

pfig = go.Figure()
shown = [a for a in sel_attrs if a in set(lat["attribute"])]
for a in shown:
    h = hist[hist["attribute"] == a]["pct"]
    pfig.add_trace(go.Box(
        y=h, name=pretty(a), boxpoints="all", jitter=0.5, pointpos=0,
        marker=dict(size=3, color=PALETTE["muted"]), line=dict(color="#444"),
        fillcolor="rgba(0,0,0,0)", showlegend=False,
    ))
cur_rows = lat.set_index("attribute")["pct"]
pcts = []
for a in shown:
    h = hist[hist["attribute"] == a]["pct"]
    v = cur_rows.get(a, np.nan)
    pcts.append((h < v).mean() * 100 if len(h) and not np.isnan(v) else np.nan)
pfig.add_trace(go.Scatter(
    x=[pretty(a) for a in shown], y=[cur_rows.get(a, np.nan) for a in shown],
    mode="markers", marker=dict(size=13, color=PALETTE["grain"], symbol="diamond"),
    name=f"{latest_month:%b %Y}",
    text=[f"{p:.0f}th pct" if not np.isnan(p) else "" for p in pcts],
    hovertemplate="%{x}: %{y:+.2f}% — %{text}<extra></extra>",
))
apply_layout(pfig, height=420,
             yaxis_title=f"% revision in {latest_month:%B} reports, {target_my[:7]}-style MY")
st.plotly_chart(pfig, width="stretch")
st.caption(
    f"Grey: every historical {latest_month:%B}-report revision of the new-crop year. "
    f"Amber diamond: the {latest_month:%b %Y} print, hover for its percentile."
)
