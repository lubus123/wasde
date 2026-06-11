# Claude Instructions — wasde-data

Read README.md, PROJECT_OBJECTIVE.md, CORE_PRINCIPLES.md, docs/DECISIONS.md first.
They are kept current; trust them over re-deriving context.

## Working rules

- **TDD.** Write/extend the golden fixture + expected CSV before touching a parser.
  All tests green (`.venv/bin/python -m pytest`) and ruff clean before any "done" claim.
- **Coverage gate: ≥85%** on `src/wasde_data`
  (`.venv/bin/python -m pytest --cov=src/wasde_data --cov-fail-under=85`). Validate with
  every shipped feature.
- **Run everything you present.** Scripts must have been executed against real data
  before claiming they work. Record QA outcomes honestly.
- **Never weaken QA to make it pass.** A failing identity/continuity check means the
  data or parser is wrong — find out which. Quarantine, don't delete.
- **Update DECISIONS.md** when you make a non-obvious choice; update the
  hand-verification log when you check values against the PDF.
- **Never edit files under `data/raw/`** — they are immutable archive.
- The ESMIS API and file URLs are documented in docs/DECISIONS.md (2026-06-11 entry).
  www.usda.gov is unreachable from this environment; do not build anything on it.
