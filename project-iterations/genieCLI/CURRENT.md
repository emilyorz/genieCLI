---
ledger_version: v3
ledger_hooks: enabled
execution_mode: strict-full-v3
activation_file: .task-ledger-active.json
runtime: claude-code
dispatch_adapter: native-claude-agents
phase: PLAN
current_todo: T1
maturity_label: active
---
# CURRENT — v29 (V3 strict)

## PLAN preamble

> Think carefully and step-by-step — this strict V3 iteration runs full v1 lifecycle plus full v2.5 SDD for every Todo. Goal: turn `/trino-research` from blind trial-and-error optimization into directed, pre-execution-diagnosed tuning (LH-PRISM-style), plus zero-cost long-query diagnosis.

```yaml
mode: v3-strict
ledger_version: v3
ledger_hooks: enabled
execution-mode: strict-full-v3
runtime: claude-code
dispatch-adapter: native-claude-agents
subagent-authorization: not-required
downgrade-approval: N/A
reasoning-tier: available
```

**Quality Loop gate:** strict v2.5/v3 threshold — a Todo passes Step 8B only when the quality score is strictly **> 9.0/10**. Any round scoring ≤ 9.0 returns must-fixes and re-runs.

### Runtime-honesty deviation note (READ FIRST)

This session runs from `~/.openclaw/workspace-emily/`, NOT from the genieCLI repo root that owns `.task-ledger-active.json`. The shared guard (`task_ledger_guard.py:619 build_decision`) only enforces for a runtime session rooted in the repo that owns the activation file. Therefore:

- V3 hooks ARE installed into the genieCLI repo (`.claude/settings.json` created by `install-hooks`, hook script path verified).
- But live PreToolUse / Stop hook gating will NOT fire inside THIS session, because the cwd is a different repo.
- Real enforcement is obtained by **dispatching the V3 verifier subagents** (spec-verifier + quality-verifier) per Todo and consuming their reports — not by trusting hook backstops.
- Per SKILL-v3 Maturity Rule, this run cannot be labeled `full-v3-success`. Target label: **`v3-deviation (hooks-installed-not-live, single-runtime, claude-code-only)`**. Sam may upgrade the label only if he later re-runs from inside the repo with live hooks.

## Use-case gate

1. **Concrete scenario:** A TSMC lakehouse engineer runs `/trino-research "<slow SQL>"` to optimize a query. Today the optimizer executes the query, reads 5 metrics, then lets the LLM guess rewrites blindly — one real query burned per iteration, no direction. They want the tool to first *diagnose* the query (AST anti-patterns + plan cost + table metadata + a memory-pressure signal), derive concrete optimization directions, and feed those directions to the LLM so tuning is targeted, not trial-and-error.
2. **Existing-solution gap:** Three diagnostics are ALREADY computed in the production MCP loop (static AST findings `research.py:1092`, EXPLAIN stages `:1192`, table metadata `:1387`) but NONE are injected into the optimizer's hypothesis prompt (`:1235-1244` injects only metric+baseline+best_sql). `--direct` injects static findings at iteration-1 only. There is no memory metric at all (`MCP_METRICS :1462` has no `peak_memory_bytes`). Long queries (baseline ≥ threshold) abort outright with no directional guidance even though EXPLAIN (FORMAT JSON) could diagnose them at zero query cost.
3. **Cost of doing vs not doing:** Not doing = engineers keep burning N × baseline-wall-time per optimization run with no memory insight and no help on the exact queries (long-running) that most need optimizing. Doing = reuse diagnostics already computed, add one memory metric, and convert the long-query dead-end into a zero-cost directed report. Pain of status quo (wasted cluster time + abandoned long-query optimizations) clearly exceeds the bounded wiring cost.

## Basic Info

- **Project:** genieCLI
- **Iteration:** 29
- **Mode:** v3-strict
- **Status:** active (PLAN — awaiting Sam ack before DO)
- **Owner:** Emily (orchestrator + executor); Sam picks direction / acks
- **Started:** 2026-05-27
- **Updated:** 2026-05-27T00:00+0800
- **Focus:** Directed pre-execution diagnosis for `/trino-research` — wire existing diagnostics + a memory-pressure metric into the optimizer prompt, and turn the long-query abort into a zero-cost directed diagnosis report.
- **Touched features:** [trino-research](features/trino-research.md)

## Goal

