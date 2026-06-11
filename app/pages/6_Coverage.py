"""Coverage & QA — what the archive actually holds, era by era, nothing hidden."""

import plotly.graph_objects as go
import streamlit as st
from lib import (
    OBS,
    PALETTE,
    apply_layout,
    attributes,
    fetch_series,
    page_header,
    pretty,
    query,
    release_calendar,
    sidebar_dataset_picker,
)

st.set_page_config(page_title="Coverage & QA", page_icon="🗄️", layout="wide")
page_header(
    "Coverage & QA",
    "Which attributes exist in which era, where the format boundaries are, and what "
    "QA flagged — gaps shown, never papered over.",
)

slug, commodity, region = sidebar_dataset_picker()

# ----------------------------------------------------------------- era timeline
cal = release_calendar()
era_colors = {"xml": PALETTE["raise"], "txt": PALETTE["prev"],
              "pdf_scan": PALETTE["cut"]}
cal["year"] = cal["report_month"].dt.year
era = cal.groupby(["year", "format_era"]).size().rename("n").reset_index()
efig = go.Figure()
for fmt, d in era.groupby("format_era"):
    efig.add_trace(go.Bar(x=d["year"], y=d["n"], name=fmt,
                          marker_color=era_colors.get(fmt, PALETTE["muted"])))
apply_layout(efig, height=240, barmode="stack", yaxis_title="reports loaded")
st.plotly_chart(efig, width="stretch")
st.caption(
    "Reports parsed into observations, by source era. The 1973–94 scan era loads "
    "via the OCR pipeline and will appear here as the exports regenerate."
)

# ------------------------------------------------------- attribute availability
st.divider()
st.subheader(f"Attribute availability — {pretty(commodity)} ({pretty(region)})")
obs = fetch_series(slug, commodity, region, include_quarantined=True)
if obs.empty:
    st.info("No observations for this dataset.")
    st.stop()
obs["year"] = obs["report_month"].dt.year
avail = (obs.groupby(["attribute", "year"])["report_month"].nunique()
         .rename("months").reset_index())
attrs_df = attributes(slug, commodity, region)
order = [a for a in attrs_df["attribute"] if a in set(avail["attribute"])]
z = avail.pivot(index="attribute", columns="year", values="months").reindex(order)
afig = go.Figure(go.Heatmap(
    z=z.values, x=z.columns.tolist(), y=[pretty(a) for a in z.index],
    colorscale=[[0, PALETTE["bg2"]], [1, PALETTE["grain"]]], zmin=0, zmax=12,
    hovertemplate="%{y} · %{x}: %{z} report months<extra></extra>",
    colorbar=dict(title="months/yr"),
))
apply_layout(afig, height=100 + 26 * len(z))
st.plotly_chart(afig, width="stretch")
st.caption("Dark cells = attribute not published that year (typical for TXT-era "
           "sub-lines like ethanol_for_fuel, published 2004→).")

# -------------------------------------------------------------------- QA status
st.divider()
st.subheader("QA status")
qa_all = query(f"SELECT qa_status, count(*) AS n FROM read_parquet('{OBS}') GROUP BY 1")
c1, c2 = st.columns([1, 2])
with c1:
    st.dataframe(qa_all.rename(columns={"qa_status": "status"}),
                 hide_index=True, width="stretch")
    quarantined = int(qa_all.loc[qa_all["qa_status"] == "quarantined", "n"].sum())
    st.caption(
        f"{quarantined} quarantined cells dataset-wide. Quarantined rows are excluded "
        "from charts by default but never deleted — they carry the OCR era's "
        "unresolved conflicts once it lands."
    )
with c2:
    exc = query(
        "SELECT check_name, severity, count(*) AS n "
        f"FROM read_parquet('{OBS.parent / 'qa_exceptions.parquet'}') "
        "GROUP BY 1, 2 ORDER BY n DESC"
    )
    st.dataframe(exc, hide_index=True, width="stretch")
    st.caption("QA exceptions across the dataset (whitelisted = known USDA "
               "rebenchmarks verified by hand; see data/manual/).")

# ------------------------------------------------- reports without observations
missing = cal[~cal["release_id"].isin(set(obs["release_id"]))]
if not missing.empty:
    with st.expander(f"{len(missing)} loaded reports with no rows for this dataset"):
        st.dataframe(
            missing[["release_id", "report_month", "format_era"]],
            hide_index=True, width="stretch",
        )
