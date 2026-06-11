# Project Objective

Build the most accurate report-vintage WASDE dataset available anywhere, and keep it
current automatically.

## Vision

Lubo (trading desk, analytics) needs WASDE history as *vintages* — what USDA said each
month, not just the final revised numbers — because trading models are trained on what
the market knew at the time. Vendors repackage this data expensively; the underlying
reports are public. This project is also a proving ground for the data-vendor-undercut
business case (see /workspace/research/data-vendor-undercut/).

## Objectives, in priority order

1. **US corn and soybean-complex balance sheets, 1973 → present, zero errors.**
   Every cell either passes three independent checks (balance identities,
   consecutive-report continuity, AgManager cross-source) or carries a human-verified
   manual override. The acceptable number of wrong numbers is zero.
2. **Every WASDE release archived raw** (all formats), immutably, with checksums —
   the dataset can always be rebuilt from disk without re-downloading.
3. **All other commodities/tables parsed best-effort** with honest, machine-readable
   coverage reporting — gaps are visible, never silent.
4. **Hands-off updates**: a daily cron poll ingests each new report (≈12th of the month)
   end-to-end — download, parse, QA, upsert, export — with no human involvement unless
   QA raises an alarm.

## Done means

- `releases` covers every ESMIS release (699 as of Jun 2026) with raw files on disk.
- Gapless monthly corn/soy ending-stocks vintage series 1973-09 → present.
- `99_qa_report.py` exits 0 on the corn/soy scope.
- A new WASDE report appears in `observations` and parquet exports within 24h of release
  with no human action.
- corn-soy-mvp consumes `data/exports/us_corn_balance.parquet` directly.

## Non-goals

- Modeling/forecasting (lives in corn-soy-mvp and dairy-model).
- Unit conversion or revision-adjusted series (store as published; derive downstream).
- Real-time intraday capture on release day (daily cadence is sufficient).
