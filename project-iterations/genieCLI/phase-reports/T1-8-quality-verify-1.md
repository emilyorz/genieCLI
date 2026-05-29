# Quality Verification Report - T1

**Verdict**: APPROVED_WITH_NITS
**Quality score**: 8.8/10
**Context Packet**: T1 pre_execution_diagnosis quality verify

## Strengths

- `pre_execution_diagnosis.py:31-38` - `_SOURCE_RANK`/`_SEVERITY_RANK` named maps + `_sort_key` 4-tuple `(severity, source, kind, evidence)` give total order independent of input order. Determinism is structural, not incidental — tested at test:245-284. This is the contract guarantee T2/T3/T4 lean on.
- `pre_execution_diagnosis.py:25-26` - thresholds module-level, named, documented with units, exported in `__all__`. Easy to tune. Matches spec §6.
- `pre_execution_diagnosis.py:87-116` - each contributor is a small pure function with a single None/empty guard at the top; never raises. `_explain_cost_contributor` even wraps the plan walk in try/except (line 197). Failure-mode invariant (spec §5) honored.
- `target_metric` values are real metric fields: `wall_time_ms` + `physical_input_bytes` ∈ `MCP_METRICS` (research.py:1462); all three ∈ `QueryMetrics` (--direct, **init**.py:26-40). Renders cleanly into the optimizer prompt context (research.py:1235-1244).
- Tests decouple via `SimpleNamespace` matching real attr shapes, cover failure modes (malformed tuple, non-tuple, deeply-nested garbage plan test:421-437), boundary (at-threshold not-over test:407), and order-independence. Genuinely meaningful, not happy-path-only.

## Issues

- [Important] `pre_execution_diagnosis.py:235-241` - **partition false-positive vs real Trino property semantics.** Real `system.metadata.table_properties` exposes the key `partitioning` with value `"[]"` when the table is NOT partitioned — production `_generate_table_suggestions` (research.py:231-232) explicitly treats `partitioning` empty or `"[]"` as no-partition. This module only checks key _presence_ via `any("partition" in k for k in properties)` and never inspects the value, so a non-partitioned table whose properties contain `partitioning="[]"` emits a bogus `leverage-partitioning` direction. WHY it matters: feeds the LLM prompt a wrong optimization hint on every unpartitioned table that still reports the property → noise on the hot path. Fix direction: mirror production — treat empty/`"[]"`/`"[ ]"` value as absent before emitting; check value not just key.
- [Important] `pre_execution_diagnosis.py:219` - **sort key match is half-right, partition key match is accidental.** Sort reads `sorted_by`/`sort_order` which exactly match production (research.py:323) ✓. But partition emission only works because `"partition" in "partitioning"` substring-matches by luck (test:309-310 uses `partitioned_by`, test:318 uses `partition_columns` — neither is the real key `partitioning`). The tests never assert against the actual production key `partitioning`, so the duck-typed contract is unverified for the one key the MCP path really produces. WHY it matters: this is the exact "silent no-op / silent false-positive in production" risk the contract review flags as highest value. Fix direction: add a test using `properties={"partitioning": "[]"}` (expect NO direction) and `properties={"partitioning": "['dt']"}` (expect direction), and make the contributor key-aware.
- [Minor] `pre_execution_diagnosis.py:165` - severity escalation `bytes_est > 10 * LARGE_SCAN_BYTES` uses a bare `10` magic multiplier inline. WHY: the two named thresholds set the documentation bar; this third knob is undocumented and unnamed. Fix: name it (e.g. `HIGH_SEVERITY_SCAN_MULTIPLIER = 10`) or comment the 10 GiB intent.
- [Minor] `pre_execution_diagnosis.py:291` - `sql` param is accepted but unused (docstring at 303-305 acknowledges it as future-proofing). Codebase rule is "no abstraction beyond the task requires." It is part of the spec'd public contract (spec §2) so keep it, but it is dead weight today. No action required — flagged for awareness.
- [Minor] `pre_execution_diagnosis.py:179-198` vs `263-283` - both explain-plan and runtime paths emit `kind="memory-pressure"` with `target_metric="peak_memory_bytes"`, differing only by evidence prefix. When both fire (plan signals build-side AND runtime peak over threshold) the consumer prompt gets two near-duplicate directions. Not wrong (evidence distinguishes them, sort is stable) but T2 may want dedup. Fix direction: leave as-is for T1; note for T2 prompt assembly.

## Assessment

Clean, well-structured, deterministic pure module that fits the consumer contract (target_metric fields are real, dataclass renders cleanly into the optimizer prompt). The one real maintainability/correctness gap is the table-metadata contributor: it matches the sort key exactly but the partition path only works by substring luck and ignores Trino's `"[]"`-means-empty semantics, producing a false-positive direction and leaving the real production key (`partitioning`) untested. Both are Important, neither is Critical (pure module, no execution, fails safe, doesn't break the build). Mergeable as the T1 contract once the metadata semantics are tightened; partition fix is cheap and should land before T2 wires this into the live prompt, otherwise T2 inherits the noise.

## Top items to fix first

1. Make `_metadata_contributor` value-aware for partitioning: treat empty / `"[]"` as not-partitioned (mirror research.py:231-232), so unpartitioned tables stop emitting `leverage-partitioning`.
2. Add tests against the REAL production property key `partitioning` (both `"[]"` → no direction and a populated value → direction); current tests only use `partitioned_by`/`partition_columns` which never appear in production.
3. Name the `10 *` severity multiplier at line 165 (single-source-of-truth like the other two thresholds).

## Observations (unrelated, do not fix here)

- `MCP_METRICS` (research.py:1462) does not include `peak_memory_bytes`; the module already targets it. This is explicitly T2's wiring job (spec §8, explore report Q3) — not a T1 defect.
- `--direct` `QueryMetrics` carries `spilled_bytes` (**init**.py:33) which the MCP path lacks entirely. T4 dual-path parity will need to decide whether the shared module ever consumes spill; out of scope for T1.
