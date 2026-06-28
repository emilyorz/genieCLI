# Code Review — v57 model-call budget for /trino-research (attempt 2)

**Verdict: PASS**

## Summary

Prior review (attempt 1) blocked on C1: two existing test files (`test_decompose_then_iterate.py`, `test_step_trace.py`) broke because `_produce_decompose_candidate()` changed its default from fragment-rewrite-on to fragment-rewrite-off without updating the tests. Develop attempt 3 fixed both by adding explicit `enable_fragment_rewrite=True` to the test call sites that exercise the fragment-rewrite path. The full targeted sweep (301 passed, 1 skipped) confirms both tests now pass. C1 is resolved.

Production code changes are well-designed: the decompose seed path is cleanly gated by `enable_fragment_rewrite` (default `False`), LLM monster ranking in `decompose()` is gated by `use_llm_ranking`, the env-var opt-in (`GENIE_FRAGMENT_REWRITE`) and cap (`GENIE_FRAGMENT_REWRITE_CAP`) are wired into both MCP paths, and provider/model failures are caught and converted to `model_failed` iterations in both the standard loop and plan-cost loop.

## Prior blocker resolution

### C1. Two existing test files broken by signature change — RESOLVED

- **Previous status**: BLOCKING
- **Current status**: Fixed in develop attempt 3
- **Evidence**: `tests/test_decompose_then_iterate.py:432` now passes `enable_fragment_rewrite=True, max_fragment_model_calls=5`. `tests/test_step_trace.py:591` now passes `enable_fragment_rewrite=True`. Full sweep 301 passed, 1 skipped.

## Open issues (non-blocking)

### L1. Stale docstring in `run_mcp_enhancement` (carried from develop review)

- **Severity**: Low
- **File**: `genie/skills/mcp_trino/research.py:2344`
- **Issue**: Docstring says `max_iterations: Number of enhancement rounds (default: 5)` but the actual signature at line 2325 is `max_iterations: int = 1`. The interactive prompt at line 3360 was correctly updated to `[1]`.
- **Risk**: Developer reading the docstring gets the wrong default. The actual behavior is correct — the signature and the user-facing prompt both say 1. This is a documentation-only mismatch in an internal function.
- **Verdict**: Non-blocking. It is inaccurate but does not affect runtime behavior or user-facing output. Should be fixed in a follow-up or at commit time.

### L2. `--direct` path call sites lack `GENIE_FRAGMENT_REWRITE` env-var wiring (carried)

- **Severity**: Low
- **Files**: `genie/skills/trino_query/research.py:415`, `:670`, `:1212`
- **Issue**: Three `--direct` path call sites rely on the default `enable_fragment_rewrite=False` without reading the `GENIE_FRAGMENT_REWRITE` env var. Unlike the MCP path siblings at `mcp_trino/research.py:1492` and `:2692`, there is no env-var escape hatch to re-enable fragment rewrite on `--direct`.
- **Risk**: Behavioral asymmetry between MCP and `--direct` paths. Fragment rewrite is permanently off on `--direct` with no override.
- **Verdict**: Non-blocking. The ticket explicitly scopes to `/trino-research` (MCP path). The `--direct` path getting evidence-only decomposition by default is correct behavior. The missing escape hatch is a minor gap for a path that is itself an opt-in mode.

### L3. AC3 cap test exercises trivial single-fragment case (carried)

- **Severity**: Low
- **File**: `tests/test_mcp_research.py:1273`
- **Issue**: `test_decompose_seed_opt_in_calls_fragment_llm` uses a single fragment with `max_fragment_model_calls=1`, so the slice `[:cap]` is trivially satisfied.
- **Risk**: Minimal — the slice logic is a one-liner (`[:_frag_cap]`) with no edge cases.
- **Verdict**: Non-blocking.

## Acceptance criteria status

