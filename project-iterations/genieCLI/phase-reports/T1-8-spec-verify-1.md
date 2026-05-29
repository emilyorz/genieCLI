# Spec Verification Report - T1

**Verdict**: SPEC_COMPLIANT
**Context Packet**: T1 / v29 / genieCLI
**Mode**: v2-sdd (strict-full-9-step)

---

## Verify re-run

**Command/check (new file)**:

```
cd /Users/leeabc/work/emilyorz/genieCLI && python -m pytest tests/test_pre_execution_diagnosis.py -v
```

**Result**: PASS
**Output excerpt**: `29 passed` — all 29 tests collected and passed.

**Command/check (full suite)**:

```
python -m pytest -q
```

**Result**: PASS
**Output excerpt**: `753 passed, 10 skipped in 0.93s`

Claimed count was 753 pass + 10 skip. Observed count is **753 pass + 10 skip**. Claim verified.
Baseline was 724 + 10 → delta = +29 new tests, zero regression.

---

## Files actually changed

- `genie/skills/mcp_trino/pre_execution_diagnosis.py` (338 lines, new file)
- `tests/test_pre_execution_diagnosis.py` (438 lines, new file)

No other files changed. Matches Tkt `Out:` exactly.

---

## Requirement mapping

| Requirement                                                                                                       | Verdict | Evidence                                                                                                                                     |
| ----------------------------------------------------------------------------------------------------------------- | ------- | -------------------------------------------------------------------------------------------------------------------------------------------- |
| `OptimizationDirection` is a frozen dataclass                                                                     | pass    | `@dataclass(frozen=True)` at line 45                                                                                                         |
| Fields: `kind / severity / rationale / evidence / target_metric` (all `str`)                                      | pass    | lines 49–53                                                                                                                                  |
| Public function `pre_execution_diagnosis(sql, *, static_report, explain_cost, table_metadata, peak_memory_bytes)` | pass    | lines 291–329                                                                                                                                |
| Contributor 1 — static: one direction per finding, severity passthrough, `evidence=static:{rule_id}@L{line}`      | pass    | lines 87–116; `test_should_emit_one_direction_per_static_finding`                                                                            |
| Contributor 2 — explain-cost: `reduce-scan` if bytes_est over threshold                                           | pass    | lines 163–177; `test_should_emit_reduce_scan_when_bytes_est_over_threshold`                                                                  |
| Contributor 2 — explain-cost: recursive walk → max non-leaf outputSizeInBytes → `memory-pressure`                 | pass    | lines 124–198; `test_should_emit_memory_pressure_from_explain_plan_when_peak_memory_none`                                                    |
| Contributor 3 — metadata: `leverage-partitioning` / `leverage-sort` when props present                            | pass    | lines 208–255; `test_should_emit_leverage_partitioning_when_partitioned_by_present`, `test_should_emit_leverage_sort_when_sorted_by_present` |
| Contributor 4 — memory/runtime: `peak_memory_bytes` over threshold → memory-pressure direction                    | pass    | lines 263–283; `test_should_emit_memory_pressure_from_runtime_when_peak_over_threshold`                                                      |
| Module-level named thresholds: `LARGE_SCAN_BYTES`, `HIGH_PEAK_MEMORY_BYTES` (1 GiB each, documented)              | pass    | lines 25–26                                                                                                                                  |
| Deterministic ranking by `(severity_rank, source_rank, kind)`                                                     | pass    | `_sort_key` at lines 61–68; extended to 4-tuple `(severity_rank, source_rank, kind, evidence)` — see note below                              |
| PURE — no import of research.py, no I/O, no network                                                               | pass    | only imports: `__future__`, `dataclasses`, `typing` — grep confirms zero research.py import                                                  |
| Total-over-partial-inputs — any/all None → no raise                                                               | pass    | `test_should_not_raise_when_single_arg_is_none[kwargs0-3]` (parametrized × 4) + `test_should_return_empty_list_when_all_inputs_are_none`     |
| All-None → `[]`                                                                                                   | pass    | `test_should_return_empty_list_when_all_inputs_are_none`                                                                                     |
| Leaf module — no top-level import of research.py (`TYPE_CHECKING` guard OK)                                       | pass    | top-level imports are only stdlib; `TYPE_CHECKING` guard present                                                                             |
| `static_report.parse_error` set → static contributor returns `[]`                                                 | pass    | lines 91–92; `test_should_contribute_nothing_when_static_report_has_parse_error`                                                             |
| Malformed/empty `raw_plan_json` → no raise                                                                        | pass    | `except Exception: pass` at line 197; `test_should_not_raise_on_malformed_explain_cost_tuple` + `_non_tuple` + `_deeply_nested_garbage`      |

---

## Spec deviation notes

### 1. Source-rank label: `"runtime"` vs `"memory"` (cosmetic, consistent, non-breaking)

Spec §4 Invariant 2 documents `source_rank{static:0,explain:1,memory:2,metadata:3}`.

