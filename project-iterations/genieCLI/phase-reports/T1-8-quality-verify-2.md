# Quality Verification Report (round 2) — T1 pre_execution_diagnosis

**Verdict**: APPROVED
**Quality score**: 9.4 / 10
**>9.0 gate**: CLEARED
**Context Packet**: T1-8B re-verify (was APPROVED_WITH_NITS 8.8)

## Test numbers actually run
- `python -m pytest tests/test_pre_execution_diagnosis.py -q` → **31 passed in 0.09s**
- `python -m pytest -q` → **755 passed, 10 skipped in 0.96s**

## Must-fixes — verified against code

### Fix 1 — partition detection mirrors production (VERIFIED)
- Production truth re-read: `research.py:231-232`
  - `partitioning = meta.properties.get("partitioning", "")`
  - `if not partitioning or partitioning == "[]":` → table is NOT partitioned.
- Module `_partition_spec` (`pre_execution_diagnosis.py:216-228`): reads `partitioning` first,
  `partitioned_by` Hive fallback (line 223), strips+lowercases, rejects via
  `_EMPTY_PARTITION_VALUES = frozenset({"", "[]", "null", "none"})` (line 213).
- Old bug (`partitioned_by` + loose `any("partition" in k)`, value-ignored) is gone.
  Caller now gates on `_partition_spec` truthiness (line 260-261).
- Semantic check: module's empty-set is a **superset** of production's two sentinels — strictly
  stricter, never looser. So no false positive that production would suppress, and a real Iceberg
  spec (`["day(event_time)"]`) still emits. Faithful mirror.

### Fix 2 — magic multiplier named (VERIFIED)
- `HIGH_SEVERITY_SCAN_MULTIPLIER: int = 10` (`pre_execution_diagnosis.py:27`), consumed at line 168.
  Bare `10 *` is gone. Co-located with `LARGE_SCAN_BYTES` / `HIGH_PEAK_MEMORY_BYTES` as SSoT.

### Fix 3 — tests assert the REAL key (VERIFIED)
- `partitioning` populated → emits: test line 319-329 (`"[\"day(event_time)\"]"`).
- `partitioning == "[]"` → no emit: line 332-339.
- `partitioning == ""` → no emit: line 342-348.
- Hive `partitioned_by` fallback still covered: line 309-316. Fabricated-key assertions removed.

## Adversarial cross-check — residual divergence?
- Production reads property values that are always strings (`research.py:211` `property_value`).
  Module still guards non-str input with `str(raw)` (line 224-225) — defensive, harmless, no false
  result on real metadata.
- Production's unpartitioned branch additionally inspects date columns to *suggest* partitioning;
  the T1 module deliberately only flags **leverage** of an existing partition. Different intent,
  not a divergence in the partitioned/unpartitioned decision. Correct scoping.
- No remaining empty-sentinel or type path found that would yield a false positive/negative on real
  Trino metadata.

## Residual nits
- [Minor] `_partition_spec` typed `dict[str, str]` but defensively stringifies non-str values
  (line 224-225). The annotation slightly under-sells the runtime contract; either tighten the type
  or drop the guard. Non-blocking, park for RETRO.
- [Minor] `_static_contributor` hardcodes `target_metric="wall_time_ms"` for every rule regardless of
  rule semantics (e.g. `predicate-pushdown` is really a scan-bytes win). Pre-existing, out of this
  fix scope; note only.

## Assessment
All three flagged must-fixes are correctly applied and the partition logic now faithfully mirrors
the production source of truth (research.py:231-232), in fact strictly stricter. Pure, deterministic,
never-raises contract holds; full suite green (755 passed). Only two cosmetic Minor nits remain.
Merge recommended.

## T2 wiring statement
**T2 can safely wire this into the live LLM optimizer prompt.** The false-positive `leverage-partitioning`
that previously fired on every unpartitioned table is eliminated and regression-locked by tests
asserting `""` and `"[]"` produce no direction. Output is total-ordered and reproducible, so T3
(zero-cost report) and T4 (--direct parity) consume a stable contract.

## Top items to fix first
1. (RETRO) Tighten `_partition_spec` type annotation or drop the str() guard.
2. (note) Per-rule `target_metric` in `_static_contributor` is a pre-existing simplification.
