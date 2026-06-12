---
covers:
  - "tests/test_mcp_preflight.py"
  - "tests/test_mcp_research.py"
  - "tests/test_mcp_rule_gate.py"
  - "tests/test_mcp_trino.py"
  - "tests/test_measure_backfill.py"
  - "tests/test_metric.py"
  - "tests/test_model_profiles.py"
  - "tests/test_model_switching.py"
  - "tests/test_new_commands.py"
  - "tests/test_openai_provider.py"
  - "tests/test_oracle2trino.py"
  - "tests/test_oracle2trino_structured.py"
  - "tests/test_output.py"
  - "tests/test_output_human.py"
  - "tests/test_per_iteration_rediagnosis.py"
  - "tests/test_per_node_memory_limit.py"
  - "tests/test_plan_cost_core.py"
  - "tests/test_plan_cost_loop.py"
  - "tests/test_plan_signature.py"
  - "tests/test_pre_execution_diagnosis.py"
last_synced: "572f7ff30399bed1a1a3c230918ba037ae874272"
---

## Purpose

Unit and integration tests for the MCP/Trino optimization pipeline and supporting subsystems. Covers MCP client plumbing (`test_mcp_trino`), preflight budget and long-query gate (`test_mcp_preflight`), per-iteration re-diagnosis and per-node memory limit threading (`test_per_node_memory_limit`, `test_per_iteration_rediagnosis`), plan-cost ranking loop (`test_plan_cost_core`, `test_plan_cost_loop`), static rule gate (`test_mcp_rule_gate`), pre-execution diagnosis direction emission (`test_pre_execution_diagnosis`), plan structural signature (`test_plan_signature`), measure/backfill isolation (`test_measure_backfill`), oracle-to-trino transpilation (`test_oracle2trino`, `test_oracle2trino_structured`), output sinks (`test_output`, `test_output_human`), model profiles and switching (`test_model_profiles`, `test_model_switching`), OpenAI provider SSE streaming (`test_openai_provider`), runtime metric extraction (`test_metric`), and session commands (`test_new_commands`).

## Exports

# No tracked files found for module 'tests/[direct-2]'

## Invariants

- `check_read_only` rejects DML (DELETE/UPDATE/DROP/INSERT) and multi-statement input, and accepts SELECT/WITH CTE/EXPLAIN/SHOW — `test_mcp_preflight.py:24-82`
- `run_preflight` tolerates `explain_runner` exceptions without raising — `test_mcp_preflight.py:144`
- `plan_cost` returns `(None, None, None)` on malformed JSON or runner exception; never raises — `test_mcp_preflight.py:173-188`
- `check_long_query_gate` blocks queries over the threshold unless `--long-query` opt-in is set; custom threshold and fallback count respected — `test_mcp_preflight.py:202-248`
- `make_query_max_run_time_sql` clamps baseline to minimum 2000 ms — `test_mcp_preflight.py:255`
- `apply_safe_limit` is a no-op when `limit=0` — `test_mcp_preflight.py:278`
- `_combine_cost` returns `None` when both rows and bytes are absent; zero rows is treated as a valid value, not `None` — `test_mcp_preflight.py:302-311`
- `_measure_mcp` propagates plain-run `CandidateTimeoutError` even when backfill also times out — `test_measure_backfill.py:46`
- `pre_execution_diagnosis` output is deterministic across repeated calls and independent of input ordering — `test_pre_execution_diagnosis.py:318-370`
- `pre_execution_diagnosis` emits no directions and does not raise when all inputs are `None` — `test_pre_execution_diagnosis.py:303`
- Static-report parse errors suppress all static findings without crashing — `test_pre_execution_diagnosis.py:468`
- `plan_signature` returns `None` for unparseable input and for `None`; table names are lowercased — `test_plan_signature.py:34-42`, `test_plan_signature.py:117`
- `structural_equivalent` returns `False` when either signature is `None` — `test_plan_signature.py:103`
- Plan-cost loop selects the candidate with lowest combined cost when L3 row-equivalence passes; falls through to no-verifiable result when all L3 checks fail — `test_plan_cost_loop.py:117`, `test_plan_cost_loop.py:316`
- `_fetch_per_node_memory_limit` falls back to a default when env var is bad, SHOW SESSION returns no matching row, or the MCP call raises — `test_per_node_memory_limit.py:103-212`
- Memory pressure direction is suppressed when the per-node limit is sufficiently large — `test_per_node_memory_limit.py:415`
- `extract_metric` caps raw output and returns an error result on non-zero exit, no float, timeout, or generic exception — `test_metric.py:71-108`
- `build_rule_gate_summary` fails open (returns empty/safe output) on malformed diagnostic objects — `test_mcp_rule_gate.py:92`
- Rule gate skips directions that duplicate static findings — `test_mcp_rule_gate.py:44`
- `OpenAIProvider` SSE stream raises on HTTP error, network error, empty SSE, and empty body — `test_openai_provider.py:152-195`
- `NullSink.confirm` always returns `True`; `MachineSink.confirm` always returns `True` — `test_output.py:29`, `test_output.py:59`
- `/compact` inserts a marker and preserves at least 1 turn; default keep is 6 turns — `test_new_commands.py:374`, `test_new_commands.py:383`, `test_new_commands.py:406`

## Change log

- 572f7ff30399bed1a1a3c230918ba037ae874272: initial card created
