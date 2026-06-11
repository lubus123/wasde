"""Revision momentum — does this month's revision predict the next one?"""

import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st
from lib import (
    MONTH_CYCLE,
    PALETTE,
    analytics,
    apply_layout,
    attributes,
    diverging,
    fetch_series,
    page_header,
    pretty,
    sidebar_dataset_picker,
)

st.set_page_config(page_title="Revision momentum", page_icon="🔁", layout="wide")
page_header(
    "Revision momentum",
    "If USDA cuts in September, does October cut again? Forecast-anchoring shows up "
    "as positively autocorrelated revisions — the difference between fading a print "
    "and following it.",
)

slug, commodity, region = sidebar_dataset_picker()
attrs_df = attributes(slug, commodity, region)
attr_list = attrs_df["attribute"].tolist()

c1, c2 = st.columns([1.2, 2])
attr = c1.selectbox("Attribute", attr_list, format_func=pretty,
                    index=attr_list.index("ending_stocks")
                    if "ending_stocks" in attr_list else 0)
eps = c2.slider("Ignore revisions smaller than (%)", 0.0, 2.0, 0.1, 0.05,
                help="Reprints with no real change pollute the sign statistics.")

obs = fetch_series(slug, commodity, region, attr_list)
mom = analytics.mom_changes(obs)
mom = mom[~mom["is_first_print"]].copy()
mom["pct"] = mom["pct_change"] * 100
mom["month_name"] = mom["report_month"].dt.strftime("%b")

# Consecutive same-marketing-year pairs: revision at t vs the next available report.
mom = mom.sort_values(["attribute", "marketing_year", "report_month"])
grp = mom.groupby(["attribute", "marketing_year"])
mom["pct_next"] = grp["pct"].shift(-1)
mom["month_next"] = grp["month_name"].shift(-1)
pairs = mom.dropna(subset=["pct", "pct_next"])

# ------------------------------------------------------------------ lag scatter
sel = pairs[(pairs["attribute"] == attr) & (pairs["pct"].abs() >= eps)].copy()
st.subheader(f"{pretty(attr)}: revision(t) vs revision(t+1)")
if len(sel) < 10:
    st.info("Not enough consecutive revision pairs for this selection.")
else:
    sel["decade"] = (sel["report_month"].dt.year // 10 * 10).astype(str) + "s"
    sfig = px.scatter(
        sel, x="pct", y="pct_next", color="decade",
        hover_data=["marketing_year", "report_month"],
        color_discrete_sequence=["#7f8896", "#5B9BD5", "#8B6F2E", "#D4A93D"],
    )
    from scipy import stats

    reg = stats.linregress(sel["pct"], sel["pct_next"])
    xs = np.linspace(sel["pct"].min(), sel["pct"].max(), 20)
    sfig.add_trace(go.Scatter(x=xs, y=reg.intercept + reg.slope * xs, mode="lines",
                              line=dict(color=PALETTE["grain"], dash="dash"),
                              name="OLS"))
    sfig.add_hline(y=0, line_color="#444")
    sfig.add_vline(x=0, line_color="#444")
    apply_layout(sfig, height=440, xaxis_title="revision at t (%)",
                 yaxis_title="revision at t+1 (%)")
    st.plotly_chart(sfig, width="stretch", key="momentum_scatter")
    rho = sel[["pct", "pct_next"]].corr(method="spearman").iloc[0, 1]
    agree = (np.sign(sel["pct"]) == np.sign(sel["pct_next"])).mean()
    st.caption(
        f"β = **{reg.slope:+.2f}** (p = {reg.pvalue:.3f}) · Spearman ρ = **{rho:+.2f}** "
        f"· same-sign rate = **{agree:.0%}** · n = {len(sel)} pairs. "
        "β > 0 and same-sign > 50% ⇒ revisions trend (follow the print); "
        "β < 0 ⇒ they mean-revert (fade it)."
    )

# ----------------------------------------------------- where in the cycle it trends
st.divider()
st.subheader("Sign agreement by month pair, all attributes")
pp = pairs[pairs["pct"].abs() >= eps].copy()
pp = pp[np.sign(pp["pct"]) != 0]
pp["agree"] = np.sign(pp["pct"]) == np.sign(pp["pct_next"])
pp["pair"] = pp["month_name"] + "→" + pp["month_next"]
pair_order = [f"{a}→{b}"
              for a, b in zip(MONTH_CYCLE, MONTH_CYCLE[1:] + MONTH_CYCLE[:1], strict=True)]
g = pp.groupby(["pair", "attribute"]).agg(agree=("agree", "mean"),
                                          n=("agree", "size")).reset_index()
g = g[g["n"] >= 8]
z = (g.pivot(index="pair", columns="attribute", values="agree") - 0.5) * 100
z = z.reindex(index=[p for p in pair_order if p in z.index],
              columns=[a for a in attrs_df["attribute"] if a in z.columns])
z = z.dropna(axis=1, how="all")  # attributes with no bucket ≥ 8 pairs
if z.empty:
    st.info("Not enough pairs per bucket — lower the revision filter.")
else:
    lim = max(abs(z.fillna(0).values).max(), 5)
    hfig = go.Figure(go.Heatmap(
        z=z.values, x=[pretty(a) for a in z.columns], y=z.index.tolist(),
        colorscale=diverging(), zmin=-lim, zmax=lim,
        text=[["" if pd.isna(v) else f"{v + 50:.0f}%" for v in row] for row in z.values],
        texttemplate="%{text}", textfont=dict(size=9), hoverongaps=False,
        colorbar=dict(title="vs 50%"),
    ))
    apply_layout(hfig, height=80 + 26 * len(z))
    st.plotly_chart(hfig, width="stretch", key="momentum_heatmap")
    st.caption(
        "Cell = how often the next report revised in the same direction (green/blue "
        "above coin-flip ⇒ momentum; red below ⇒ mean reversion). Buckets with fewer "
        "than 8 pairs hidden."
    )

# ------------------------------------------------------------- conditional bars
st.divider()
st.subheader(f"{pretty(attr)}: follow or fade?")
sa = pairs[(pairs["attribute"] == attr) & (pairs["pct"].abs() >= eps)]
rows = []
for label, cond_sign in [("after a cut", -1), ("after a raise", 1)]:
    base = sa[np.sign(sa["pct"]) == cond_sign]
    if len(base) < 5:
        continue
    k = (np.sign(base["pct_next"]) == cond_sign).sum()
    n = len(base)
    p = k / n
    se = 1.96 * np.sqrt(p * (1 - p) / n)
    uncond = (np.sign(sa["pct_next"]) == cond_sign).mean()
    rows.append({"case": f"same direction {label}", "p": p * 100, "err": se * 100,
                 "uncond": uncond * 100, "n": n})
if rows:
    bdf = pd.DataFrame(rows)
    bfig = go.Figure()
    bfig.add_trace(go.Bar(x=bdf["case"], y=bdf["p"], error_y=dict(array=bdf["err"]),
                          marker_color=PALETTE["grain"], name="conditional"))
    bfig.add_trace(go.Bar(x=bdf["case"], y=bdf["uncond"],
                          marker_color=PALETTE["muted"], name="unconditional"))
    apply_layout(bfig, height=360, yaxis_title="probability (%)", barmode="group")
    st.plotly_chart(bfig, width="stretch", key="momentum_bars")
    st.caption("Amber above grey ⇒ the print direction carries information about the "
               "next one. Error bars: 95% normal-approx CI.")
else:
    st.info("Not enough conditional pairs at this filter level.")
