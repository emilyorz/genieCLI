# Wrap + Retro — v57 model-call budget for /trino-research

## Outcome

DONE. Code commit: `e11c472` (`fix(trino-research): bound v57 model calls and preserve reports`).

## Shipped behavior

- Default `/trino-research` decompose seed is evidence-only: no LLM monster ranking and no per-fragment LLM rewrite unless explicitly opted in.
- Fragment rewrite opt-in: `GENIE_FRAGMENT_REWRITE=1`.
- Fragment rewrite cap: `GENIE_FRAGMENT_REWRITE_CAP`.
- Interactive `/trino-research` default max iterations is now `1`.
- Standard-loop and plan-cost-loop provider/model failures are recorded as `model_failed` and still produce report objects.

## Verification

Final targeted sweep:

```text
python -m pytest tests/test_mcp_research.py tests/test_strategy_verify.py tests/test_decompose_then_iterate.py tests/test_step_trace.py tests/test_trino_optimize.py tests/test_critical_path.py -q
301 passed, 1 skipped
```

Compile check:

```text
python -m py_compile genie/skills/mcp_trino/research.py genie/skills/mcp_trino/trino_optimize.py genie/skills/mcp_trino/preflight.py tests/test_mcp_research.py tests/test_decompose_then_iterate.py tests/test_step_trace.py
passed
```

## Worked

- V4 review gate caught a real regression class: tests outside the initially run set also exercised `_produce_decompose_candidate()` and needed explicit opt-in.
- Backedge `review → develop` worked cleanly and forced the missing tests into the final targeted sweep.
- Provider failure is now handled in both standard and plan-cost loops.

## Failed / friction

- Initial subagent auth failure caused degraded early ticket/develop handling.
- First develop verification was too narrow: it missed `tests/test_decompose_then_iterate.py` and `tests/test_step_trace.py`.
- Gate reviewer attempt 2 produced useful prose but did not overwrite `review.review.json`; main control had to write the hash-bound gate JSON.

## Change next time

- For any signature/default behavior change to a shared helper, run `rg` for all production and test call sites before claiming develop complete.
- For V4 review gate agents, explicitly require them to overwrite stale review JSON from prior attempts and verify reviewer_dispatch_id/hash before returning.
- Keep the test sweep tied to changed helper call graph, not only the files named in the ticket.

## Open follow-ups

- Fix stale internal docstring: `run_mcp_enhancement()` docstring still says max iterations default is 5 while runtime default is 1.
- Decide whether `--direct` should get the same `GENIE_FRAGMENT_REWRITE` opt-in wiring as MCP path.
