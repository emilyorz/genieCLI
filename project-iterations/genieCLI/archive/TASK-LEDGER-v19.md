# TASK-LEDGER

## Basic Info

- Project: genieCLI v19 — /trino-research safety guardrails (evaluate level)
- Repo Folder: project-iterations/genieCLI/
- Iteration: 19
- Owner: Emily (Claude Code)
- Status: done
- Updated: 2026-04-16T14:30+0800
- Focus: Add a pre-flight "evaluate level" gate before any SQL
  execution to prevent OOM, rogue DML, and runaway queries.

## Motivation

Sam: "我們有辦法先評估這個 query 出來的資料量級會多大嗎？我怕 CLI
會 OOM 或是 AI 直接炸掉"

Current state before v19:

- No pre-flight size estimation. Huge result sets load into Python list,
  then normalized to JSON for equivalence check → 2x memory → OOM risk.
- No read-only enforcement. AI could (in principle) generate DML/DDL.
- Timeout default 30s at HTTP layer — too short for many optimization
  queries, too permissive once we know the query is bad.

## Goal

- One-line summary:
  Before the first execution, gate the SQL through an "evaluate level"
  (read-only check + size estimate + timeout budget). Hard-cap what we
  load into memory for comparison.
- Done when:
  1. Read-only whitelist (SELECT/WITH/EXPLAIN/SHOW) enforced; DML/DDL blocked; ✅
  2. EXPLAIN (FORMAT JSON) pre-flight estimates rows/bytes from Trino CBO; ✅
  3. Hard row capture cap (default 100k) prevents OOM in Python; ✅
  4. Truncation-safe row count comparison (checks full count before subset equiv); ✅
  5. Opt-in `--safe-limit N` wraps SQL in `SELECT * FROM (...) LIMIT N`; ✅
  6. Per-query timeout raised from 30s → 300s (configurable via `--query-timeout`); ✅
  7. Tests: 25 new (read-only, EXPLAIN parse, size budget, safe-limit wrap); ✅
  8. Full regression: 616 pass, 0 fail; ✅

## New module: `genie/skills/mcp_trino/preflight.py`

- `check_read_only(sql)` — regex + keyword check; blocks DML/DDL/multi-statement
- `estimate_from_explain(explain_json)` — parses Trino JSON EXPLAIN, walks tree, extracts first `outputRowCount` + `outputSizeInBytes`
- `run_preflight(sql, explain_runner, budget)` — orchestrates: read-only → EXPLAIN estimate (soft if tool missing) → budget check → PreflightReport
- `apply_safe_limit(sql, n)` — wraps SQL with a guarded outer LIMIT
- `PreflightBudget` — configurable caps (default: 100k rows, 100 MB, 100k capture)

## Changes

| File                                  | Change                                                                                                                                                                  |
| ------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `genie/skills/mcp_trino/preflight.py` | NEW — pre-flight gate logic                                                                                                                                             |
| `genie/skills/mcp_trino/research.py`  | `run_trino_research_via_mcp`: inject preflight + safe_limit + query_timeout; `_measure_mcp`: honour `max_capture_rows`; iteration loop: truncation-safe row count check |
| `genie/chat.py`                       | Parse `--safe-limit`, `--query-timeout` flags; updated help text                                                                                                        |
| `tests/test_mcp_preflight.py`         | NEW — 25 tests for all preflight surfaces                                                                                                                               |

## Verification

- 616 tests pass (up from 591; +25 new)
- Read-only rejects: DELETE, UPDATE, DROP, INSERT, multi-statement, empty, only-comments
- Read-only accepts: SELECT, WITH CTE, EXPLAIN, SHOW
- EXPLAIN parsing: root-level estimates, child estimates, invalid JSON tolerated, missing estimate → None
- Budget rejection: 10M rows with 1M budget → blocked with actionable error
- Safe-limit wrap: semicolons stripped; zero-limit passthrough
- EXPLAIN runner failure is non-blocking: preflight OK with `estimated_rows=None`

## User-facing behavior

- Default: pre-flight runs silently when SQL is SELECT and EXPLAIN works
- Failure: `Pre-flight rejected: <reason>` + estimate if available, then return
- Flags: `--safe-limit N` (wrap), `--query-timeout S` (override, default 300s)

## Retro

- **Worked:** Isolating the preflight logic in its own module kept
  research.py clean and tests focused. The `explain_runner` callable
  injection made the preflight testable without mocking the whole
  MCP client.
- **Failed:** Initial plan to use sqlglot for SQL parsing was
  dropped — the keyword/regex approach is simpler and sqlglot is
  already pulled for other things. Good enough for a defense-in-depth
  layer (server-side will also reject illegal ops on a truly
  read-only catalog).
- **Change next (deferred to v20):**
  - Non-deterministic SQL detection (NOW, RAND, UUID → baseline drift)
  - Iteration cost budget (cap total query runs × cost)
  - MCP server row-limit auto-detection (so we know if baseline was truncated by the server)
  - Cache warm-up run before measurement
  - Dedup on AI-proposed hypotheses
