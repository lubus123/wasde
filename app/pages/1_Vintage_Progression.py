"""Vintage evolution — how belief about a crop year formed, report by report."""

import plotly.graph_objects as go
import streamlit as st
from lib import (
    GREYS,
    PALETTE,
    STATUS_MARKERS,
    analytics,
    apply_layout,
    attributes,
    fetch_series,
    marketing_years,
    page_header,
    pretty,
    sidebar_dataset_picker,
    unit_label,
)
from plotly.subplots import make_subplots

st.set_page_config(page_title="Vintage progression", page_icon="📈", layout="wide")
page_header(
    "Vintage progression",
    "Every number USDA ever printed for one crop year, first projection to final "
    "actual — then all years overlaid on a common clock to see how belief converges.",
)

slug, commodity, region = sidebar_dataset_picker()
attrs_df = attributes(slug, commodity, region)
mys = marketing_years(slug, commodity, region)
if not mys:
    st.info("No data for this dataset.")
    st.stop()

c1, c2, c3, c4 = st.columns([1.3, 1, 1.6, 1.2])
attr = c1.selectbox("Attribute", attrs_df["attribute"], format_func=pretty,
                    index=int((attrs_df["attribute"] == "ending_stocks").idxmax()))
focus_my = c2.selectbox("Focus marketing year", mys, index=min(1, len(mys) - 1))
overlay_mys = c3.multiselect(
    "Overlay years", mys, default=[m for m in mys[: len(mys[:12])] if m != focus_my][:9]
)
norm = c4.radio("Overlay scale", ["% of final", "% of first print", "Raw"],
                help="Final = the last value WASDE ever printed for that year.")

unit = attrs_df.set_index("attribute")["unit"].get(attr)
obs = fetch_series(slug, commodity, region, [attr])
mom = analytics.mom_changes(obs)
finals = analytics.final_values(obs).set_index("marketing_year")

# ----------------------------------------------------------- single-MY deep dive
fmy = mom[mom["marketing_year"] == focus_my].sort_values("report_month")
if fmy.empty:
    st.info(f"{pretty(attr)} was not published for {focus_my}.")
    st.stop()

fig = make_subplots(rows=2, cols=1, shared_xaxes=True, row_heights=[0.7, 0.3],
                    vertical_spacing=0.06)
for status, grp in fmy.groupby("year_status", sort=False):
    fig.add_trace(
        go.Scatter(
            x=grp["report_month"], y=grp["value"], name=status,
            mode="markers", marker=dict(symbol=STATUS_MARKERS.get(status, "circle"),
                                        size=9, color=PALETTE["grain"]),
        ),
        row=1, col=1,
    )
fig.add_trace(
    go.Scatter(x=fmy["report_month"], y=fmy["value"], mode="lines",
               line=dict(color=PALETTE["grain"], width=1.4), showlegend=False),
    row=1, col=1,
)
if focus_my in finals.index:
    f = finals.loc[focus_my]
    fig.add_hline(y=f["final_value"], line_dash="dot", line_color=PALETTE["muted"],
                  annotation_text=f"final {f['final_value']:,.2f} "
                                  f"({f['final_year_status']})", row=1, col=1)
fig.add_trace(
    go.Bar(
        x=fmy["report_month"], y=fmy["delta"],
        marker_color=[PALETTE["raise"] if d > 0 else PALETTE["cut"] if d < 0
                      else PALETTE["muted"] for d in fmy["delta"].fillna(0)],
        name="MoM revision",
    ),
    row=2, col=1,
)
apply_layout(fig, height=560)
fig.update_yaxes(title_text=unit_label(unit), row=1, col=1)
fig.update_yaxes(title_text="Δ", row=2, col=1)
st.plotly_chart(fig, width="stretch")
st.caption(
    f"**{pretty(attr)} — {focus_my}**: open circles = projection, filled = estimate, "
    "diamonds = actual. Bars are the report-by-report revision tape "
    "(green = raised, red = cut)."
)

# ------------------------------------------------------- multi-year overlay + cone
st.divider()
st.subheader("All years on a common clock")

show_mys = [m for m in dict.fromkeys([*overlay_mys, focus_my]) if m in set(mom["marketing_year"])]
ov = mom[mom["marketing_year"].isin(show_mys)].copy()
first = ov.groupby("marketing_year")["report_month"].transform("min")
ov["months_in"] = ((ov["report_month"].dt.year - first.dt.year) * 12
                   + (ov["report_month"].dt.month - first.dt.month))
ov = ov.join(finals["final_value"], on="marketing_year")

if norm == "% of final":
    ov["y"] = ov["value"] / ov["final_value"].where(ov["final_value"] != 0) * 100
    ytitle = "% of final print"
elif norm == "% of first print":
    first_val = ov.sort_values("months_in").groupby("marketing_year")["value"].transform("first")
    ov["y"] = ov["value"] / first_val.where(first_val != 0) * 100
    ytitle = "% of first print"
else:
    ov["y"] = ov["value"]
    ytitle = unit_label(unit)

ofig = go.Figure()
if norm == "% of final":
    # Convergence cone from ALL finalized years, not just the displayed ones.
    allf = mom.join(finals[["final_value", "final_year_status"]], on="marketing_year")
    allf = allf[(allf["final_year_status"] == "actual") & (allf["final_value"] != 0)]
    af_first = allf.groupby("marketing_year")["report_month"].transform("min")
    allf["months_in"] = ((allf["report_month"].dt.year - af_first.dt.year) * 12
                         + (allf["report_month"].dt.month - af_first.dt.month))
    allf["ratio"] = allf["value"] / allf["final_value"] * 100
    cone = allf.groupby("months_in")["ratio"].quantile([0.25, 0.75]).unstack()
    n_cone = allf["marketing_year"].nunique()
    ofig.add_trace(go.Scatter(x=cone.index, y=cone[0.25], mode="lines",
                              line=dict(width=0), showlegend=False, hoverinfo="skip"))
    ofig.add_trace(go.Scatter(x=cone.index, y=cone[0.75], mode="lines",
                              line=dict(width=0), fill="tonexty",
                              fillcolor="rgba(212,169,61,0.12)",
                              name=f"IQR cone ({n_cone} finalized yrs)"))

for i, my in enumerate(sorted(show_mys)):
    d = ov[ov["marketing_year"] == my].sort_values("months_in")
    last = my == focus_my
    color = PALETTE["grain"] if last else GREYS[i % len(GREYS)]
    ofig.add_trace(go.Scatter(
        x=d["months_in"], y=d["y"], name=my,
        line=dict(color=color, width=3.5 if last else 1.3),
        opacity=1.0 if last else 0.6,
        mode="lines",
    ))
if norm != "Raw":
    ofig.add_hline(y=100, line_dash="dot", line_color=PALETTE["muted"])
apply_layout(ofig, height=460, yaxis_title=ytitle,
             xaxis_title="months since first print (0 = May before harvest)")
st.plotly_chart(ofig, width="stretch")
st.caption(
    "Bold amber = focus year. On the *% of final* scale the dotted 100 line is where "
    "every year must end; the shaded band is the interquartile path of all finalized "
    "years — a year outside the cone is resolving unusually."
)
unfinal = [m for m in show_mys
           if m in finals.index and finals.loc[m, "final_year_status"] != "actual"]
if norm == "% of final" and unfinal:
    st.caption(
        f"⚠️ {', '.join(unfinal)} have not reached a final *actual* print — their "
        "'final' is just the latest value, so they end at 100 by construction."
    )
