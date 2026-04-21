# CURRENT — v28

## Basic Info

- **Project:** genieCLI
- **Iteration:** 28
- **Status:** PLAN — ack gate OPEN (production path + 3+ files + 1 Open question requires Sam's read)
- **Owner:** Emily (planning + execution); Sam picks direction
- **Started:** 2026-04-20
- **Updated:** 2026-04-21T09:25+0800
- **Focus:** `/trino-research` long-query handling — stop burning N × baseline_time on slow queries by replacing per-iteration execution with EXPLAIN-based plan-cost ranking + tiered correctness guards + upfront cost gate + per-candidate wall-clock kill.
- **Touched features:** [trino-research](features/trino-research.md)

## Goal

- **One-line summary:** Make `/trino-research` usable on long-running queries (baseline ≥ 60s) — from current O(N × baseline) to O(baseline × 2 + K × fallback) without silently losing row-level correctness.
- **Done when:**
  - `/trino-research` on a baseline-1h query does not exceed ~3h total under default settings (1 baseline + 1 final verify + up to 1 fallback verify), down from current ~18h floor.
  - Correctness property preserved: the final "Best SQL" emitted is row-equivalent to the baseline on the real, full query (not sampled), verified by the existing `_results_equivalent` check. If all top candidates fail verify, the tool emits "no verifiable improvement found" rather than a wrong rewrite.
  - Upfront cost gate: baseline wall-time > `--long-query-threshold` (default 60s) aborts with an estimated-total-time message unless `--long-query` is passed.
  - Per-candidate wall-clock kill: each iteration candidate runs under `query_max_run_time = 1.2 × baseline_wall_time` session property; timeout is treated as "not an improvement" and iteration continues without tearing down the loop.
  - `features/trino-research.md` reflects the long-query design + the structural-invariant assumption behind L1 + the reasons L2 was deferred.
  - 641+ tests pass (existing) + new unit tests for `_plan_cost_of`, `_explain_plan_signature`, `_structural_equivalent`, K-retry fallback path, and the upfront-gate/timeout knobs.

## Carryover (from v27)

Max 3 items. v27 produced 2 promotes; both **deferred one-time** here because Sam picked a different direction (long-query handling) via Telegram msgs 818/820/822. One-time defer is permitted by the SKILL spec; a second defer would force park-or-drop tagging.

- ⭐ P1 S — PLAN-time validator dry-run → **deferred one-time to v29** (process change, not blocking v28's product work)
- ⭐ P2 S — Hypothesis prompt structure → **deferred one-time to v29** (brittleness observable only under sustained runs; v28's plan-cost loop may reshape the hypothesis-extraction surface anyway)

## Promote Verification (mandatory first PLAN action)

| From | Item | Outcome | Evidence |
|------|------|---------|----------|
| v27-#change-next-1 | PLAN-time validator dry-run | **deferred-one-time** | Sam explicitly raised long-query concern (Telegram msg 818) and acked the proposed PLAN direction (msg 822); this is process hygiene, not product work — holding until v29 retro. If it defers a second time, must re-tag as park (with concrete trigger) or drop. |
| v27-#change-next-2 | Hypothesis prompt structure | **deferred-one-time** | Same rationale: Sam's current pain is long-query wall-time, not hypothesis extraction fidelity. Additionally, the plan-cost iteration loop in v28 may reshape the inner-loop prompt (since we no longer ship real metrics to the LLM after each iter); re-evaluating hypothesis structure is better done after v28 lands. Same second-defer constraint as above. |

## Active Parks (carried from v27 + 1 new from v27-#failed-2)

5 parks entering v28 DO.

- Ledger roll-over drag — age 2/3 — trigger: a second iteration closes >1 day after final Todo accepted — origin: v26-#failed-2
- Autoresearch product-value signal — age 2/3 — trigger: E2E mode decision lands AND smoke mode picked — origin: v26-#change-next-3
- E2E smoke mode labelling — age 1/3 — trigger: next time `kept=0/2` in an E2E report causes a human to suspect a product regression when it isn't — origin: v26-#change-next-1
- Cron plumbing (`E2E-REPORT.md outside repo` + `HTTP 401`) — age 1/3 — trigger: Sam actually tries to open an auto-generated E2E PR and finds the branch exists but no PR — origin: v26-#change-next-2
- Telegram allowlist plumbing — age 0/3 — trigger: next time Telegram reply errors AND the message was a status-flip ack — origin: v27-#failed-2

## Theme Tracker

| Theme | Appearances | Status |
|-------|-------------|--------|
| UX polish (cards / banners / spinners / syntax highlights / output layout) | v22, v23, v24, v26 (banner), v27 (trino-research output) | long-term — no per-instance lifecycle |
| Trino query-shape economics (cost, rows, bytes, wall-time tradeoffs) | v19 (preflight gate), v25 (EXPLAIN ANALYZE backfill), v28 (plan-cost loop + long-query gate) | **active — v28 is the third entry in 4 iterations; promoting to real theme rather than coincidence** |
| E2E signal hygiene | v27 parks (deferred) | dormant |

## Hardthink — Alternatives considered

### For the CORRECTNESS GUARD (the critical design choice)

This is the one choice Sam needs to ack — it affects engineering scope, correctness risk, and worst-case wall-time.

1. **L1 + L3 + K-retry (three-layer, my recommendation).**
   - **L1 (per-iteration, cheap):** Trino `EXPLAIN (FORMAT JSON)` on baseline + candidate → extract structural signature = {output columns (name + type), aggregation function names, GROUP BY keys, top-level FilterNode predicate set}. Reject candidate if structural signature diverges. Infrastructure partly reused from `genie/skills/mcp_trino/preflight.py:78` (`estimate_from_explain`) and `research.py:1599` (`EXPLAIN (FORMAT JSON)` invocation). Coverage: catches ~80% of semantic drift — dropped columns, swapped aggregate functions, lost filters, different GROUP BY shape. Miss class: semantically-equivalent predicates written differently (e.g. `x BETWEEN 1 AND 10` vs `x >= 1 AND x <= 10`) may trigger false reject; we mitigate by comparing filter predicates as a *soft* signal (log warning, don't reject) while output/agg/GROUP BY are *hard* gates.
   - **L3 (final winner only, expensive):** existing `_measure(..., capture_rows=True)` + `_results_equivalent` full row-level comparison. This is the real correctness guard; L1 is the cheap pre-filter.
   - **K-retry on L3 fail:** if the top-ranked candidate fails L3 row-equivalence, fall back to the next-ranked candidate that passed L1, retry L3. Default K=3. Hard cap to prevent runaway wall-time on pathological cases.
   - **Wall-time profile on 1h baseline:** 1 baseline run (1h) + 5 iter × EXPLAIN (seconds) + 1 final winner verify (1h) + worst-case K=3 fallback verifies (3h) = ~5h worst case, ~2h typical case.
   - **Pros:** cheap iteration, strong correctness net, graceful degradation, explicit "no verifiable improvement" fallback.
   - **Cons:** L1 implementation is non-trivial (structural signature parsing + equivalence logic); Trino EXPLAIN JSON field names need probing (T1); L1 false-positive risk requires careful soft/hard-gate split.

2. **L3-only + K-retry (simpler, my acceptable fallback).**
   - No L1 structural check; iteration purely ranks by plan cost (outputRowCount × outputSizeInBytes from `estimate_from_explain`).
   - Final: same L3 row-equivalence on top winner + K-retry to next-ranked.
   - **Wall-time profile:** 1 baseline + 5 iter × EXPLAIN + 1 final verify + K fallbacks worst case = same ~5h worst, ~2h typical.
   - **Pros:** much simpler code (no structural parser, no equivalence logic, no predicate normalization). Ships faster. Less fragile to Trino EXPLAIN JSON schema changes.
   - **Cons:** a structurally-wrong candidate (LLM drops a column or filter) that happens to have lower plan cost will rank first — forcing L3 verify to reject it, burning 1h on the fallback. On a consistently-hallucinating LLM run, might burn K × 1h on known-bad candidates. L1 would have caught these for free.
   - **Risk bound:** at worst, same total time as Option 1; at best, still better than status quo. Correctness unchanged (L3 is the real gate either way).

3. **L1 + L2 + L3 (tightest net, rejected).**
   - Add L2: sampled row verify — substitute base tables with `TABLESAMPLE BERNOULLI(0.1)` versions, run baseline + candidate on sample, compare rows.
   - **Cons:** requires SQL AST parsing to substitute tables across CTEs, subqueries, and joins. `sqlparse` is a shallow tokenizer, not an AST library; proper substitution requires a Trino-aware parser (we don't have one). Plus TABLESAMPLE doesn't compose cleanly with aggregations (sum/count change under sampling). Engineering cost is 2-3× L1 for marginal gain over L1 + K-retry.
   - **Reject.** Revisit only if L1 + K-retry shows a "structural-match-but-data-different" failure class in production that K-retry doesn't surface quickly enough.

4. **No correctness check at all — trust LLM, emit winning SQL blind.**
   - **Reject** — produces silently wrong SQL. Regression from current state. Not acceptable for production.

**Recommendation: Option 1 (L1 + L3 + K=3).** Option 2 (L3-only + K=3) is the acceptable fallback if Sam wants to ship faster; correctness properties are identical (L3 is the gate), only wall-time profile on hallucinated runs differs.

### For the ITERATION RANKING METRIC

1. **Existing `estimate_from_explain` output (outputRowCount + outputSizeInBytes) — recommendation.**
   - Already parsed; cheap. Lower product of estimates = lower cost. Reuses `preflight.py:78` code.
   - Cons: rough proxy; doesn't reward partition pruning visibility or join algorithm choice directly.

2. **Parse EXPLAIN's textual "TOTAL COST" scalar.**
   - Cons: text parsing is more fragile than JSON; field format varies by Trino version. JSON-based rows/bytes estimate is the same underlying signal.

3. **Real wall-time from a TABLESAMPLE-sized execution per candidate.**
   - Cons: defeats the purpose (we're trying to avoid execution). Also TABLESAMPLE reshape problem.

**Recommendation: Option 1.**

### For the UPFRONT COST GATE

1. **Hard threshold + `--long-query` opt-in — recommendation.**
   - If baseline measured wall-time > `--long-query-threshold` (default 60s) AND `--long-query` not passed → abort with "baseline=Xs; predicted total=Ys; pass --long-query to proceed". Matches Sam's original pain literally.
   - Threshold configurable via `--long-query-threshold=<seconds>`.

2. **Soft warning (print predicted total, proceed anyway).**
   - Cons: doesn't solve "accidentally burned a night" which is the whole motivation.

3. **No gate.**
   - Reject — reopens the original pain.

**Recommendation: Option 1.**

### For PER-CANDIDATE WALL-CLOCK KILL

1. **Trino session property `query_max_run_time = 1.2 × baseline_wall_ms` — recommendation.**
   - Native Trino mechanism; throws cleanly; already-supported across Trino 467+. Set once at session start, applies to every subsequent candidate query.
   - The `1.2×` margin: a candidate taking >20% longer than baseline is definitionally not an improvement; killing it early loses nothing.

2. **Client-side query cancel via query ID lookup.**
   - Cons: more code (query-id tracking, cancel RPC); duplicates server-side mechanism we already have.

3. **No timeout.**
   - Reject — runaway candidate could burn hours before failing naturally.

**Recommendation: Option 1.**

## Hardthink — Scope

### In

- `genie/skills/trino_query/research.py` — rewriting `_run_optimization_loop`:
  - New helper `_plan_cost(sql, explain_runner) -> (rows_estimate, bytes_estimate, raw_plan_json)` reusing `preflight.estimate_from_explain`.
  - New helper `_explain_plan_signature(raw_plan_json) -> PlanSignature` dataclass with `{output_columns: list[(name, type)], agg_functions: tuple[str, ...], group_by_keys: tuple[str, ...], filter_predicates: tuple[str, ...]}`. Walks the JSON tree; extracts from `AggregateNode`, `FilterNode`, and root output schema nodes.
  - New helper `_structural_equivalent(baseline_sig, candidate_sig) -> (ok: bool, reason: str)` — HARD gates: output columns, agg_functions, group_by_keys must match exactly (order-independent for output, exact tuple for agg/group-by). SOFT check: filter_predicates logged as warning if divergent, does not reject.
  - Iteration loop: replace per-iter `_measure(..., capture_rows=True)` with `_plan_cost(...)` + L1 `_structural_equivalent(baseline_sig, candidate_sig)`. History entry status extended: `plan_cost_better`, `plan_cost_worse`, `structural_reject` (new), plus existing `no_sql`, `lint_failed`.
  - After loop: re-rank all non-rejected candidates by plan cost ascending; for the top candidate run full `_measure(..., capture_rows=True)` + `_results_equivalent`. On L3 pass → emit as winner. On L3 fail → take next candidate, up to K=3 retries; emit `no_verifiable_improvement` status if all fail.
  - Upfront gate: measure baseline wall-time with existing `_measure`; if `baseline.wall_time_ms > long_query_threshold_ms AND not long_query_opt_in`, abort with predicted-total-time message derived from max_iterations × 1.2 × baseline + final verify cost.
  - Session property: emit `SET SESSION query_max_run_time = '<N>ms'` before iteration where N = 1.2 × baseline_wall_ms.
- `genie/skills/trino_query/__init__.py` (or wherever the slash command flags are parsed): add `--long-query` (bool), `--long-query-threshold` (int seconds, default 60), `--max-fallbacks` (int, default 3).
- `tests/test_mcp_research.py` — new tests: `test_plan_cost_extracts_rows_and_bytes`, `test_explain_plan_signature_extracts_output_columns_agg_and_group_by`, `test_structural_equivalent_rejects_mismatched_agg_functions`, `test_structural_equivalent_accepts_reordered_output_columns`, `test_iteration_loop_skips_execution_for_structural_reject`, `test_k_retry_falls_back_on_l3_failure`, `test_upfront_gate_aborts_when_baseline_exceeds_threshold`, `test_upfront_gate_skipped_when_long_query_opt_in`.
- `features/trino-research.md` — Design log v28 entry (full rationale + alternatives considered + L1 soft/hard split) + Limits entry (L2 deferred, with condition to revisit) + Iteration touchpoint + Current capability snapshot update (flag list + behavior).

### Out (explicitly deferred)

- **L2 sampled row verify** — deferred to dedicated iteration if L1 + K-retry shows a real miss class. Engineering cost is high (SQL AST table substitution) and not justified without evidence.
- **Parallel candidate evaluation** — not in scope; doesn't solve the fundamental "each candidate still needs 1h on real data".
- **LLM hypothesis prompt restructure** — v27 carryover, deferred one-time (see Promote Verification).
- **PLAN-time validator dry-run** — v27 carryover, deferred one-time.
- **Interactive `genie chat` vs non-interactive `--sql-file` divergence** — both continue to behave identically; no mode-specific branching.
- **Machine sink (JSON output) changes** — untouched; existing shape preserved with new status values documented.
- **Cross-connector (non-Trino) application** — /trino-research only; MCP connectors for other engines out of scope.

## Hardthink — Open questions

**One — Sam's ack needed on this.**

1. **Correctness tier: Option 1 (L1 + L3 + K-retry) vs Option 2 (L3-only + K-retry)?**
   - Option 1 adds ~200-300 lines of plan-signature + structural-equivalence code and two new test files' worth of unit coverage; catches ~80% of LLM hallucinations at iter-time for free.
   - Option 2 ships ~1/3 the code; same final correctness (L3 is the gate); worst-case wall-time identical; typical-case wall-time slightly worse (more L3 fallbacks on hallucinating runs).
   - My recommendation: Option 1. Willing to drop to Option 2 if you want a faster-to-ship v28 and accept "hallucination tax" on wall-time.

All other design choices (ranking metric = plan cost, gate mechanism = `--long-query` flag, timeout = Trino session property, K default = 3, threshold default = 60s, thresholds tunable via flags) are stated in the Alternatives above; I'm picking the recommendation unless you flag one.

## Todo

| ID | Status | Pri | Task | Feature | Tool | Verify | Note |
|----|--------|-----|------|---------|------|--------|------|
| T1 | blocked | P0 | Probe Trino `EXPLAIN (FORMAT JSON)` output on a representative query — pin down node types (`AggregateNode`, `FilterNode`, `OutputNode`), field names for aggregation function signatures and GROUP BY keys, and confirm `query_max_run_time` session property behavior on Trino 467. Capture 2-3 sample JSON plans for unit-test fixtures. | trino-research | Bash + mcp trino tool | Sample plans saved to `tests/fixtures/explain_plans/`; 3 field names documented: agg functions path, group-by keys path, filter predicate path. Confirm `query_max_run_time` throws QUERY_EXCEEDED_TIME_LIMIT or similar. | Blocks T3 (L1 structural signature extraction depends on the actual field names). Only relevant under Option 1. If Option 2 picked, T1 is just the `query_max_run_time` probe. |
| T2 | pending | P0 | `_plan_cost` helper — reuse `preflight.estimate_from_explain`; returns `(rows_est, bytes_est, raw_plan_json)`. Unit tests for parse success, missing fields, malformed JSON. | trino-research | Edit + pytest | 3+ unit tests pass: happy path returns both estimates; missing `estimates` returns `(None, None, raw)`; malformed JSON returns `(None, None, None)` without raising. | Small scope; independent of Option 1 vs 2. |
| T3 | pending | P0 | `_explain_plan_signature` + `_structural_equivalent` — walks plan JSON, extracts output columns / agg functions / group-by keys / filter predicates into a frozen dataclass. Equivalence: hard on first three, soft (log-warn) on fourth. Unit tests use T1 fixtures. | trino-research | Edit + pytest | 6+ unit tests pass: signature extraction for simple SELECT / agg / join / CTE; equivalence accepts reordered output columns; rejects changed agg function; rejects changed group-by; accepts semantically-equivalent filter rewrite (via warn-not-reject). | **Only under Option 1.** If Sam picks Option 2 at ack, T3 drops entirely. |
| T4 | pending | P0 | Rewrite `_run_optimization_loop`: replace per-iter `_measure(capture_rows=True)` with `_plan_cost` + (Option 1: L1 structural check) + new history statuses. After loop: re-rank, pick top candidate, full `_measure` + `_results_equivalent`, K-retry to next-ranked on fail, emit `no_verifiable_improvement` if all fail. | trino-research | Edit + pytest | Existing 641 tests still pass. New tests cover: skip-execution-on-L1-reject (Option 1 only), K-retry fallback on L3 fail, `no_verifiable_improvement` when all candidates fail. | Largest code change; depends on T2 (+ T3 under Option 1). |
| T5 | pending | P0 | Upfront cost gate + per-candidate wall-clock kill: emit `SET SESSION query_max_run_time` before iteration; add `--long-query`, `--long-query-threshold`, `--max-fallbacks` flags; abort with predicted-total-time message when baseline exceeds threshold without opt-in. | trino-research | Edit + pytest | Unit tests: gate aborts with correct message when baseline > threshold and no opt-in; gate skipped when opt-in is true; session property emitted with correct value (1.2 × baseline_ms). | Small; depends on T4 for history-status integration. |
| T6 | pending | P1 | `features/trino-research.md` — Design log (v28 full rationale + alternatives + L1 soft/hard split), Limits (L2 deferred with revisit condition), Iteration touchpoint, Current capability update. | trino-research | Edit | Re-read confirms Design log v28 entry, Limits entry on L2, Iteration touchpoint for v28, Current capability flags listed. | Doc-track VERIFY enforcement (SKILL.md rule 10). |
| T7 | pending | P1 | Smoke run — dry-run the iteration loop end-to-end against localhost:8811 or Sam's Trino with `SELECT 1` (confirms no regression) + one `EXPLAIN (FORMAT JSON)`-returning query (confirms L1 happy path). Not a long-query test. | trino-research | Bash + mcp trino tool | Terminal output shows new status values (`plan_cost_better` etc.), final report renders via v27 layout, no exceptions. | Real long-query verification (baseline ≥ 1h) deferred to whenever Sam has such a query to throw at it; not a blocker for v28 merge. |

## Reports

_(populated as Todo items complete)_

## Blocked

- **T1** blocks on a Trino environment handy for EXPLAIN probing — localhost:8811 + Trino 467 (same env used in v25) or Sam's remote TSMC cluster. Non-blocking if localhost:8811 is up; expected to take <10 min.

## Retro

_(populated at end of v28)_

### Worked

_(tbd)_

### Failed

_(tbd)_

### Change next

_(tbd)_

### Duplicate check

_(tbd — grep against `archive/v1..v27` and this file before each Change-next item is finalized)_

### Park aging pass

_(tbd — 5 parks entering v28; no at-cap items)_

## Process gap

_(populated at end of v28)_

## Do differently next time

_(populated at end of v28)_

### Next-round Focus (preview)

_(populated at end of v28)_

## Roll-over Checklist

- [x] Promote Verification table filled (v27 promotes deferred-one-time with rationale)
- [ ] All Failed/Change-next items tagged
- [ ] Promote count ≤ 3
- [ ] `features/trino-research.md` updated with v28 Design log + Limits + Iteration touchpoint (T6 enforces)
- [ ] Park aging applied (5 parks entering; no at-cap items)
- [ ] Theme Tracker — "Trino query-shape economics" row bumped with v28 entry
- [ ] Move this file to `archive/v28.md`
- [ ] Create new `CURRENT.md` with Carryover from v28 promotes (v27 two deferred items re-enter as second-chance items — must be tagged if deferred again)
- [ ] Update `STATUS.md`: Active Iteration → v29, Next Iteration Focus, refreshed Parks, Feature Index
- [ ] Run `scripts/validate_ledger.py` manually (PLAN-time dry-run: v27 carryover — though not yet adopted, apply it informally to catch column/tag format issues before committing this PLAN)
- [ ] No meta-retro due (next at v30; v28 is round 4 of post-spec run)

## Archive / Handoff

- When archived, this file becomes `archive/v28.md` (read-only).
- STATUS.md is the single entrypoint going forward.