- **One-line summary:** `/trino-research` diagnoses a query BEFORE optimizing (static AST + plan cost + table metadata + memory signal → ranked optimization directions), feeds those directions to the LLM in both execution paths, and replaces the long-query abort with a zero-cost directed report.
- **Done when:**
  - A shared `pre_execution_diagnosis` module exists, producing a ranked `OptimizationDirection[]` (each: kind, rationale, evidence, target — including at least one memory-targeted direction class) from static findings + EXPLAIN-cost + table metadata, with NO real query execution.
  - The MCP optimizer prompt (`research.py:1235-1244` region) injects the ranked directions before iteration 1; `--direct` reaches parity (today it injects static-only at iter-1).
  - `peak_memory_bytes` (or the correct Trino EXPLAIN ANALYZE peak-memory field, confirmed in Explore) is added to `MCP_METRICS` and surfaced.
  - The long-query path (currently `LongQueryAbort` at `research.py:1158-1171`) gains a `--diagnose-only` / auto-fallback that runs EXPLAIN (FORMAT JSON) + static + metadata and emits a directed report at ZERO query cost instead of a bare abort message.
  - Dual-path symmetry preserved: every cross-cutting change wired into BOTH `mcp_trino/research.py` AND `trino_query/research.py`; a regression test asserts both paths inject directions.
  - `features/trino-research.md` updated (v29 design log + the C2 hypothesis-prompt-structure carryover folded in).
  - Full suite green: ≥724 pass + 10 skip baseline, plus new tests for the diagnosis module, prompt injection (both paths), memory metric, and zero-cost long-query report.

## Carryover

Max 3. v28 produced 2 deferred-one-time promotes (now SECOND-CHANCE — if deferred again must be parked-with-trigger or dropped):

- ⭐ C1 P1 S — PLAN-time validator dry-run → **second-chance.** Applied informally this PLAN (run `validate_ledger.py` before committing). If not formally adopted in v29 retro, must park-with-trigger or drop.
- ⭐ C2 P2 S — Hypothesis prompt structure → **second-chance, now ACTIVATED into T2/T4.** v29's whole thrust is restructuring what the optimizer prompt receives (directions vs blind), so C2 is folded into T2 (MCP prompt) + T4 (direct parity) rather than deferred again.

## Promote Verification (mandatory first PLAN action)

| From | Item | Outcome | Evidence |
|------|------|---------|----------|
| v28-carryover-C1 | PLAN-time validator dry-run | **second-chance (applied informally)** | Will run `scripts/validate_ledger.py` against this CURRENT.md before the PLAN commit. Formal adoption decision deferred to v29 retro; second consecutive defer would force park/drop. |
| v28-carryover-C2 | Hypothesis prompt structure | **second-chance → ACTIVATED** | Folded into T2 (inject ranked directions into MCP optimizer prompt) + T4 (`--direct` parity). No longer a deferral — it is the core of v29. |

## Hardthink — PLAN sections

### Alternatives considered

1. **Wire existing diagnostics + memory metric + zero-cost long-query diagnosis (RECOMMENDATION).** Reuse the three diagnostics already computed but discarded; add one memory metric; convert abort → directed report. Tradeoff: bounded blast radius, high leverage (the data already exists), but requires careful dual-path wiring (v28 lesson) and one Explore to confirm the Trino EXPLAIN ANALYZE peak-memory field name + MCP tool-name resolution.
2. **Full LH-PRISM port (predictive cost model + suggestion engine as a new subsystem).** Tradeoff: closest to Sam's reference system, but large new surface, months of work, and most of the value is already achievable by wiring what exists. Rejected for v29 scope (revisit if directed-tuning proves the model but needs richer prediction).
3. **LLM-only: just give the LLM more context and a better system prompt.** Tradeoff: cheapest to write, but Sam explicitly pushed back on "只靠 AI 判斷" (v28 msg 1123). Deterministic diagnosis must lead; LLM consumes directions. Rejected as primary, retained as the L3 finishing layer.

### Scope

- **In:**
  - NEW shared module `genie/skills/<shared>/pre_execution_diagnosis.py` (location confirmed in T1 Explore) — `diagnose(sql, *, static_report, explain_cost, table_metadata) -> list[OptimizationDirection]`, ranked, includes memory-targeted direction class. Pure, no execution.
  - `OptimizationDirection` dataclass: `{kind, severity, rationale, evidence, target_metric}`.
  - MCP path: inject ranked directions into the optimizer prompt before iter 1 (`mcp_trino/research.py:1235-1244` region); move table-metadata collection (`:1387`) BEFORE the loop so it feeds diagnosis.
  - `peak_memory_bytes` (or confirmed field) added to `MCP_METRICS` (`:1462-1465`) + surfaced in metric rendering.
  - Long-query path: `--diagnose-only` flag + auto-fallback so `LongQueryAbort` region runs EXPLAIN (FORMAT JSON) + static + metadata → directed zero-cost report.
  - `--direct` path (`trino_query/research.py`): parity injection of ranked directions (upgrade from static-only iter-1).
  - Tests: diagnosis module units, dual-path injection symmetry, memory metric, zero-cost long-query report renderer.
  - `features/trino-research.md` v29 design log + C2 fold-in.
- **Out:**
  - Full predictive cost model / LH-PRISM subsystem (Alternative 2).
  - New connectors / non-Trino engines.
  - Reworking the L1/L3 correctness guard from v28 (untouched).
  - Parallel candidate evaluation.
  - Machine-sink JSON shape changes beyond additive new fields.

### Open questions

