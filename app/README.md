# WASDE Vintage Explorer

Streamlit + Plotly dashboard over the wasde-data parquet exports. Built for one
question asked many ways: **how do WASDE beliefs form, revise, and resolve?**

```bash
.venv/bin/pip install -e ".[app]"
.venv/bin/streamlit run app/Home.py
```

## Architecture

```
app/
  Home.py                      # latest report: metric cards, balance-sheet waterfall,
                               # all-US-tables pulse heatmap
  lib.py                       # DuckDB-over-parquet query layer (mtime cache-bust),
                               # dimension helpers, palette, sidebar selectors
  pages/
    1_Vintage_Progression.py   # single-MY deep dive + multi-year overlay + IQR cone
    2_Report_Month_Matrix.py   # years × attributes %-revision heatmap + bias panel
    3_Bias_Explorer.py         # mean error by calendar month / by horizon bucket
    4_Revision_Momentum.py     # revision(t) vs revision(t+1); follow-or-fade
    5_Surprise_Leaderboard.py  # z-scored biggest prints + current-report percentile
    6_Coverage.py              # era timeline, attribute availability, QA status
src/wasde_data/analytics.py    # ALL number-crunching: pure df-in/df-out, 100% covered
```

**Data path:** `data/exports/*.parquet` only — never `data/wasde.duckdb` (the OCR
backfill writes it; parquet is lock-free). Refresh with `scripts/12_export.py`;
the app's `@st.cache_data` keys on export mtime and picks changes up automatically.

**Series key:** `(table_slug, commodity, region, attribute)`. Never key on
`(table_slug, attribute)` alone — slugs carry multiple commodities. Unit is display
metadata (latest-era via `arg_max`), not part of the key; TXT-era unit labels on
price/area attributes are mislabelled upstream.

## Semantics (full rationale in docs/DECISIONS.md, app entry)

- **Headline value** of a report = row with `forecast_month=''` or `== report month`;
  labelled duplicates (2010-H2 artifact) lose to the unlabelled row.
- **MoM revision**: within-report column diff for projections (prev column taken from
  the data, never month arithmetic — shutdown gaps), cross-release diff for
  estimate/actual and single-column sub-tables. First prints excluded from stats.
- **Final** = last value WASDE ever printed for the cell; bias stats use only
  marketing years whose final is a true `actual`.
- **Colour convention**: diverging scale anchored at zero; red = number cut,
  green (blue in colour-blind mode) = number raised. Direction of revision, NOT
  price impact — the matrix page has an explicit price-impact toggle that flips
  supply-side attributes.

## Testing

- `tests/test_analytics.py` — golden corn 2012/13 drought chain (hand-verified
  against the printed reports) + synthetic edge cases (shutdown gap, NaN first-print
  placeholder, labelled-actual dedupe, single-column fallback, zero/negative guards).
- `tests/test_app_smoke.py` — `streamlit.testing.v1.AppTest` renders every page
  against the real exports; skipped when streamlit isn't installed.
- Visual QA: every page screenshot-inspected (Playwright, dark theme, 1700px) before
  ship — keep doing this for any layout change.

## Extending

Add analytics as pure functions in `src/wasde_data/analytics.py` (tests first,
fixture slices under `tests/fixtures/app/`), then bind them in a page. Keep pages
free of number-crunching. New datasets/eras need zero app changes — dimensions are
discovered from the exports at runtime.
