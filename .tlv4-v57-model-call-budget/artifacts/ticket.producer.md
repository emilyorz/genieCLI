# Ticket — v57 stop fan-out model calls in /trino-research

## Repro

Run `/trino-research` on Sam's real MES rules-engine query: a large fuzzy-join query with per-row double `EXISTS`. On v56 the run performs many model/check cycles and appears to ask the model for every fragment/line. It eventually hits a provider/runtime limit and exits before writing the normal report.

## Expected

Default `/trino-research` should make a bounded number of model calls. For long/company queries the default path should be whole-query optimization guided by the offline critical-path/P-strategy evidence, with a default hard budget of one model optimization call unless the caller explicitly opts into more.

A failure, timeout, or budget stop should still produce a report containing baseline details, iteration history so far, failure reason, and unchanged best SQL when no safe improvement exists.

## Actual

The v48 seed path decomposes a query into CTE/root/predicate-subquery fragments, ranks monsters with an LLM, then optimizes up to five monster fragments with additional LLM calls. The plan-cost loop also enables this seed path before entering the whole-query loop.

Evidence:

- `genie/skills/mcp_trino/research.py:1976` `_produce_decompose_candidate()` documents `decompose→per-fragment-optimize(cap=5)`.
- `genie/skills/mcp_trino/research.py:2029` calls `_decompose(sql, llm_fn, cost_reader_fn)`.
- `genie/skills/mcp_trino/trino_optimize.py:863` calls `llm(_build_monster_prompt(...))` for monster ranking.
- `genie/skills/mcp_trino/research.py:2128` loops over fragments; `research.py:2132` calls `_optimize(fr, llm_fn, cp_guidance=cp_guidance)` for each monster.
- `genie/skills/mcp_trino/research.py:1475` enables the seed path in the plan-cost loop by default via `GENIE_V48_SEED_DECOMPOSE != "0"`.
- `genie/skills/mcp_trino/research.py:2658` enables the same seed path in the standard loop by default.
- `genie/skills/mcp_trino/research.py:3394` only writes the final MCP report after `run_mcp_enhancement()` returns; unhandled provider/runtime failures before return can skip report writing.

## Root cause hypothesis

v48-v56 conflated two roles:

1. decomposition as evidence/reporting, and
2. decomposition as a default rewrite engine.

For large SQL, role (2) creates fan-out model calls before the normal whole-query optimizer even starts. v55 critical-path guidance is then applied to every fragment rewrite call, which steers calls but does not bound call count.

## Fix scope

- Make default decompose seed evidence-only: collect trace/critical-path/decompose information without fragment rewrite model calls.
- Default model-call budget for optimization should be one whole-query optimization call on the existing plan-cost/standard loop path.
- Fragment rewrite must require an explicit opt-in flag/env and respect a hard cap.
- Provider/model exceptions in the enhancement loop should be converted into iteration status and final/partial report output, not uncaught abort.

## Non-goals

- Do not redesign P9 verifier correctness.
- Do not remove deterministic decorrelation pre-pass when it does not call the model.
- Do not add live company SQL fixtures.
- Do not push.

## Acceptance criteria

1. Default `_produce_decompose_candidate()` path must not call fragment LLM ranking or fragment optimize for read execution.
2. A test proves default seed/decompose path makes zero advisory fragment LLM calls and returns original SQL when deterministic decorrelation does not apply.
3. A test proves opt-in fragment rewrite still calls the fragment LLM path and remains capped.
4. A test proves provider failure during the standard iteration loop returns an `EnhancementReport` with an `exec/model_failed` style iteration instead of raising before report generation.
5. Existing `tests/test_strategy_verify.py` and relevant `tests/test_mcp_research.py` pass.

## Files affected

- `genie/skills/mcp_trino/research.py`
- `genie/skills/mcp_trino/trino_optimize.py` only if function signature support is required
- `tests/test_mcp_research.py`
