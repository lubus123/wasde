"""A calendar month through history — every year's e.g. September report at once."""

import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st
from lib import (
    MONTH_CYCLE,
    MONTH_NUM,
    SUPPLY_SIDE,
    analytics,
    apply_layout,
    attributes,
    diverging,
    fetch_series,
    is_price,
    page_header,
    pretty,
    sidebar_dataset_picker,
    unit_label,
)

st.set_page_config(page_title="Report month matrix", page_icon="🗓️", layout="wide")
page_header(
    "A calendar month through history",
    "Pick a report month and see every year's print side by side: how that month's "
    "report moved each balance-sheet line, colour-coded by direction of revision.",
)

slug, commodity, region = sidebar_dataset_picker()
attrs_df = attributes(slug, commodity, region)
attr_units = dict(zip(attrs_df["attribute"], attrs_df["unit"], strict=True))

c1, c2, c3, c4 = st.columns([1, 1.4, 1.2, 1.2])
month = c1.selectbox("Report month", MONTH_CYCLE, index=MONTH_CYCLE.index("Sep"))
sel_attrs = c2.multiselect(
    "Attributes",
    attrs_df["attribute"].tolist(),
    default=attrs_df["attribute"].tolist(),
    format_func=pretty,
)
crop = c3.radio(
    "Target crop year",
    ["New crop (projection)", "Old crop (estimate)"],
    horizontal=True,
    help="A September report revises both the new-crop projection and the old-crop "
    "estimate; pick which marketing year the matrix tracks.",
)
mode = c4.radio(
    "Cell value",
    ["% MoM change", "Absolute change", "% dev from final"],
    horizontal=True,
)
price_frame = st.toggle(
    "Colour by price impact",
    help="Flips the colour of supply-side attributes (area, yield, production, "
    "stocks-in, imports) so green = bullish-for-price. Cell text stays the raw "
    "revision either way.",
)

if not sel_attrs:
    st.info("Select at least one attribute.")
    st.stop()

obs = fetch_series(slug, commodity, region, sel_attrs)
if obs.empty:
    st.info("No observations for this dataset.")
    st.stop()

status = "projection" if crop.startswith("New") else "estimate"
month_num = MONTH_NUM[month]

mom = analytics.mom_changes(obs)
cur = analytics.current_values(obs)
finals = analytics.final_values(obs)
errors = analytics.attach_errors(cur, finals)

mom_m = mom[(mom["report_month"].dt.month == month_num) & (mom["year_status"] == status)]
if mom_m.empty:
    st.info(f"No {status} rows in {month} reports for this dataset.")
    st.stop()

# One marketing year per report: the newest MY holding the chosen status.
tgt = mom_m.groupby("report_month")["marketing_year"].max().rename("_tgt")
mom_t = mom_m.merge(tgt, on="report_month")
mom_t = mom_t[mom_t["marketing_year"] == mom_t["_tgt"]].drop(columns="_tgt")
mom_t = mom_t.merge(
    errors[["attribute", "marketing_year", "report_month", "pct_error"]],
    on=["attribute", "marketing_year", "report_month"],
    how="left",
)

if mode == "% MoM change":
    mom_t["_z"] = mom_t["pct_change"] * 100
    fmt, suffix = "{:+.1f}", "%"
elif mode == "Absolute change":
    mom_t["_z"] = mom_t["delta"]
    fmt, suffix = "{:+,.1f}", ""
else:
    mom_t["_z"] = mom_t["pct_error"] * 100
    fmt, suffix = "{:+.1f}", "%"

cols = [a for a in attrs_df["attribute"] if a in sel_attrs]
z = mom_t.pivot(index="marketing_year", columns="attribute", values="_z")
z = z.reindex(columns=[c for c in cols if c in z.columns]).sort_index(ascending=False)


def _col_label(attr: str) -> str:
    base = pretty(attr)
    return f"$ {base}" if is_price(attr, attr_units.get(attr)) else base


text = z.map(lambda v: "" if pd.isna(v) else fmt.format(v) + suffix)
z_color = z.copy()
if price_frame:
    for a in z_color.columns:
        if a in SUPPLY_SIDE:
            z_color[a] = -z_color[a]

hover = mom_t.pivot(index="marketing_year", columns="attribute", values="value")
hover = hover.reindex(index=z.index, columns=z.columns)
hover_prev = mom_t.pivot(index="marketing_year", columns="attribute", values="value_prev")
hover_prev = hover_prev.reindex(index=z.index, columns=z.columns)

