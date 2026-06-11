# Decisions Log

Machine-searchable record of why things are the way they are. Newest first.

## 2026-06-11 — QA interpretation notes

- **cross_source_agmanager warns are the vintage-vs-final gap, not errors.**
  Our 'latest actual' cell is the last number WASDE ever printed for that
  marketing year; AgManager carries today's revised final (USDA keeps revising
  via Grain Stocks / Census after WASDE stops printing the year — AgManager's
  decimals, e.g. 338.141, are giveaways of post-WASDE revisions). E.g. corn
  2007/08 ending stocks: WASDE final print 1,624 vs revised 1,685. The check
  stays as a warn: for the OCR era it bounds plausibility; identities and
  consecutive-report continuity are the hard checks.
- **The QA net caught a real 1995 misprint**: July 1995 meal production was
  printed 32,685 (supply only adds with 32,920); the August 1995 report
  reprints the corrected 32,920. Both sides whitelisted with full reasoning in
  config/qa_whitelist.yaml.
- **Continuity ULP tolerance**: reprinted prior-month columns are re-rounded
  from unrounded internals (45.12 -> 45.13); differences of one printed ULP
  are not revisions and are filtered in check_mom_continuity.

## 2026-06-11 — M1 backfill findings

- **697 distinct releases, not 699.** The ESMIS catalog contains three entries
  dated 2019-11-08 (ids 95544/95545/95546) whose files are all named
  `latest.*` — stale aliases from an old "current report" mechanism. One
  (95545) still served real bytes during backfill and its XML is verified
  `Report_Month="November 2019"`; the other two now 404. Archive dedup by
  release_id collapsed them correctly. Every real report 1973-09→2026-06 is on
  disk: 1,358 files, sha256-manifested, zero fetch errors.
- **AgManager is finals, not vintages.** K-State's corn/soy workbooks hold the
  *latest-revised* balance sheet per marketing year (refreshed with each WASDE,
  verified updated same-day as the June 2026 report). They therefore
  cross-validate the finalized 'actual' columns of historical reports — the
  majority of OCR-era cells. Projection-column vintages are validated by
  balance identities + consecutive-report continuity (each projection is
  printed in two consecutive reports). `xSoybeans_Monthly.xlsx` looked like
  vintage data but is a current-year template only — not used.
- **Cron**: container cron service enabled; daily 17:10 UTC entry runs
  `scripts/10_update.py` (release is ~12:00 ET on the 9th–15th; daily polling
  beats date math). The mars-archive crontab pattern was already proven here.
- **OCR feasibility probed** (1985 + 1975 scans): tesseract 5.3.0 locates the
  corn/soy pages reliably at 150dpi and reads table structure at 350dpi, but
  digit confusion (i/1, o/0, brace/paren) is frequent — confirming the
  quarantine-until-3-way-validated design rather than trusting raw OCR.

## 2026-06-11 — M0 skeleton

- **ESMIS over www.usda.gov as the only primary source.** ESMIS
  (`esmis.nal.usda.gov`, the relocated Cornell usda.library.cornell.edu) hosts all 699
  releases 1973-09→present with a free, no-auth JSON API
  (`/api/v1/release/findByIdentifier/wasde?page=N`, 25/page). `www.usda.gov` hangs after
  the TLS handshake from this environment (Akamai-level block) — its OCE consolidated
  CSVs (2010+) are redundant with the ESMIS XML anyway. Decision: build entirely on
  ESMIS; treat OCE CSVs as an optional future cross-check.
- **Format eras (verified by enumerating all 699 releases via the API).**
  XML from 2010-07-09 (192 releases); fixed-width TXT 1995-01-12 → 2010-06 (and again
  2016+ alongside XML); 1973→1994 are CCITT fax-scan PDFs with no text layer (verified
  on 1975 + 1985 samples) → OCR with triple validation. XML is canonical wherever it
  exists; TXT is canonical 1995→2010-06; OCR fills 1973→1994.
- **One long fact table** (`observations`) instead of per-commodity tables. Both core
  trading queries (vintage history of one cell; May→June diff within one report) are
  single filters on the long table. ~30 sub-reports × per-domain DDL would add schema
  surface with no query benefit. Convenience views (`us_corn_balance`, …) give the
  per-domain ergonomics.
- **`forecast_month='Est'` sentinel** instead of NULL for finalized-year columns —
  DuckDB primary keys reject NULLs, and the projection-month column is part of the
  natural key (a June report carries both May and Jun projection columns).
- **vN revisions are separate releases** (`wasde-2026-05-12-v2`), `is_latest` flag on
  the newest. Both vintages are preserved — a revision IS new information with a
  timestamp, exactly what a vintage dataset must keep.
- **Exact-match label registry** (normalize → dict lookup), no fuzzy matching. Fifty
  years of label drift is finite and enumerable; fuzzy matching would trade silent
  misclassification risk for convenience, the wrong trade at a zero-error bar.
  Registry misses are recorded, and fail hard on corn/soy tables.
- **Values stored as published, no unit conversion.** Corn is million bushels
  domestically and MMT in world tables; converting at ingest would bake in rounding
  and hide transcription errors from the identity checks.
- **Conventions cloned from dairy-model** (http_cache, db.upsert, config, pytest+respx,
  numbered scripts) **and mars-archive** (immutable raw + manifest.jsonl + state.json,
  advance-state-only-on-zero-errors). These are battle-tested against USDA endpoints;
  divergence would need a reason.

## Hand-verification log

(Values checked against the printed report, per the accuracy contract.)

- 2026-06-11 · wasde-2026-06-11 (XML era) · full US corn table vs PDF page 12
  rebuilt from word positions: Beginning Stocks 1,763/1,551/2,142(May)/2,145(Jun);
  Production 14,892/17,021/15,995/15,995; Imports 22/28/25/25; Supply
  16,677/18,600/18,162/18,165; Feed&Residual, FSI, Ethanol, Domestic, Exports,
  Use, Ending Stocks 1,551/2,145/1,957/1,960; Farm Price 4.24/4.15/4.40/4.40 —
  ALL 18 cells match the database exactly.
- 2026-06-11 · wasde-2005-06-10 (TXT era) · corn ending stocks
  958/2,215/2,540(May)/2,540(Jun) vs raw text — match (locked in
  tests/test_txt_parser.py::test_2005_corn_values_match_print).
- 2026-06-11 · wasde-1995-06-12 (TXT era) · corn ending stocks 850/1,538/998(May)/
  748(Jun) and soybean price ranges 5.10-6.10 -> 5.60 / 5.25-6.25 -> 5.75
  (wrapped-range rows) vs raw text — match.
- 2026-06-11 · stocks-chain identity: ending_stocks(year N) ==
  beginning_stocks(year N+1) within the June 2026 report — holds for corn.
- 2026-06-11 · cross-unit: US corn 2026/27 Jun ending stocks 1,960 Mbu x
  0.0254 = 49.78 MMT == world-table US row (locked in
  tests/test_xml_parser.py::test_world_corn_unit_consistency_with_us_corn).