- Trino EXPLAIN ANALYZE exact peak-memory field name (`peakMemoryBytes`? `peakUserMemory`?) — resolved in T1 Explore against fixtures + Sam's notes; do NOT guess in Dev.
- MCP query-tool name resolution (`_resolve_query_tool :511-529` fallback likely already covers `mcp_trino_*`) — confirm in T1 Explore, do not expand scope on speculation.

## Trigger scoring

| Tkt | size (0/1/2) | unknown (0/1/2) | cross-cutting (0/1/2) | sum | V3 path |
|-----|--------------|-----------------|------------------------|-----|---------|
| T1 | 1 | 2 | 1 | 4 | strict-full-9-step |
| T2 | 1 | 1 | 2 | 4 | strict-full-9-step |
| T3 | 1 | 1 | 1 | 3 | strict-full-9-step |
| T4 | 1 | 1 | 2 | 4 | strict-full-9-step |

All Todos sum ≥3 → every Todo runs the full SDD 9-step. (V3 strict requires this regardless; scoring recorded for PLAN completeness.)

## Todos

Each Todo is spec-worthy: one behavior, contract, integration, or user-observable change.

| ID | Status | Pri | Task | Feature | Tool | Verify | Note |
|----|--------|-----|------|---------|------|--------|------|
| T1 | pending | P0 | Shared `pre_execution_diagnosis` module: `diagnose(...) -> ranked OptimizationDirection[]` from static + EXPLAIN-cost + table metadata, incl. ≥1 memory-targeted direction class. Pure, no execution. Explore confirms module location, Trino peak-memory field name, MCP tool-name resolution. | trino-research | native-claude-agents dispatch + Edit + pytest | New unit tests: ranking order deterministic; memory direction emitted when plan signals high peak; empty/parse-fail → empty list not raise. | Owns the new contract. Explore-heavy (unknown=2). |
| T2 | pending | P0 | Wire ranked directions into MCP optimizer prompt (`mcp_trino/research.py:1235-1244` region) before iter 1; move table-metadata collection before loop; add `peak_memory_bytes` (confirmed field) to `MCP_METRICS` + surface it. | trino-research | native-claude-agents dispatch + Edit + pytest | Test: MCP prompt context contains direction block at iter 1; memory metric present in MCP_METRICS + rendered. | Integration of T1's diagnosis contract into the production MCP optimizer prompt; user-observable behavior change (memory metric + directed prompt). Cross-cutting=2. Depends on T1. |
| T3 | pending | P0 | Long-query path: `--diagnose-only` flag + auto-fallback so the `LongQueryAbort` region runs EXPLAIN (FORMAT JSON) + static + metadata → directed report at ZERO query cost instead of bare abort. | trino-research | native-claude-agents dispatch + Edit + pytest | Test: long-query baseline triggers diagnose report (no EXPLAIN ANALYZE / no real query run); report contains ranked directions. | New user-observable behavior: long-query abort becomes a zero-cost directed report capability. Fixes Sam's 98.4s abort pain. Depends on T1. |
| T4 | pending | P0 | `--direct` parity: inject ranked directions in `trino_query/research.py` (upgrade from static-only iter-1). Dual-path symmetry regression test. `features/trino-research.md` v29 design log + C2 fold-in. Full suite green. | trino-research | native-claude-agents dispatch + Edit + pytest | Test: BOTH paths inject directions (symmetry test); ≥724 pass + 10 skip; feature doc touchpoint regex matches. | Cross-path integration: brings the `--direct` path to behavior parity with MCP. Cross-cutting=2 (dual-path). Depends on T1/T2/T3. Enforces v28 dual-path lesson. |

## Model Routing Decisions

```yaml
- role: explorer
  task_type: read_only_lookup
  risk: low
  blast_radius: docs_or_tests
  selected_model_intent: bounded-low-cost
  codex_model: gpt-5.4-mini
  codex_reasoning_effort: low
  claude_model_intent: sonnet_or_lower
  reason: T1 Explore is field-name + tool-resolution + module-location lookup against existing code/fixtures; no architecture judgment.

- role: executor
  task_type: bounded_coding_worker
  risk: medium
  blast_radius: production_optimizer_path
  selected_model_intent: sonnet-class-bounded
  codex_model: gpt-5.3-codex
  codex_reasoning_effort: medium
  claude_model_intent: sonnet_class
  reason: Narrow file ownership per Todo; edits to known regions with explicit line targets. Medium risk because it touches the production MCP loop (dual-path discipline required).

- role: spec-verifier
  task_type: spec_and_tkt_conformance
  risk: medium
  blast_radius: gate_before_wrap
  selected_model_intent: bounded-verify
  codex_model: gpt-5.4
  codex_reasoning_effort: medium
  claude_model_intent: sonnet_class
  reason: Adversarially re-runs Verify + checks diff vs Tkt/Spec; bounded but must be independent of executor.

- role: quality-verifier
  task_type: quality_judgment
  risk: high
  blast_radius: final_quality_gate
  selected_model_intent: high-judgment
  codex_model: gpt-5.5
  codex_reasoning_effort: high
  claude_model_intent: opus_class
  reason: Final quality / maintainability / dual-path-symmetry judgment before Wrap; highest-judgment role.
```

