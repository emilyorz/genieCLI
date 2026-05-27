# Quality Verification Report — v29 T2

**Verdict**: APPROVED
**Quality score**: 9.3 / 10
**Gate (> 9.0) cleared**: YES
**Context Packet**: v29-T2-quality (spec-verifier: SPEC_COMPLIANT, prerequisite met)

## Observed test counts (independently re-run)

- `tests/test_pre_execution_diagnosis.py` + `tests/test_pre_execution_diagnosis_wiring.py`: **38 passed** in 0.27s
- Full suite (`.venv/bin/python -m pytest -q`): **772 passed** in 1.59s, 0 failed, 0 skipped
- Matches claimed ~772. (T1 baseline 755 + 10 skip; current tail shows no skip line → skips now 0. Delta is consistent with new wiring/format tests + prior skips resolved; no regressions.)

## Strengths

- `pre_execution_diagnosis.py:311-335` `format_directions_for_prompt` — cohesive, pure, deterministic. Empty-list → `""` contract lets both callers gate injection on truthiness; no empty/low-value lines ever reach the prompt. Clean.
- `pre_execution_diagnosis.py:62-69` `_sort_key` adds `evidence` as final tie-breaker → total order independent of input order. Backed by real order-invariance tests (`test_should_produce_same_order_for_same_kind_different_evidence`).
- `mcp_trino/research.py:1245-1251, 1468-1472` single-fetch metadata reuse is correct: pre-loop `diag_refs` and post-loop `qualified_refs` derive from the identical `_extract_table_names(sql)` + identical `c and s` filter → same refs; metadata is schema/props (stable within a run), so no staleness risk.
- Wiring test (`test_pre_execution_diagnosis_wiring.py`) is NOT a tautology: it patches the real `new_session` call site, captures the actually-assembled `sys_prompt`, and asserts both header presence AND `index(header) < index(SKILL_PROMPT)` ordering on both paths. Remove the injection from production and these fail. Faking `peak_memory_bytes` above 1 GiB to guarantee a `memory-pressure` direction is a sound way to make the assertion deterministic without a live server.
- Injection placement is spirit-identical: both paths put `directions_block` immediately before the skill prompt body (`research.py:1285-1287` MCP, `research.py:808` direct), gated on truthiness. No silent no-op on either path — this is the project's hard dual-path rule, satisfied.
- `_build_mcp_explain_runner` error-swallowing is appropriate: `plan_cost` is explicitly best-effort (`preflight.py:128-138`), and reduce-scan/runtime memory-pressure still fire without it. The swallow does not hide a correctness path.

## Must-fix issues

None. No Critical, zero Important. The change is surgical, both paths wired, tests green and meaningful.

## Parked nits (→ RETRO, non-blocking)

1. `mcp_trino/research.py:595-625` `_build_mcp_explain_runner` — branchy row-shape extraction (dict-values / list-tuple / str) has **no direct unit test**. The wiring test drives it through a MagicMock client (returns None), and the diagnosis unit tests feed pre-built plan dicts, so the helper's own parsing branches are uncovered. Add 3-4 unit tests feeding fake `_execute_via_mcp` results (dict cell, list cell, bare str, error→None). Low risk today (best-effort, None-safe) but it is the only new production helper with branchy logic and zero coverage.
2. `pre_execution_diagnosis.py:185` `_explain_cost_contributor` gates plan-walk on `isinstance(raw_plan_json, dict)`, but `plan_cost` may return a `list` root (`preflight.py:155`). A list-rooted plan silently yields no explain-derived memory-pressure (reduce-scan via `estimate_from_explain` is unaffected, and runtime peak still fires). Trino `EXPLAIN (FORMAT JSON)` root is normally an object, so this is a narrow edge; consider accepting list roots for symmetry with `plan_cost`'s declared return type.
3. `pre_execution_diagnosis.py:202-203` bare `except Exception: pass` around `_max_non_leaf_output_bytes`. The helper is already type-guarded and shouldn't raise; the blanket catch is belt-and-suspenders but slightly over-broad. Cosmetic.
4. MCP path runs `EXPLAIN (FORMAT JSON)` (via `_build_mcp_explain_runner` + `plan_cost`) and separately `EXPLAIN ANALYZE` baseline — two explain round-trips on the default path. Acceptable (different purposes), but worth a one-line comment noting the intentional double-explain so a future reader doesn't "dedupe" them.

## Score rationale

The wiring is correct, symmetric across both production paths, deterministic, and the new pure helper is clean with the empty-string-gates-injection contract doing real work. Test quality is above average for first-version wiring: the dual-path prompt-capture strategy genuinely proves injection rather than asserting on a mock. The single deduction from a 9.5+ is parked-nit #1 — a new branchy production helper (`_build_mcp_explain_runner`) shipping with no direct unit test on its parsing branches. That is a real (if low-severity) coverage gap on a production-path function, so it holds the score at 9.3 rather than higher. It is not severe enough to block: the helper is None-safe, best-effort, and its failure modes degrade gracefully without affecting correctness. Gate (> 9.0) cleared.
