# Core Principles

1. **Raw files are immutable.** Once a file lands in `data/raw/releases/`, it is never
   rewritten. A changed upstream file becomes a new versioned name. Everything downstream
   can be rebuilt from raw; nothing in raw can be rebuilt from anywhere.
2. **Vintage fidelity over convenience.** Store what the report printed (`value`,
   `raw_attribute`, `raw_value` in the OCR era). No unit conversions, no backfilled
   revisions, no "fixing" USDA's numbers. Derived/cleaned series belong downstream.
3. **No silent drops.** Every label a parser cannot resolve goes to `unmapped_labels`;
   for priority tables (corn/soy) it is a hard failure. Every skipped table or release
   shows up in the coverage report. Quarantined cells stay visible with `qa_status`.
4. **Exact-match registry, grown by humans.** Label aliases are added deliberately to
   `config/registry/*.yaml` after reading the QA report — never fuzzy-matched at runtime.
5. **Idempotent everything.** All DB writes go through `db.upsert()` (delete-then-insert
   on natural keys). Every script can be re-run safely; backfills resume via the HTTP
   cache; `state.json` advances only on fully-successful update runs.
6. **Trust nothing parsed; verify by identity.** Supply = beginning stocks + production
   + imports; total use = domestic + exports; ending = supply − use. Each projection is
   printed in two consecutive reports — they must agree. OCR additionally cross-checks
   AgManager. A number is accurate because it survived checks, not because parsing
   "looked right".
7. **Parsers emit, normalize resolves, db loads.** Parsers yield `ParsedCell` and never
   touch the database or registry internals. One contract, three eras.
8. **Tests are golden-file first.** Each format era has a trimmed real fixture and an
   expected-output CSV written *before* the parser. No live network in the default
   test run (`-m integration` for smoke tests).
9. **Be polite to USDA.** Cache every fetch, sleep between downloads, never hammer.
   The backfill is one-time by construction.