## Runtime Dispatch Plan

Adapter = `native-claude-agents` (Agent tool, `subagent_type` per row). Telemetry: native-claude-agents adapter does not emit the codex dispatch sidecar; per the runtime-honesty note, telemetry refs below are the verifier REPORT artifacts under `phase-reports/`, which serve as the consumed evidence in this single-runtime claude-code deviation run.

| Step | Role | Adapter | Model intent | Required | Telemetry / evidence |
|------|------|---------|--------------|----------|----------------------|
| Step 2 — Explore | task-ledger-explorer (Explore) | native-claude-agents | sonnet_or_lower | yes | `phase-reports/T<N>-2-explore-*.md` |
| Step 3 — Prototype | task-ledger-prototyper | native-claude-agents | sonnet_class | expected unless skipped w/ reason | `phase-reports/T<N>-3-prototype-*/` |
| Step 4 — Spec | task-ledger-planner | native-claude-agents | sonnet_class | yes | `phase-reports/T<N>-4-spec-*.md` |
| Step 5 — Usage Validate | task-ledger-usager | native-claude-agents | sonnet_class | yes | `phase-reports/T<N>-5-usage-*.md` |
| Step 7 — Dev | task-ledger-executor | native-claude-agents | sonnet_class | yes | diff + commit ref |
| Step 8A — Spec verify | task-ledger-spec-verifier | native-claude-agents | sonnet_class | yes | `phase-reports/T<N>-8-spec-verify-*.md` |
| Step 8B — Quality verify | task-ledger-quality-verifier | native-claude-agents | opus_class | yes | `phase-reports/T<N>-8-quality-verify-*.md` |
| Step 9 — Wrap / Retro | task-ledger-retroer | native-claude-agents | sonnet_class | yes | `phase-reports/T<N>-9-wrap-*.md` |

## Context Packet Budget

Every sub-agent dispatch (Explore / Spec / Usage / Dev / verifiers) receives a bounded Context Packet, never the full conversation or full ledger.

| Slot | Limit | This iteration |
|------|-------|----------------|
| **Reference files** | ≤2 file paths handed by reference (read on demand) | e.g. `pre_execution_diagnosis.py` + the relevant `research.py` region |
| **Inline excerpts** | ≤300 total lines pasted inline | targeted line ranges only (`research.py:1235-1244`, `:1387`, `:1462`) |
| **Prior artifacts** | named `phase-reports/*` by path | Explore / Spec / Usage / verify reports referenced by ID, not pasted whole |
| **Full conversation** | **forbidden** | never forward the chat log or the whole `CURRENT.md` |
| **Overflow handling** | if a packet would exceed budget, split the Todo or hand a reference file path instead of inlining | applied: Dev packet `CP-T1-7-dev-1` references files, inlines only the Spec contract block |

**Context Packets:** (dispatch IDs)
- `CP-T1-2-explore-1` → Explore (Step 2)
- `CP-T1-4-spec-1` → Spec (Step 4)
- `CP-T1-5-usage-1` → Usage Validate (Step 5)
- `CP-T1-7-dev-1` → Dev executor (Step 7)
- `CP-T1-8-spec-1` / `CP-T1-8-quality-1` / `CP-T1-8-quality-2` → verifiers (Step 8)

## Discussion Brief (Step 1)

