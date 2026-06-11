# Decisions Log

Machine-searchable record of why things are the way they are. Newest first.

## 2026-06-11 — M5 tesseract pass results (full scan era)

- 137 releases parsed, 17,045 cells: 12,898 ok / 2,372 identity-corrected /
  1,337 quarantined. Quarantine concentrates 1981-87 (fax quality);
  1989-94 are nearly clean.
- **The structured per-commodity layout reaches back to ~1980**, not 1985 —
  the era estimate in the original plan was conservative. The compact-format
  era is 1973-79 (plus a few 1980 releases).
- **49 structured-era releases (1985-93 scattered) located ZERO pages** —
  header-strip OCR too degraded to match the page titles. Not silent: they
  show as coverage gaps. The GOT pass must also re-attempt page location for
  these, not just re-read pages tesseract found.
- 69 residual identity fails on unquarantined groups (mostly soy use-identity
  with merged 'Seed & residual' era variants) — targets for the dual-reader
  reconcile.

## 2026-06-11 — M5 dual-reader pivot (tesseract x GOT-OCR 2.0)

- **Second independent reader instead of tesseract tuning.** Tesseract's and a
  neural OCR model's error processes are independent — two readers agreeing on
  a digit is far stronger evidence than either alone, and it collapses the
  manual worklist versus single-reader quarantining. Acceptance rule
  (scripts/06_reconcile.py): agree + identities -> ok; disagree -> the reader
  whose column satisfies the balance identities wins; no arbiter -> worklist.
- **Reader #2 = PaddleOCR (PP-OCRv5 mobile, 250dpi)** — free and local per
  Lubo's call. GOT-OCR 2.0 was tried first and retired: its autoregressive
  decoder needs >12 min/page on CPU inside Docker-on-M4 (no GPU passthrough),
  even int8-quantized. Paddle's CNN det+rec reads the same hard page in 26s
  with 8/8 ground-truth digits. Page texts cache under data/raw/paddle_text/;
  output flows through the SAME parse_page machinery as tesseract (colon-
  optional label split + engine-tolerant label prefixes), so the readers
  differ only in engine.
- **Arbitration is per-cell with identity backing** (scripts/06_reconcile.py):
  agreed cells anchor the group; disputed cells are decided by a unique
  identity-passing combination of reader choices; a win counts only for cells
  that participate in a fully-testable identity (no vacuous verification).
  Single-reader cells outside every identity -> 'warn' (visible, unverified);
  true conflicts with no arbiter -> 'quarantined' + worklist.
- **Design validated with a one-page Claude vision probe** (≈$0.02, then
  stopped): on the hardest sample (June 1985 corn page) the second reader
  produced columns where EVERY balance identity passed, and the identity
  algebra adjudicated both tesseract misreads exactly (beginning stocks
  3,120 not 4,120: 3,120+4,175+2=7,297 = printed supply; production 7,656 not
  7,636: 723+7,656+2=8,381 = printed supply). VLM hallucination risk is real
  in general, which is why agreement+identities remains the acceptance rule
  for ANY reader pair.
- Identity algebra extracted to src/wasde_data/identity.py (shared by
  scripts/05 repair and scripts/06 reconcile).

## 2026-06-11 — M5 OCR design (1973-94 scan era)

- **Two print eras inside the scan era.** ~1985-1994 (27-32 pages): per-commodity
  balance sheets in the layout the TXT era inherited — handled by
  parsers/ocr_parser.py. 1973-~1984 (4-17 pages): compact all-commodity summary
  tables — DEFERRED (separate parser needed; releases stay visible as
  no-observation rows in coverage). Era boundary is detected naturally: page
  titles like 'U.S. Feed Grains and Corn' don't exist before ~1985.
- **Column years are derived from the report date, not OCRed.** Fax-era OCR
  reads '1983/84' as '1983/64'; WASDE's column convention is fixed
  ([base-2 actual, base-1 est, base proj x prior+current month], base rolls in
  May, May prints a single first-projection column), so years are derived and
  the noisy header is ignored.
- **Raw OCR is never trusted; identities repair, then quarantine.** Signed
  balance identities (supply = beg+prod+imports; use = dom+exports or soy
  components; ending = supply-use) run per column group: a single missing or
  uniquely-repairable member is derived (qa_status='corrected', including rows
  whose printed line was unreadable - raw_attribute='(derived from identity)');
  ambiguous groups quarantine wholesale. Observed 1985 sample: ~45% of cells
  quarantine on the first pass - the next accuracy lever is the cross-report
  reconciliation pass (each number is printed in two consecutive reports; two
  independent OCR reads agreeing is strong evidence), then AgManager finals
  for actual columns, then data/manual/ocr_overrides.csv for the remainder.
- **Title regexes tolerate OCR confusions** ('U.5.', 'C0rn'); numeric tokens
  get a digit-confusion translation (i/l/I->1, o/O->0, S->5, B->8...) applied
  only to numeric-ish tokens; '1.181'-style three-decimal artifacts are
  thousands separators.

### M5 remaining work (for the next session)

1. Cross-report reconciliation pass (06-style script): match report N's
   quarantined cells against report N+1's prior-month column and N-1's
   next-month reprint; agreement -> corrected, disagreement -> stays
   quarantined. Add AgManager check for 'actual' columns.
2. Manual override workflow for the residue (data/manual/ocr_overrides.csv,
   applied last, qa_status='corrected').
3. Pre-1985 compact-table parser (commodity rows x year columns, 4-17 pages).
4. Goal unchanged: zero quarantined corn/soy cells 1973-94.

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
- **`forecast_month=''` sentinel** (empty string, not NULL) for finalized-year
  columns — DuckDB primary keys reject NULLs, and the projection-month column is part
  of the natural key (a June report carries both May and Jun projection columns).
  (2026-06-11: this doc previously said `'Est'`; the implementation and data use `''`
  — corrected to match reality.)
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
