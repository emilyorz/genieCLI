# Develop Artifact — v57 model-call budget for /trino-research

## Files changed

- `genie/skills/mcp_trino/trino_optimize.py`
- `genie/skills/mcp_trino/research.py`
- `genie/skills/mcp_trino/preflight.py`
- `tests/test_mcp_research.py`
- `tests/test_decompose_then_iterate.py`
- `tests/test_step_trace.py`

## Behavior changed

- `decompose()` now supports `use_llm_ranking=False`; heuristic fragment detection still runs, but LLM monster ranking is skipped when disabled.
- `_produce_decompose_candidate()` defaults to evidence-only decomposition (`enable_fragment_rewrite=False`). Deterministic decorrelation can still apply, but per-fragment LLM rewrite is off unless explicitly enabled.
- MCP standard-loop and plan-cost-loop seed paths use `GENIE_FRAGMENT_REWRITE=1` as the explicit opt-in for fragment rewrite.
- `GENIE_FRAGMENT_REWRITE_CAP` bounds opt-in fragment rewrite candidates.
- `/trino-research` interactive default max iterations is now 1 instead of 5.
- Standard-loop provider/model exceptions become a `model_failed` iteration and return a normal `EnhancementReport` with unchanged best SQL.
- Plan-cost-loop provider/model exceptions append `model_failed` to core history so the MCP adapter can still build a report.

## Review fixes

- Develop review attempt 1 hard-failed AC4 because standard-loop provider failure lacked an integration test. Added `test_standard_loop_model_failure_returns_enhancement_report`.
- Review step then found two existing tests broken by the new default-off fragment rewrite behavior. Updated the tests that intentionally exercise fragment rewrite to opt in explicitly:
  - `tests/test_decompose_then_iterate.py`: `enable_fragment_rewrite=True, max_fragment_model_calls=5`
  - `tests/test_step_trace.py`: `enable_fragment_rewrite=True`

## Verification

Ran:

```text
python -m py_compile genie/skills/mcp_trino/research.py genie/skills/mcp_trino/trino_optimize.py genie/skills/mcp_trino/preflight.py tests/test_mcp_research.py tests/test_decompose_then_iterate.py tests/test_step_trace.py
python -m pytest tests/test_mcp_research.py tests/test_strategy_verify.py tests/test_decompose_then_iterate.py tests/test_step_trace.py tests/test_trino_optimize.py tests/test_critical_path.py -q
```

Results:

```text
301 passed, 1 skipped
py_compile: passed
```

## Known limitations

- The company MES query itself was not run locally because the Trino/MCP company environment is not available in this workspace.
- Fragment rewrite remains available only through environment opt-in; no new CLI flag was added to keep the public CLI surface small for this bugfix.
- `--direct` path now defaults to evidence-only via the shared helper default; explicit `GENIE_FRAGMENT_REWRITE` opt-in wiring for `--direct` is deferred because this v57 ticket targets `/trino-research` MCP behavior.