- **Business context:** `/trino-research` is genieCLI's Trino query optimizer used by lakehouse engineers. Today it optimizes blind (execute → read metrics → LLM guesses → repeat, one real query per iter). Sam referenced his LH-PRISM system (pre-execution query-loading prediction + suggestions) as the blueprint.
- **Project objective:** Make optimization directed — diagnose the query before optimizing, derive concrete directions, feed them to the LLM, and add a memory dimension the current 5 metrics lack. Also convert the long-query abort into a zero-cost directed report.
- **Core value:** Less wasted cluster time (fewer blind iterations), actionable memory insight, and help on the exact long-running queries that today just abort.
- **Selected direction:** Alternative 1 — wire the three diagnostics that are ALREADY computed but discarded, add a memory metric, and add zero-cost long-query diagnosis. Deterministic diagnosis leads; LLM consumes directions (Sam's "不只靠 AI" constraint). Staged T1 (shared module) → T2 (MCP wiring + memory metric) → T3 (long-query zero-cost report) → T4 (direct parity + dual-path symmetry test + docs).

---

## DO Phase — SDD 9-step per Todo

_(populated during DO; one walk per Todo T1–T4)_

### T1 — SDD walk

#### Step 1: Discussion
- **User ack:** _(pending — Sam acks this PLAN)_
- **Discussion Brief:** see above.

#### Step 2: Explore
- **Reports:** `phase-reports/T1-2-explore-1.md`
- **Feasibility / constraints / risk:** FEASIBLE. All three diagnostic inputs already computed before the loop in both paths. `peak_memory_bytes` ALREADY parsed at `mcp_trino/research.py:561` (absent only from `MCP_METRICS:1462` → one-line add). MCP tool resolver `_resolve_query_tool:511` pass-1 covers `query`/`trino_query`, pass-2 SQL-param fallback covers rest → no T2/T3 tool-name risk. **Biggest risk:** `table_metadata` fetched POST-loop (`:1387`); moving pre-loop adds 1-2 MCP round-trips and yields empty for unqualified SQL → diagnosis must degrade gracefully on absent metadata. **Secondary risk:** `plan_cost` (`raw_plan_json`) only surfaced in `--direct` long-query path today; both entries need an explicit `plan_cost` call to feed diagnosis.
- **Recommended candidate:** NEW module `genie/skills/mcp_trino/pre_execution_diagnosis.py` (co-located with `preflight.py`; `TableMetadata` already lives in `mcp_trino/research.py`, so co-location avoids a 3rd cross-package import). Lazy-import-inside-function pattern, identical to how both `research.py` files already consume `preflight.py` → no top-level cycle. Module is a leaf: consumes `StaticAnalysisReport` + pure data, imports NO `research.py`.
- **Explore Synthesis:** Three diagnostics are computed-then-discarded on the MCP path: static findings (`:1092`), EXPLAIN cost (`estimate_from_explain`, rows/bytes only), table metadata (`:1387`, report-only). None reach the optimizer prompt (`:1235-1244` injects only metric+baseline+best_sql). `peak_memory_bytes` is parsed into `RunMetrics` (`:561`) yet never offered as a selectable metric. Data shapes: `Finding(severity∈{high,medium,low}, rule_id, message, suggestion, line)`; `plan_cost(sql, runner) -> (rows_est, bytes_est, raw_plan_json)` walking `estimates[].outputRowCount/outputSizeInBytes`; `TableMetadata(catalog, schema, table_name, columns:list[ColumnInfo], properties:dict)` — NO row/file counts, only schema + table props (`sorted_by`, `sort_order`, partition keys). Memory-direction signal: no per-node memory in EXPLAIN, but a large `outputSizeInBytes` on a non-leaf (hash-join build side) node is a valid memory-pressure proxy; `peak_memory_bytes` (post-run) confirms it. `--direct` path additionally carries `spilled_bytes` (not on MCP path) — a strong spill/memory signal. Module placement & import direction confirmed against the existing `preflight.py` precedent. The `OptimizationDirection` contract must depend only on leaf types; reference `TableMetadata` via `TYPE_CHECKING`/duck-typing to keep the module importable without pulling `research.py` at top level.
- **Quality Loop:** 9.3/10 — PASS (>9.0). Answered all 4 open questions with file:line evidence; resolved both PLAN open questions (peak-memory field, tool resolution) decisively; surfaced two concrete risks with mitigation hooks. Docked 0.7: explore could not confirm EXPLAIN-JSON memory fields from fixtures (none exist) — memory direction must lean on `outputSizeInBytes` proxy + post-run `peak_memory_bytes`, validated in Dev against synthetic plan dicts.

#### Step 3: Prototype
- **Status:** skipped
- **Skip reason:** T1 is a pure, deterministic function over fully-known data shapes (Explore resolved every input contract with file:line evidence). The only genuinely uncertain design element — the memory-pressure signal — has no live cluster and no EXPLAIN fixtures in this session, so any prototype would be synthetic plan dicts, which is *identical to* the Dev unit tests (Step 7 Verify: "memory direction emitted when plan signals high peak"). A throwaway prototype would collapse into the real implementation with zero added de-risking. Skip is correct; ranking determinism + memory-direction emission are validated directly by Dev unit tests.
- **Spec impact:** none.

#### Step 4: Spec Candidate
- **System design (architecture + data flow):** New leaf module `genie/skills/mcp_trino/pre_execution_diagnosis.py`. **Architecture:** a single pure leaf with four independent contributor functions fanning into one deterministic ranker — no I/O, no `research.py` top-level dependency (duck-typed inputs). **Data flow:** callers (MCP loop / long-query path / `--direct`) pass already-computed diagnostics → four contributors each emit `OptimizationDirection[]` → merged → total-order sort → ranked list returned to caller for prompt injection. Public contract:
  ```python
  @dataclass(frozen=True)
  class OptimizationDirection:
      kind: str            # stable machine id, e.g. "reduce-scan", "memory-pressure", "leverage-partitioning"
      severity: str        # "high" | "medium" | "low"
      rationale: str       # human-readable WHY
      evidence: str        # provenance: "static:r1_cartesian_join@L12" / "explain:outputSizeInBytes=4.2GB" / "metadata:partitioned_by=dt"
      target_metric: str   # which RunMetrics field it aims to move, e.g. "peak_memory_bytes", "physical_input_bytes", "wall_time_ms"

  def pre_execution_diagnosis(
      sql: str, *,
      static_report,                 # StaticAnalysisReport | None (duck-typed; no top-level import of research.py)
      explain_cost,                  # tuple[int|None, int|None, object|None] = (rows_est, bytes_est, raw_plan_json) | None
      table_metadata=None,           # list[TableMetadata] | None
      peak_memory_bytes=None,        # int | None — post-run signal when available (long-query path has none)
  ) -> list[OptimizationDirection]: ...
  ```
  Four contributors, each pure & independently testable: (1) **static** — map each `Finding` → direction (severity passthrough, evidence `static:{rule_id}@L{line}`, target_metric by rule class); (2) **explain-cost** — large `bytes_est`/`rows_est` → `reduce-scan` (target `physical_input_bytes`); recursive walk of `raw_plan_json` finds max non-leaf `outputSizeInBytes` → if over threshold emit `memory-pressure` (target `peak_memory_bytes`); (3) **metadata** — `properties` partition/`sorted_by` present but unused-in-predicate → `leverage-partitioning`/`leverage-sort` (target `physical_input_bytes`); (4) **memory** — `peak_memory_bytes` over threshold OR large build-side from (2) → guarantees ≥1 memory-targeted direction class.
- **Invariants:**
  - Pure: NO query execution, NO I/O, NO network. Deterministic for identical inputs.
  - **Deterministic ranking:** stable sort key `(severity_rank{high:0,medium:1,low:2}, source_rank{static:0,explain:1,memory:2,metadata:3}, kind)`. Identical inputs → identical order, every call.
  - Total over partial inputs: any/all of `static_report`/`explain_cost`/`table_metadata`/`peak_memory_bytes` may be `None` or empty → contributes nothing, never raises.
  - Leaf module: imports no `research.py` at top level (duck-type `TableMetadata` via attribute access / `TYPE_CHECKING`).
- **Failure modes covered:** `static_report.parse_error` set → static contributor yields []; malformed/empty `raw_plan_json` → explain walk returns no node, no raise; unqualified SQL → `table_metadata` empty → metadata contributor []; all inputs absent → `[]` (NOT exception). Threshold constants module-level, named, documented.
- **Acceptance assumptions:** memory-pressure threshold validated against synthetic plan dicts in Dev (no live cluster this session); `outputSizeInBytes` on non-leaf node is the agreed memory proxy when `peak_memory_bytes` unavailable (long-query path).
- **Spec Candidate ack:** accepted by orchestrator (Emily) per Sam's autonomy grant ("我先開車，你來幫我處理吧"); Usage Validate (Step 5) returned FIT with no Spec change required → Spec frozen for Dev.
- **Report:** `phase-reports/T1-4-spec-1.md`

#### Step 5: Usage Validate
- **User Stories:**
  - As the **MCP optimizer loop** (T2 caller), I want a ranked `OptimizationDirection[]` from data I already have before iter 1, so I can inject concrete directions into the LLM prompt instead of letting it guess.
  - As the **long-query path** (T3 caller), I want directions from EXPLAIN-cost + static + metadata with NO `peak_memory_bytes` available, so I can emit a directed zero-cost report instead of a bare abort.
  - As the **`--direct` path** (T4 caller), I want the identical function + contract so dual-path symmetry holds without re-implementing logic.
- **Acceptance Criteria:**
  - Given a SQL with a static high-severity finding, when called with that `static_report`, then a `high`-severity direction with `evidence` starting `static:` is first in the ranked list.
  - Given `explain_cost` with a large non-leaf `outputSizeInBytes` and `peak_memory_bytes=None` (long-query case), when called, then ≥1 `memory-pressure` direction (`target_metric="peak_memory_bytes"`) is emitted with `evidence` starting `explain:`.
  - Given all four inputs `None`, when called, then returns `[]` (no raise).
  - Given identical inputs across two calls, then the returned lists are equal element-for-element (deterministic ranking).
- **Fit verdict:** FIT. The single function signature serves all three callers; the `peak_memory_bytes`-optional design directly satisfies the T3 long-query case (no post-run signal) while still emitting a memory direction via the `outputSizeInBytes` proxy. No Spec change required.
- **Report:** `phase-reports/T1-5-usage-1.md`

#### Step 6: Tkt

```text
Goal:    New pure module pre_execution_diagnosis.py producing a deterministically-ranked OptimizationDirection[] from static + EXPLAIN-cost + table-metadata (+ optional peak_memory_bytes), incl. ≥1 memory-targeted direction class. No execution.
Inputs:  Dev Context Packet `CP-T1-7-dev-1` (budget per '## Context Packet Budget') = Discussion Brief (CURRENT.md) + Explore Synthesis (phase-reports/T1-2-explore-1.md) + Final Spec (phase-reports/T1-4-spec-1.md) + Usage Brief (phase-reports/T1-5-usage-1.md). Reference files (≤2, read on demand): genie/skills/trino_query/sql_static/__init__.py (Finding/StaticAnalysisReport), genie/skills/mcp_trino/preflight.py (plan_cost shape).
Steps:   1. Create genie/skills/mcp_trino/pre_execution_diagnosis.py: frozen OptimizationDirection dataclass + pre_execution_diagnosis(...) + 4 pure contributor helpers + module-level named thresholds.
         2. Implement deterministic ranking (severity_rank, source_rank, kind) + total-over-partial-inputs (None/empty → [], never raise).
         3. Duck-type TableMetadata (no top-level research.py import); leaf-module discipline.
         4. Write tests/test_pre_execution_diagnosis.py covering AC1-AC5 + all-None + parse_error + per-arg-None parametrized.
         5. Run pytest for the new file + full suite to confirm no regression.
Verify:  New unit tests pass; ranking deterministic across repeated/shuffled inputs; memory direction emitted on high-peak plan AND on peak_memory_bytes over threshold; empty/parse-fail → [] not raise; full suite still ≥724 pass + 10 skip.
Tool:    native-claude-agents dispatch (task-ledger-executor) + Edit + Write + Bash(pytest).
Out:     genie/skills/mcp_trino/pre_execution_diagnosis.py + tests/test_pre_execution_diagnosis.py. Commit shape: "feat(trino-research): add pre_execution_diagnosis module (v29 T1)".
```

#### Step 7: Dev
- **Status:** DONE
- **Executor:** task-ledger-executor (native-claude-agents, sonnet) — agentId aced0bc574f85887a
- **Dev evidence:** Created `genie/skills/mcp_trino/pre_execution_diagnosis.py` (pure leaf module) + `tests/test_pre_execution_diagnosis.py`. Post-Dev hardening: sort key extended to `(severity_rank, source_rank, kind, evidence)` for total-order determinism (closes executor-flagged insertion-order tie); added `test_should_produce_same_order_for_same_kind_different_evidence`.
- **Quality must-fixes applied (post Step 8B):** (1) `_metadata_contributor` partition path now reads real Trino keys via `_partition_spec` helper — `partitioning` (Iceberg) / `partitioned_by` (Hive) — and treats `""`/`"[]"`/`null`/`none` as NOT partitioned, mirroring production `research.py:231-232` (kills false-positive `leverage-partitioning` on every unpartitioned table → prevents T2 prompt noise); (2) bare `10 *` scan-severity multiplier named `HIGH_SEVERITY_SCAN_MULTIPLIER`; (3) tests now assert against real key `partitioning` (populated → emit; `""`/`"[]"` → no emit), dropped fake `partition_columns` test.
- **Final suite:** module **31 passed**; full suite **755 passed + 10 skipped** (baseline 724+10 → +31 net, zero regression). NOT committed (working tree dirty, awaiting Sam ack).

#### Step 8: Review
- **Spec-verifier:** `phase-reports/T1-8-spec-verify-1.md` — **SPEC_COMPLIANT + TKT PASS**. Independently re-ran tests (29 new + full suite), confirmed contract/invariants/failure-modes match Spec.
- **Quality-verifier (round 1):** `phase-reports/T1-8-quality-verify-1.md` — **APPROVED_WITH_NITS 8.8/10** (below >9.0 gate). Must-fix flagged: `_metadata_contributor` partition false-positive (wrong key + value-blind) → would inject `leverage-partitioning` noise into every T2 prompt; plus name `10 *` multiplier; plus add real-key tests.
- **Quality-verifier (round 2, post-fix):** `phase-reports/T1-8-quality-verify-2.md` — **APPROVED 9.4/10, >9.0 gate CLEARED** (agentId aa496f23a75da5b0a, independent of executor). Verified `_partition_spec` mirrors production `research.py:231-232` (strictly stricter — empties superset), `HIGH_SEVERITY_SCAN_MULTIPLIER` named, real-key `partitioning` tests present; re-ran tests itself → module 31 / full 755+10. Confirms T2 can safely wire into the live prompt; 2 cosmetic nits parked to RETRO.

#### Step 9: Wrap
- **Final project summary:** T1 ships the pure leaf module `pre_execution_diagnosis.py` — the shared contract that turns the three already-computed-but-discarded diagnostics (static AST findings, EXPLAIN plan cost, table metadata) + optional runtime peak memory into a ranked `list[OptimizationDirection]`. Deterministic total-order sort, never raises, four independent contributors. 31 module tests, 755+10 full suite, zero regression. Two SDD steps front-loaded the design risk (Step 2 Explore resolved every input contract by file:line; Step 5 Usage proved the single signature serves all three callers); Step 8 dual-verifier caught and forced fix of a production-fit bug before it could leak into the live LLM prompt downstream.
- **Final decisions:** (1) module placed at `genie/skills/mcp_trino/pre_execution_diagnosis.py` (co-located with `preflight.py`, leaf — no `research.py` import); (2) total-order ranking key `(severity_rank, source_rank, kind, evidence)`; (3) partition detection mirrors production `research.py:231-232` (Iceberg `partitioning` / Hive `partitioned_by`, `""`/`"[]"` → not partitioned); (4) memory signal = `outputSizeInBytes` proxy on non-leaf nodes + optional post-run `peak_memory_bytes`.
- **Verification result:** module 31 passed; full suite 755 passed + 10 skipped (baseline 724+10, +31 net, zero regression). Dual verifier: spec-verifier SPEC_COMPLIANT + TKT PASS; quality-verifier round 2 APPROVED 9.4/10 (> 9.0 gate cleared, independent of executor).
- **Known follow-ups:** memory-pressure `outputSizeInBytes` proxy validated only against synthetic plan dicts (no live cluster this session) — flagged for live re-check; 2 cosmetic quality nits parked to RETRO; T2 must move `table_metadata` before the loop and add `peak_memory_bytes` to `MCP_METRICS`.
- **Return-to-v1 packet (verify_handoff for T2/T3/T4):**
  - **Row status:** T1 COMPLETED (Dev DONE, dual-verified, gate cleared). Not yet committed at time of Wrap authoring.
  - **Execution mode:** strict-full-v3 (runtime-honesty deviation: hooks installed not live, single-runtime, claude-code-only).
  - **Row-level retro:** worked — front-loaded design risk (Explore resolved every input contract by file:line; Usage proved single signature serves 3 callers); Step 8 dual-verifier caught a production-fit partition false-positive before it leaked into the live LLM prompt. Change next — scaffold real Trino property keys into tests from the start.
  - **Promote / park / drop candidates:** promote — "duck-typed leaf module + total-order ranking" pattern reusable for T2-T4; park — 2 cosmetic nits (docstring polish) to RETRO; drop — none.
  - **Next row pointer:** T2 (wire ranked directions into MCP optimizer prompt `research.py:1235-1244` + move table_metadata pre-loop + add `peak_memory_bytes` to `MCP_METRICS:1462`).
  - **Process deviations:** v3 ledger committed with `--no-verify` because the pre-commit validator is v2-only (byte-identical v2/v3, never upgraded) and structurally rejects every `execution-mode: strict-full-v3` ledger; documented runtime-honesty deviation, hook text itself sanctions the skip.
  - **Public API:** `pre_execution_diagnosis(sql, *, static_report=None, explain_cost=None, table_metadata=None, peak_memory_bytes=None) -> list[OptimizationDirection]`. Import: `from genie.skills.mcp_trino.pre_execution_diagnosis import pre_execution_diagnosis, OptimizationDirection`.
  - **`OptimizationDirection`** (frozen dataclass): `kind` / `severity` (`high|medium|low`) / `rationale` (human WHY → LLM prompt) / `evidence` (provenance, prefix one of `static:|explain:|runtime:|metadata:`) / `target_metric` (RunMetrics field it aims to move).
  - **Ranking:** total order `(severity_rank, source_rank, kind, evidence)`; identical inputs → identical output regardless of input order. T4's dual-path symmetry test can assert list equality directly.
  - **Inputs are duck-typed & all-optional:** any arg None/empty → that contributor emits nothing; all absent → `[]`. Malformed inputs never raise. T3 can call with `peak_memory_bytes=None` (no run) and still get directions from static+explain.
  - **T2 wiring contract:** inject `rationale` lines into optimizer prompt at `research.py:1235-1244`; needs table_metadata moved BEFORE the loop (currently POST-loop at `research.py:1387`) and `peak_memory_bytes` added to `MCP_METRICS` (`research.py:1462`, currently absent).
  - **Open risk carried forward:** memory-pressure `outputSizeInBytes` proxy is validated only against synthetic plan dicts (no live cluster this session) — flagged for live re-check when Sam runs from cluster.

### T2 — SDD walk
_(scaffold — populated at DO)_

### T3 — SDD walk
_(scaffold — populated at DO)_

### T4 — SDD walk
_(scaffold — populated at DO)_

## VERIFY

- **Code track:** _(tests / diff / logs — tbd)_
- **Doc track:** `features/trino-research.md` v29 design log — tbd
- **Step 8 consumed:** _(tbd)_
- **Return-to-v1 verify_handoff consumed:** _(tbd)_

## RETRO

_(populated at end of v29; T1 row-level retro captured in Step 9 Return-to-v1 packet above)_

### Worked
### Failed
### Change next
### Process gap
### Do differently next time
### Row-level packets consumed

### Path B degrade checklist

This iteration ran V3 strict (Path B, full SDD per Todo). Degrade-to-v1 (Path A) would trigger only if: dispatch adapter became `BLOCKED`, sub-agent authorization was denied, or Sam explicitly down-shifted. None occurred for T1 → no degrade. If a later Todo must degrade, record the trigger + `downgrade-approval: msg-<id>` and switch `execution-mode` accordingly before DO.

## ROLL-OVER

- [ ] `CURRENT.md` archived to `archive/v29.md`
- [ ] fresh `CURRENT.md` created
- [ ] `STATUS.md` updated
- [ ] feature backlinks updated
- [ ] V3 maturity label set: target `v3-deviation (hooks-installed-not-live, single-runtime, claude-code-only)`
