# Decisions Log

Machine-searchable record of why things are the way they are. Newest first.

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

(Values checked by a human against the printed PDF, per the accuracy contract.
Format: date · release · cell · expected · observed · ok?)

_None yet — first entries due at M2._
