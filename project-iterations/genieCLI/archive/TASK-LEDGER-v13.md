# TASK-LEDGER

## Basic Info

- Project: genieCLI v13 — Subtraction & Deduplication
- Repo Folder: project-iterations/genieCLI/
- Iteration: 13
- Owner: Emily (Claude Code)
- Status: complete
- Last Updated: 2026-04-14T13:00+08:00
- Current Focus: Done

## Goal

- One-line summary:
  Remove re-export shims, deduplicate shared SQL helpers, and consolidate provider test infrastructure.
- Done when:
  1. Re-export shims deleted, importers point to canonical sources;
  2. Duplicate `_extract_sql_from_reply` extracted to `genie/core/`;
  3. Provider test `_msg()` helper deduplicated via conftest.py;
  4. Tests passing;
  5. Committed.

## Carryover

- v10 T9 (live verify on Sam's MCP server) remains blocked on Sam.

## Todo

| ID  | Status | Pri | Task                                                              | Owner | Note                                |
| --- | ------ | --- | ----------------------------------------------------------------- | ----- | ----------------------------------- |
| T1  | done   | P0  | Delete oracle2trino/patterns.py + runtime/eval_loop.py shims      | Emily | 3 importers updated                 |
| T2  | done   | P0  | Extract `_extract_sql_from_reply` to genie/core/sql_extraction.py | Emily | 2 research modules + 1 test updated |
| T3  | done   | P1  | Move `_msg()` helper to conftest.py, remove from 3 test files     | Emily | Provider test dedup                 |
| T4  | done   | P1  | Run tests + verify all rounds                                     | Emily | 587 passed, 10 skipped, 0 failed    |

## Reports

### Ledger setup — 2026-04-14T12:00+08:00

- Opened v13 as three-round subtraction ledger.
- Prior state: 92 Python files, ~16700 LOC, 588 tests.

### Round 1 — Delete re-export shims — 2026-04-14T12:30+08:00

**Goal:** Remove `oracle2trino/patterns.py` and `runtime/eval_loop.py` — both were thin re-export shims adding indirection with no value.

**Files changed:**

- `genie/skills/oracle2trino/patterns.py` — DELETED (re-exported from `genie/core/sql_patterns`)
- `genie/runtime/eval_loop.py` — DELETED (re-exported from `genie/runtime/autoresearch_cli`)
- `genie/skills/oracle2trino/__init__.py` — import path updated
- `genie/runtime/autoresearch_cli.py` — import path updated
- `tests/test_oracle2trino_structured.py` — import path updated
- `tests/test_sql_patterns.py` — removed 2 stale tests for deleted shim

**Verification:** 100 targeted tests passed (0 failures).

### Round 2 — Extract sql_extraction — 2026-04-14T12:45+08:00

**Goal:** Deduplicate `_extract_sql_from_reply` — identical 15-line function copy-pasted in `trino_query/research.py` and `mcp_trino/research.py`.

**Files changed:**

- `genie/core/sql_extraction.py` — NEW: canonical `extract_sql_from_reply()`
- `genie/skills/trino_query/research.py` — replaced inline def with import
- `genie/skills/mcp_trino/research.py` — replaced inline def with import
- `tests/test_mcp_research.py` — import updated to `genie.core.sql_extraction`

**Verification:** 100 targeted tests passed (0 failures).

### Round 3 — Deduplicate \_msg() test helper — 2026-04-14T13:00+08:00

**Goal:** Three provider test files had identical `_msg(role, text)` helper. Moved to `tests/conftest.py`.

**Files changed:**

- `tests/conftest.py` — added `_msg()` helper
- `tests/test_anthropic_provider.py` — removed local `_msg`, added `from conftest import _msg`
- `tests/test_openai_provider.py` — same
- `tests/test_tgenie_provider.py` — same
- `pyproject.toml` — added `pythonpath = ["tests"]` for conftest importability

**Verification:** 587 passed, 10 skipped, 0 failed (full suite).

## Summary

| Metric         | Before                                         | After             | Delta                                                                      |
| -------------- | ---------------------------------------------- | ----------------- | -------------------------------------------------------------------------- |
| Python files   | 92                                             | 90                | -2 (shims deleted) + 1 (sql_extraction) = net -1                           |
| Duplicate code | 3× `_msg()` + 2× `_extract_sql` + 2 shim files | 0                 | -7 duplication sites                                                       |
| Tests          | 588 pass                                       | 587 pass, 10 skip | -1 (stale shim test removed), +10 skip (sqlglot-conditional, pre-existing) |

## Remaining risks

- `pythonpath = ["tests"]` in pyproject.toml: standard pytest pattern, but if tests/ grows an `__init__.py` later, naming collisions are possible. Low risk.
- `extract_sql_from_reply` is now a public function in `genie.core.sql_extraction` — if external code imports `_extract_sql_from_reply` from the old locations, it will break. No known external consumers.

## Next round assessment

Not recommended. The remaining duplication is either test-specific fixtures (appropriate to keep local) or modules with structurally different code. Further subtraction would be abstracting for abstraction's sake.
