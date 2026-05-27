# Usage Brief — T1 pre_execution_diagnosis

**Todo:** T1 | **Phase:** DO / Step 5 (Usage Validate) | **Date:** 2026-05-27
**Fit verdict:** FIT

## User Stories

1. **MCP optimizer loop (T2 caller)** — wants ranked `OptimizationDirection[]` from already-computed data before iter 1, to inject concrete directions into the LLM prompt instead of blind guessing.
2. **Long-query path (T3 caller)** — wants directions from EXPLAIN-cost + static + metadata with NO `peak_memory_bytes` (no run happened), to emit a directed zero-cost report instead of a bare abort.
3. **`--direct` path (T4 caller)** — wants the identical function + contract so dual-path symmetry holds without duplicated logic.

## Acceptance Criteria

| # | Given | When | Then |
|---|-------|------|------|
| AC1 | SQL with a high-severity static finding | called with that `static_report` | a `high`-severity direction, `evidence` starts `static:`, ranks first |
| AC2 | `explain_cost` w/ large non-leaf `outputSizeInBytes`, `peak_memory_bytes=None` | called | ≥1 `memory-pressure` direction (`target_metric="peak_memory_bytes"`), `evidence` starts `explain:` |
| AC3 | all four inputs `None` | called | returns `[]`, no raise |
| AC4 | identical inputs, two calls | called twice | lists equal element-for-element (deterministic) |
| AC5 | `table_metadata` with partition props unused in predicate | called | `leverage-partitioning` direction emitted |

## Fit analysis

The single signature serves all three callers. The `peak_memory_bytes`-optional design is what makes the T3 long-query case work: no post-run metric exists there, yet AC2 guarantees a memory direction still surfaces via the `outputSizeInBytes` plan proxy. Determinism (AC4) is required because T4's dual-path symmetry test asserts both paths inject the *same* directions for the same query — non-deterministic ordering would make that test flaky.

**No Spec change required.** Proceed to Tkt.

## Challenge / edge cases raised

- What if a static finding AND an explain memory signal target the same fix? → both emit; ranking de-dup is NOT required for T1 (LLM tolerates overlap), but `kind` + `evidence` differ so they're distinguishable. Dedup deferred (YAGNI) unless T2 prompt noise proves it needed.
- Long-query path passes `peak_memory_bytes=None` AND may lack `table_metadata` (unqualified SQL) → still must yield ≥1 direction from static+explain alone. Covered by AC2.