Implementation uses `"runtime": 2` in `_SOURCE_RANK` and emits evidence prefix `runtime:` from contributor 4. The label was renamed during Dev from `memory` → `runtime` to be more precise (contributor 4 fires only on POST-run `peak_memory_bytes`, not on the plan-estimated memory-pressure emitted by contributor 2).

Effect on sort order: NONE. The rank value (2) is unchanged. Contributor 4 always emits `runtime:` evidence; contributor 2 emits `explain:` evidence. No direction is ever emitted with prefix `memory:`, so the old label would have resolved to the default rank (9) anyway. The rename is internally consistent and correct; the spec vocabulary is outdated by the Dev rename.

Severity: cosmetic / documentation drift. Not a behavior defect. Quality-verifier should decide if the spec should be back-updated.

### 2. Sort key 4-tuple vs spec 3-tuple (strict improvement, self-documented)

Spec documents sort key as `(severity_rank, source_rank, kind)` — 3 fields.

Implementation uses `(severity_rank, source_rank, kind, evidence)` — 4 fields.

The 4th field `evidence` was added post-Dev by the orchestrator (per Step 7 Dev notes) to close a tie-breaking gap: two directions from the same contributor with identical kind (e.g., two `leverage-partitioning` directions for two different tables) would have been insertion-order-dependent on the 3-tuple alone. Adding `evidence` makes the sort a strict total order.

The 4th field does not violate the spec's ranking intent (same-tier items still sort in the same relative order as 3-tuple would, with ties resolved deterministically by evidence). Test `test_should_produce_same_order_for_same_kind_different_evidence` explicitly covers this case. Self-documented in Step 7 Dev notes.

Severity: benign improvement that strengthens the determinism invariant. Not a regression.

---

## Extra work check

None. Exactly two files created (`pre_execution_diagnosis.py` + `tests/test_pre_execution_diagnosis.py`), matching Tkt `Out:` verbatim. No refactoring of existing files, no extra dependencies added.

---

## Ledger updates

- `STATUS.md`: not applicable — T1 is an intermediate Todo; STATUS.md is updated at Wrap (T4 / Step 9). Current STATUS.md correctly reflects v28 state. No T1-specific update required or expected.
- `features/trino-research.md`: not applicable for T1. Tkt `Out:` for T1 does not include feature doc update. Feature doc is assigned to T4 (v29 design log + C2 fold-in).

---

## Usage compliance

**AC1** — high-severity static finding, `evidence` starts `static:`, ranks first:

- `test_should_rank_high_severity_static_finding_first`: PASS.
- Caveat: the test calls with only `static_report` set (no other contributors), so "ranks first" reduces to "is first element of a 1-element list." It does not prove static high-severity beats a same-severity explain direction. This is a test-coverage gap, not a correctness bug — `_SOURCE_RANK` statically enforces `static:0 < explain:1`, making cross-source static priority structurally guaranteed.

**AC2** — `explain_cost` with large non-leaf outputSizeInBytes, `peak_memory_bytes=None` → `memory-pressure` direction with `evidence` starting `explain:`:

- `test_should_emit_memory_pressure_from_explain_plan_when_peak_memory_none`: PASS. Uses `(None, None, plan)` with `peak_memory_bytes=None`. Asserts `evidence.startswith("explain:")` and `target_metric="peak_memory_bytes"`. LOAD-BEARING — verified substantively, not vacuously.

**AC3** — all four inputs None → `[]`, no raise:

- `test_should_return_empty_list_when_all_inputs_are_none`: PASS. Asserts exact `result == []`.

**AC4** — determinism / input-order-invariance:

- `test_should_produce_identical_output_on_repeated_calls`: PASS — same input, two calls, asserts `result_a == result_b`.
- `test_should_produce_same_ranking_regardless_of_findings_input_order`: PASS — shuffled input list, same output.
- `test_should_produce_same_ranking_regardless_of_table_metadata_order`: PASS — two tables swapped, same output.
- `test_should_produce_same_order_for_same_kind_different_evidence`: PASS — closes the tie-breaking edge case the 3-tuple spec would have left nondeterministic. LOAD-BEARING — scrutinized: the fixture creates two `leverage-partitioning` directions for `alpha` and `beta`; the evidence strings differ (`metadata:alpha partitioned_by=dt` vs `metadata:beta partitioned_by=region`); lexicographic sort on evidence is deterministic regardless of input order. Assertion is non-vacuous.

**AC5** — `table_metadata` with partition props → `leverage-partitioning`:

- `test_should_emit_leverage_partitioning_when_partitioned_by_present`: PASS. Asserts `"leverage-partitioning" in kinds`.

---

## Issues

- **low / documentation drift** — `source_rank` label `memory:2` in spec → `runtime:2` in code. Spec vocabulary is stale. Behavior is correct. Recommend updating spec/CURRENT.md at Wrap to reflect `runtime` label.
- **low / test coverage** — AC1 "ranks first" test does not exercise cross-source ranking (static vs explain at same severity). Structurally guaranteed by `_SOURCE_RANK` constants but not tested end-to-end. Worth noting for quality-verifier judgment.

---

## Recommendation

pass to quality-verifier
