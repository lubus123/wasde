# wasde-data

Report-vintage archive of **every USDA WASDE report (September 1973 → present)**, all
commodities, with US corn/soybean balance sheets held to a zero-error accuracy bar, plus a
monthly scraper that ingests each new report.

"Report-vintage" means each observation records what the report said **at publication time**
— the dataset answers "what did USDA believe about 2008/09 corn ending stocks in each month
of 2008?" as well as "what changed between May and June 2026?".

## Quickstart

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"          # core + test tooling
.venv/bin/pip install -e ".[ocr]"          # only for the 1973-94 scan era
.venv/bin/pip install -e ".[app]"          # Vintage Explorer dashboard
sudo apt install tesseract-ocr             # only for the 1973-94 scan era

.venv/bin/python -m pytest                 # all tests (no network)
.venv/bin/python -m pytest -m integration  # live ESMIS smoke tests
.venv/bin/streamlit run app/Home.py        # Vintage Explorer (reads data/exports/)
```

## Pipeline

| Script | Purpose |
|---|---|
| `scripts/01_backfill_catalog.py` | ESMIS catalog → `releases` + download every raw file (resumable) |
| `scripts/02_parse_xml_era.py` | Parse 2010-07→present XML releases into `observations` |
| `scripts/03_parse_txt_era.py` | Parse 1995-01→2010-06 fixed-width TXT releases |
| `scripts/04_load_agmanager.py` | Load AgManager (K-State) corn/soy sheets for cross-validation |
| `scripts/05_ocr_corn_soy.py` | Reader #1: tesseract over 1980-94 scans + identity repair |
| `scripts/06_reconcile.py` | Reader #2: PaddleOCR + per-cell identity arbitration |
| `scripts/07_relocate_scans.py` | Paddle-led page relocation for header-unreadable scans |
| `scripts/10_update.py` | **Cron entrypoint**: poll ESMIS, ingest new release end-to-end |
| `scripts/12_export.py` | Parquet exports to `data/exports/` |
| `scripts/99_qa_report.py` | Full QA sweep; nonzero exit on failures |

## Data sources

- **ESMIS** (`esmis.nal.usda.gov`) — canonical source, free JSON API, all 699+ releases.
  Format eras: XML (2010-07→), fixed-width TXT (1995-01→2010-06), scanned PDF (1973→1994).
- **AgManager.info** (Kansas State) — independently curated corn/soy monthly balance
  sheets back to 1973, used only for cross-validation (`agmanager_obs` table).
- `www.usda.gov` (OCE consolidated CSVs) is **not** used: unreachable from this
  environment and redundant with the ESMIS XML.

## Database

DuckDB at `data/wasde.duckdb`. One long fact table `observations` keyed on
`(release_id, table_slug, region, commodity, attribute, marketing_year, forecast_month)`;
values stored **as published** (no unit conversion), with `raw_attribute`/`raw_commodity`
keeping the exact printed labels. Views: `observations_latest` (vN revisions resolved),
`vintage_current` (the headline number per report), `us_corn_balance`, `us_soybeans_balance`.

Typical queries:

```sql
-- Full vintage history of US corn ending stocks
SELECT report_month, marketing_year, value FROM vintage_current
WHERE table_slug='us_corn' AND attribute='ending_stocks' ORDER BY report_month;

-- What changed May → June 2026 (both columns printed in the June report)
SELECT attribute, forecast_month, value FROM observations
WHERE release_id='wasde-2026-06-11' AND table_slug='us_corn'
  AND marketing_year='2026/27';
```

## Vintage Explorer (app/)

Streamlit + Plotly dashboard over the parquet exports (`data/exports/`, refreshed by
`scripts/12_export.py` — the app never opens the live DuckDB, so it stays lock-free
while the OCR backfill runs). Launch: `.venv/bin/streamlit run app/Home.py`.

| Page | Question it answers |
|---|---|
| Home | What did the latest report change, across every US table? |
| Vintage Progression | How did belief about one crop year form, and is this year converging normally (IQR cone)? |
| Report Month Matrix | **Every year's e.g. September report at once** — % MoM revision per attribute, colour-coded, plus bias of that month's print vs the final number |
| Bias Explorer | Where is USDA systematically wrong, by calendar month and by months-to-resolution? |
| Revision Momentum | Does a cut predict another cut? (lag-1 autocorrelation, follow-vs-fade) |
| Surprise Leaderboard | Biggest prints in 30 years (z-scored within calendar month); how unusual is today? |
| Coverage & QA | Which attributes exist in which era; QA status, nothing hidden |

Pure analytics live in `src/wasde_data/analytics.py` (100% covered, golden-tested
against the hand-verified corn 2012/13 drought-year chain); pages are thin Plotly
glue. Smoke tests: `tests/test_app_smoke.py` renders every page headlessly.

## Accuracy contract

Every corn/soy number passes balance-sheet identity checks, month-over-month continuity
(each projection is printed in two consecutive reports), and — for the OCR era —
cross-validation against AgManager. Cells failing checks are quarantined
(`qa_status='quarantined'`), surfaced by `99_qa_report.py`, and only cleared by a
human-verified entry in `data/manual/ocr_overrides.csv`. Unmapped labels in priority
tables are hard parse failures, never silently skipped.

See `PROJECT_OBJECTIVE.md` (what "done" means), `CORE_PRINCIPLES.md` (engineering rules),
`docs/DECISIONS.md` (why things are the way they are).