zmax = np.nanmax(np.abs(z_color.values)) if np.isfinite(z_color.values).any() else 1.0
fig = go.Figure(
    go.Heatmap(
        z=z_color.values,
        x=[_col_label(a) for a in z.columns],
        y=z.index.tolist(),
        zmin=-zmax,
        zmax=zmax,
        colorscale=diverging(),
        text=text.values,
        texttemplate="%{text}",
        textfont=dict(size=10),
        customdata=np.dstack([hover.values, hover_prev.values]),
        hovertemplate=(
            "%{y} · %{x}<br>now %{customdata[0]:,.2f} · prev %{customdata[1]:,.2f}"
            "<br>cell: %{text}<extra></extra>"
        ),
        hoverongaps=False,
        colorbar=dict(title=suffix or "Δ"),
    )
)
apply_layout(fig, height=max(420, 24 * len(z) + 120))
fig.update_layout(xaxis=dict(side="top"), yaxis=dict(autorange="reversed", dtick=1))
fig.update_yaxes(autorange=True)
event = st.plotly_chart(fig, width="stretch", on_select="rerun")
st.caption(
    f"Each row is one year's **{month} report**, tracking the {crop.lower()} marketing "
    "year. Blank cells: not published. First prints (new marketing year) carry no "
    "change and show blank."
)

# ------------------------------------------------------------------- drill-down
points = (event or {}).get("selection", {}).get("points", [])
if points:
    p = points[0]
    label_to_attr = {_col_label(a): a for a in z.columns}
    d_attr, d_my = label_to_attr.get(p["x"], p["x"]), p["y"]
    drill = mom[(mom["attribute"] == d_attr) & (mom["marketing_year"] == d_my)]
    clicked = drill[drill["report_month"].dt.month == month_num]
    st.subheader(f"{pretty(d_attr)} — {d_my} vintage")
    dfig = go.Figure(
        go.Scatter(
            x=drill["report_month"],
            y=drill["value"],
            mode="lines+markers",
            line=dict(color="#7f8896", width=1.6),
            marker=dict(size=7),
            name=d_my,
        )
    )
    if not clicked.empty:
        c = clicked.iloc[0]
        dfig.add_trace(
            go.Scatter(
                x=[c["report_month"]],
                y=[c["value"]],
                mode="markers",
                marker=dict(size=14, color="#D4A93D", symbol="star"),
                name=f"{month} report",
            )
        )
        st.caption(
            f"{month} report printed **{c['value']:,.2f}** vs previous "
            f"**{c['value_prev']:,.2f}** ({c['delta']:+,.2f}, method: {c['method']})."
        )
    apply_layout(dfig, height=320, yaxis_title=unit_label(attr_units.get(d_attr)))
    st.plotly_chart(dfig, width="stretch")

# ------------------------------------------------------------------- bias panel
st.divider()
st.subheader(f"Bias of the {month} print vs the final number")
err_m = errors[
    (errors["calendar_month"] == month_num)
    & (errors["year_status"] == status)
    & (errors["final_year_status"] == "actual")
    & (errors["horizon_months"] > 0)
    & errors["attribute"].isin(sel_attrs)
].copy()
err_m = err_m.merge(tgt, on="report_month")
err_m = err_m[err_m["marketing_year"] == err_m["_tgt"]]

if err_m.empty:
    st.info("No finalized marketing years for this selection yet.")
else:
    err_m["pct_err_display"] = err_m["pct_error"] * 100
    left, right = st.columns([1.4, 1])
    bfig = px.box(
        err_m,
        x="attribute",
        y="pct_err_display",
        points="all",
        hover_data=["marketing_year"],
        category_orders={"attribute": [c for c in cols if c in set(err_m["attribute"])]},
        color_discrete_sequence=["#D4A93D"],
    )
    bfig.add_hline(y=0, line_dash="dot", line_color="#888888")
    bfig.update_xaxes(tickvals=list(range(len(cols))), labelalias={a: pretty(a) for a in cols})
    apply_layout(bfig, height=420, yaxis_title="(print − final) / final, %", xaxis_title="")
    left.plotly_chart(bfig, width="stretch")

    summary = analytics.bias_summary(err_m, by="calendar_month").drop(
        columns="calendar_month"
    )
    for col in ("mean_pct_error", "median_pct_error", "mae_pct"):
        summary[col] = summary[col] * 100
    summary["attribute"] = summary["attribute"].map(pretty)
    summary = summary.rename(
        columns={
            "attribute": "Attribute", "n": "n",
            "mean_pct_error": "Mean bias %", "median_pct_error": "Median bias %",
            "mae_pct": "MAE %", "hit_rate_over": "P(print > final)",
            "t_stat": "t", "wilcoxon_p": "Wilcoxon p",
        }
    )
    right.dataframe(
        summary.style.format(
            {
                "Mean bias %": "{:+.2f}", "Median bias %": "{:+.2f}", "MAE %": "{:.2f}",
                "P(print > final)": "{:.0%}", "t": "{:+.2f}", "Wilcoxon p": "{:.3f}",
            },
            na_rep="—",
        ).map(
            lambda v: "font-weight: bold; color: #D4A93D"
            if isinstance(v, float) and v < 0.05
            else "",
            subset=["Wilcoxon p"],
        ),
        width="stretch",
        hide_index=True,
    )
    right.caption(
        "Bias uses only marketing years that reached a final *actual* print. "
        "Wilcoxon signed-rank vs zero median; with ~15 attributes tested, expect "
        "one nominal p<0.05 by chance — treat isolated hits with suspicion."
    )
