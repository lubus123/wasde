"""Bias explorer — the full projection-error surface across months and horizons."""

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st
from lib import (
    MONTH_CYCLE,
    MONTH_NUM,
    analytics,
    apply_layout,
    attributes,
    diverging,
    fetch_series,
    page_header,
    pretty,
    sidebar_dataset_picker,
)

st.set_page_config(page_title="Bias explorer", page_icon="🎯", layout="wide")
page_header(
    "Bias explorer",
    "Where is USDA systematically wrong? Mean error of every print vs the final "
    "number, sliced by calendar month and by months-to-resolution.",
)

slug, commodity, region = sidebar_dataset_picker()
attrs_df = attributes(slug, commodity, region)
sel_attrs = st.multiselect(
    "Attributes", attrs_df["attribute"].tolist(),
    default=attrs_df["attribute"].tolist(), format_func=pretty,
)
if not sel_attrs:
    st.info("Select at least one attribute.")
    st.stop()

obs = fetch_series(slug, commodity, region, sel_attrs)
cur = analytics.current_values(obs)
finals = analytics.final_values(obs)
errors = analytics.attach_errors(cur, finals)
errors = errors[(errors["final_year_status"] == "actual")
                & (errors["horizon_months"] > 0)]
if errors.empty:
    st.info("No finalized marketing years for this selection.")
    st.stop()

n_my = errors["marketing_year"].nunique()
st.caption(f"{n_my} finalized marketing years · error = (print − final) / final.")

attr_order = [a for a in attrs_df["attribute"] if a in set(errors["attribute"])]


def _surface(by: str, x_order: list, x_labels: list[str], title: str) -> go.Figure:
    g = (errors.groupby(["attribute", by])["pct_error"].mean() * 100).reset_index()
    z = g.pivot(index="attribute", columns=by, values="pct_error")
    z = z.reindex(index=attr_order, columns=[c for c in x_order if c in z.columns])
    lim = max(abs(z.fillna(0).values).max(), 0.1)
    fig = go.Figure(go.Heatmap(
        z=z.values, x=[x_labels[x_order.index(c)] for c in z.columns],
        y=[pretty(a) for a in z.index],
        colorscale=diverging(), zmin=-lim, zmax=lim,
        text=[["" if pd.isna(v) else f"{v:+.1f}" for v in row] for row in z.values],
        texttemplate="%{text}", textfont=dict(size=9), hoverongaps=False,
        colorbar=dict(title="mean %"),
    ))
    apply_layout(fig, height=80 + 28 * len(z), title=title)
    return fig


left, right = st.columns(2)
month_order = [MONTH_NUM[m] for m in MONTH_CYCLE]
left.plotly_chart(
    _surface("calendar_month", month_order, MONTH_CYCLE,
             "Mean error by calendar report month"),
    width="stretch",
)
H_LABELS = ["1–3", "4–6", "7–9", "10–12", "13–18", "19–24", "25+"]
errors["horizon_bucket"] = pd.cut(
    errors["horizon_months"], bins=[0, 3, 6, 9, 12, 18, 24, 999], labels=H_LABELS
)
right.plotly_chart(
    _surface("horizon_bucket", H_LABELS, H_LABELS,
             "Mean error by months before final print"),
    width="stretch",
)
st.caption(
    "Left: seasonal bias (e.g. early-summer optimism). Right: the same errors on the "
    "resolution clock — long horizons should be noisy, short ones near zero. "
    "Positive = print above final."
)

st.divider()
d_attr = st.selectbox("Drill into one attribute", attr_order, format_func=pretty)
d = errors[errors["attribute"] == d_attr].copy()
d["pct_err_display"] = d["pct_error"] * 100
d["decade"] = (d["report_month"].dt.year // 10 * 10).astype(str) + "s"
sfig = px.scatter(
    d, x="horizon_months", y="pct_err_display", color="decade",
    hover_data=["marketing_year", "report_month"],
    color_discrete_sequence=["#7f8896", "#5B9BD5", "#8B6F2E", "#D4A93D"],
)
sfig.add_hline(y=0, line_dash="dot", line_color="#888888")
apply_layout(sfig, height=420, xaxis_title="months before final print",
             yaxis_title="(print − final) / final, %")
st.plotly_chart(sfig, width="stretch")