| AC | Status | Evidence |
|----|--------|----------|
| AC1 | PASS | Default path skips fragment LLM ranking (`use_llm_ranking=False`) and optimize (`monsters = []` when `enable_fragment_rewrite=False`). Both MCP call sites wire env-var opt-in. |
| AC2 | PASS | `test_decompose_seed_default_does_not_call_fragment_llm` (test_mcp_research.py:1232) — sentinel `fail_if_called` raises if LLM is touched; asserts `recomposed_sql == sql` and `all(not candidate.changed)`. |
| AC3 | PASS | `test_decompose_seed_opt_in_calls_fragment_llm` (test_mcp_research.py:1258) — records LLM calls with `enable_fragment_rewrite=True` and asserts calls non-empty. Cap logic trivially satisfied (L3 above). |
| AC4 | PASS | Two integration tests: `test_standard_loop_model_failure_returns_enhancement_report` verifies `EnhancementReport` with `model_failed` iteration and `enhanced_sql == original_sql`. `test_plan_cost_loop_records_model_failure_without_raising` verifies `model_failed` in history without raising. |
| AC5 | PASS | Full sweep: 301 passed, 1 skipped across `test_mcp_research.py`, `test_strategy_verify.py`, `test_decompose_then_iterate.py`, `test_step_trace.py`, `test_trino_optimize.py`, `test_critical_path.py`. |

## Diff audit

6 files changed, +255/-29 lines.

### Production code (3 files)

- **`genie/skills/mcp_trino/trino_optimize.py`** (+17/-10): `decompose()` gains `use_llm_ranking: bool = True` keyword-only parameter. When `False`, heuristic monster IDs are used directly; LLM ranking call is skipped entirely. Fallback logic on LLM failure preserved. Clean.

- **`genie/skills/mcp_trino/research.py`** (+62/-16):
  - `_produce_decompose_candidate()` gains `enable_fragment_rewrite: bool = False` and `max_fragment_model_calls: int = 1`. These gate both the `decompose(use_llm_ranking=)` call and the monster optimization loop. When off, all fragments get passthrough candidates. Rationale string correctly distinguishes "fragment rewrite disabled" vs "non-monster or over-cap".
  - MCP standard-loop seed (`:2692`) and plan-cost-loop seed (`:1492`) both wire `GENIE_FRAGMENT_REWRITE` and `GENIE_FRAGMENT_REWRITE_CAP` env vars.
  - `max_iterations` default changed from 5 to 1 (signature `:2325` + interactive prompt `:3360`).
  - Standard-loop provider failure: `except Exception` catches around `provider.complete_text()`, renders a `model_failed` iteration, appends `IterationRecord`, and breaks. Report generation proceeds normally with unchanged best SQL.
  - Interactive prompt fallback default changed from 5 to 1.

- **`genie/skills/mcp_trino/preflight.py`** (+10/-1): Plan-cost-loop provider failure: `except Exception` around `provider.complete_text()`, appends `model_failed` dict to history, breaks. Matches the standard-loop pattern. `winner_sql` stays `None`.

### Test code (3 files)

- **`tests/test_mcp_research.py`** (+167/0): Four new tests covering AC2-AC5. `_fake_cost()` helper for cost stubs. `test_standard_loop_model_failure_returns_enhancement_report` is a substantial integration test with thorough monkeypatching. All assertions are specific and non-trivial.

- **`tests/test_decompose_then_iterate.py`** (+1/0): Line 432 adds `enable_fragment_rewrite=True, max_fragment_model_calls=5` to the frag-cap test that exercises the over-cap behavior.

- **`tests/test_step_trace.py`** (+1/0): Line 591 adds `enable_fragment_rewrite=True` to the fragment-SQL-in-event-detail test.

### Cross-file consistency

All 5 production call sites of `_produce_decompose_candidate` verified:
- 2 MCP-path sites: explicit env-var-driven kwargs. Correct.
- 3 `--direct`-path sites: rely on default `False`. Correct per ticket scope (L2 above).

All test call sites that exercise fragment-rewrite behavior now pass `enable_fragment_rewrite=True`. Test call sites that exercise default/evidence-only behavior correctly omit it.

## Verification

- Prior C1 blocker verified resolved: both test files fixed with minimal, correct changes.
- Full sweep: 301 passed, 1 skipped (confirmed by develop artifact).
- Stale docstring (L1) confirmed by reading line 2344 vs signature at line 2325.
- All `_produce_decompose_candidate` call sites inspected across both production files and all 4 test files.

## Recommendation

**Commit.** All 5 acceptance criteria pass. The prior C1 blocker is resolved. Three low-severity items remain (stale docstring, `--direct` path asymmetry, trivial cap test) — none are blocking. The docstring fix (L1) is a one-line change that could be included at commit time but is not required.

## Grade: A-

Production code is clean, well-gated, and correctly handles the failure paths. Tests cover all four acceptance criteria with real integration-level verification. The deduction is for the stale docstring (should have been caught during development) and the `--direct` path asymmetry (low-risk technical debt).
