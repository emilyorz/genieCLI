# Ticket v36 - TLV4 Bugfix Profile

## Scope / Non-Scope

Scope:

- Bugfix/cleanup only for v34 residuals that do not require live cluster access.
- Fix stale `make_query_max_run_time_sql()` docstring so it matches current `CANDIDATE_TIMEOUT_HEADROOM = 2.0` and 2000ms floor.
- Make T-F-24 executable: prove `peak_memory_limit_bytes` threads into `_run_mcp_plan_cost_loop`, not just conditionally assert when the loop happens to fire.
- Improve `bad_env_fallthrough` breadcrumb when bad env var is followed by failed/unavailable `SHOW SESSION`; output must clearly say the 1 GiB fallback is now in effect.
- Conditional process cleanup: add v3 schema support to directly available `validate_ledger.py` templates only if this v36 run is allowed to touch external Task Ledger tooling paths.

Non-scope:

- Exclude live-cluster numeric validation. Do not probe Sam's office `mcp-trino`, `localhost:8811`, live `SHOW SESSION`, real casing/format, or production 0.5 fraction correctness.
- No new `/trino-research` feature behavior.
- Do not consume v35 residuals as main scope. v35 residuals are optional residuals only.

## Repro / Evidence

- `project-iterations/genieCLI/STATUS.md` says v35 is DONE and next action is consuming v34 residuals; it lists stale docstring, non-firing T-F-24, and incomplete `bad_env_fallthrough` breadcrumb.
- `genie/skills/mcp_trino/preflight.py` docstring still says `ceil(1.0 x baseline_wall_ms)` and 1000ms, while implementation uses `make_candidate_timeout_ms()` with `CANDIDATE_TIMEOUT_HEADROOM = 2.0` and 2000ms floor.
- `tests/test_per_node_memory_limit.py::TestMcpPathThreading::test_show_session_fetched_once_per_run` only checks `peak_memory_limit_bytes` under `if plan_cost_loop_kwargs:`, so the intended assertion silently skips when `_run_mcp_plan_cost_loop` is not entered.
- `genie/skills/mcp_trino/research.py` prints bad env as "falling through to SHOW SESSION" even when `SHOW SESSION` fails and the effective behavior is fallback.
- `/Users/leeabc/.codex/skills/task-ledger-cycle/templates/validate_ledger.py` and `/Users/leeabc/.claude/skills/task-ledger-cycle/templates/validate_ledger.py` exist, but are external Task Ledger tooling, not repo product code.

## Expected vs Actual

- Expected: timeout docstring says 2x headroom and 2000ms floor. Actual: docstring says 1.0x and 1000ms.
- Expected: T-F-24 fails if `_run_mcp_plan_cost_loop` does not receive the resolved 5 GiB limit. Actual: assertion is conditional and can be skipped.
- Expected: bad env + failed `SHOW SESSION` breadcrumb says fallback is active. Actual: breadcrumb stops at "falling through to SHOW SESSION".
- Expected: v3 ledger validation support is available when using maintained Task Ledger tooling. Actual: direct template validator still documents and implements v1/v2-only assumptions.

## Implementation Tasks

1. P1 - Repair T-F-24 coverage.
   - Update `tests/test_per_node_memory_limit.py::TestMcpPathThreading::test_show_session_fetched_once_per_run`.
   - Patch or provide an MCP explain runner / plan-cost eligible setup so `long_query_opt_in=True` actually reaches `_run_mcp_plan_cost_loop`.
   - Remove the `if plan_cost_loop_kwargs:` guard.
   - Assert `_run_mcp_plan_cost_loop` is called exactly once and receives `peak_memory_limit_bytes == 5 * 1024**3`.

2. P1 - Fix `bad_env_fallthrough` breadcrumb.
   - Keep source-tag behavior stable.
   - When source is `bad_env_fallthrough` and bytes is `None`, print a message that includes both the bad env fallback-to-`SHOW SESSION` and final 1 GiB fallback.
   - Add or adjust a unit test covering bad env plus `SHOW SESSION` error/failure.

3. P2 - Fix stale docstring.
   - Update `make_query_max_run_time_sql()` docstring only; no behavior change.
   - Make it refer to `make_candidate_timeout_ms()` / `CANDIDATE_TIMEOUT_HEADROOM`, 2x headroom, and 2000ms floor.

4. P3 - Track `validate_ledger.py` v3 schema support as out-of-repo residual for this v36.
   - Do not edit external Task Ledger tooling in this repo-scoped v36 unless Sam explicitly authorizes that broader scope.
   - Record the residual in v36 wrap/status with a concrete next action.

## Acceptance Criteria

- `python3 -m pytest tests/test_mcp_preflight.py::TestMakeQueryMaxRunTimeSql -q`
- `python3 -m pytest tests/test_per_node_memory_limit.py::TestMcpPathThreading::test_show_session_fetched_once_per_run -q`
- `python3 -m pytest tests/test_per_node_memory_limit.py -q`
- `python3 -m pytest tests/test_mcp_plan_cost_loop.py -q`
- `python3 -m pytest -q`
- `git diff --check`
- No live `mcp-trino`, `localhost:8811`, or production cluster probe is required or performed.

## Risks / Residuals

- T-F-24 may need a small test fixture to make MCP plan-cost eligibility true; do not weaken production gating just to satisfy the test.
- Breadcrumb copy must not imply live `SHOW SESSION` numeric validation happened.
- `validate_ledger.py` is external tooling, not in-repo product code; classify it as residual unless separately authorized.
- Optional residuals only, not v36 main scope: v35 report timestamp collision, default MCP interactive paste reachability-before-paste limitation, and preferred test-file shape mismatch.
