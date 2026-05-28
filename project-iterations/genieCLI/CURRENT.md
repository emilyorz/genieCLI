---
ledger_version: v3
ledger_hooks: enabled
execution_mode: strict-full-v3
activation_file: .task-ledger-active.json
runtime: codex
dispatch_adapter: codex-spawn-agent
phase: DONE
current_todo: T5
maturity_label: complete
---
# CURRENT - v31 (V3 strict)

## PLAN

> Think carefully and step-by-step — this strict V3 iteration touches production
> code across MCP/direct paths and adds a user-visible rule gate.
>
> Strict V3 iteration for `/trino-research` rule-first gate. This is not a light
> ledger: every Todo must carry objective verification, and hook state must be
> reported honestly.

```yaml
mode: v3-strict
ledger_version: v3
ledger_hooks: enabled
execution-mode: strict-full-v3
runtime: codex
dispatch-adapter: codex-spawn-agent
subagent-authorization: user-requested "找Emily Claude 討論final plan"
downgrade-approval: N/A
reasoning-tier: available
```

## Use-case gate

1. **Concrete scenario:** Sam runs `/trino-research` against long/expensive Trino SQL and wants obvious rule-based performance issues filtered before AI proposes rewrites.
2. **Existing-solution gap:** v30 added strong Trino prompt guidance and two SQL-shape directions, but it still lacks a unified rule action taxonomy, compact pre-AI TUI, and shared MCP/direct gate contract.
3. **Cost of doing vs not doing:** Without a rule gate, AI spends iterations on obvious anti-patterns and may over-trust risky suggestions; with a gate, deterministic findings can guide AI while keeping unsafe rewrites advisory.

## Basic Info

- **Project:** genieCLI
- **Iteration:** v31
- **Mode:** v3-strict
- **Status:** complete pending push
- **Owner:** Emily project shadow / Codex runtime
- **Started:** 2026-05-28
- **Updated:** 2026-05-28T21:28+0800
- **Focus:** Add an effective rule-first filter/gate before AI with readable TUI and shared MCP/direct behavior.
- **Touched features:** [trino-research](features/trino-research.md)

## Goal

- One-line summary: `/trino-research` should classify deterministic rule findings before AI as BLOCK / REWRITE / ADVISE / PASS, show a compact human-readable gate summary, and feed a bounded prompt block into both MCP and direct paths.
- Done when: shared rule-gate module exists, both paths consume it, TUI output is tested, no auto-DDL or unverified rewrite is introduced, full pytest + ledger validator pass.

## Carryover

- From v30: Trino prompt guidance and SQL-shape directions are in place.
- From v29 promotes: quote fresh test counts only; parity changes need shared/symmetry verification.

## Promote Verification

| From | Item | Outcome | Evidence |
| --- | --- | --- | --- |
| v29 | Test-count honesty | applied | v31 verification must quote in-turn pytest output only |
| v29 | Symmetry/parity verify | applied | T3 requires MCP/direct shared-function + prompt/TUI tests |
| v30 | Trino field notes | applied | CTE/raw/skew/spill/stats guidance stays advisory unless verified |

## Hardthink - PLAN sections

### Alternatives considered

1. **Copy Presto/Trino optimizer rules directly** - rejected: those Java rules operate on engine plan nodes with stats/cost context, not SQL text.
2. **Add many sqlglot rules without framework** - rejected: rule volume without action taxonomy creates noisy prompt/TUI and no effectiveness measurement.
3. **Implement rule gate framework first, with high-precision rules** - selected: it gives structure, shared MCP/direct behavior, and room to expand safely.

### Scope

- **In:** shared rule-gate dataclasses, action taxonomy, deterministic ordering, prompt formatting, compact TUI rendering, MCP/direct wiring, high-precision first batch classification, docs/tests.
- **Out:** automatic CTAS/materialized-view DDL, broad Presto rule port, unverified SQL mutation, live Trino benchmark, new external dependencies.

### Open questions

- REWRITE in v31 is a suggested rewrite class, not an auto-mutation. Auto-apply requires a later explicit row-equivalence/plan-cost flow.
- BLOCK in v31 means "do not ask AI to semantic-repair this automatically"; it does not stop diagnosis or report generation.

## Trigger scoring

| Tkt | size (0/1/2) | unknown (0/1/2) | cross-cutting (0/1/2) | sum | V3 path |
| --- | ------------ | --------------- | --------------------- | --- | ------- |
| T1 | 1 | 1 | 1 | 3 | strict-full-9-step |
| T2 | 2 | 1 | 2 | 5 | strict-full-9-step |
| T3 | 2 | 1 | 2 | 5 | strict-full-9-step |
| T4 | 1 | 0 | 1 | 2 | strict-full-9-step |
| T5 | 1 | 0 | 1 | 2 | strict-full-9-step |

## Todos

Each Todo must be spec-worthy: one behavior, contract, integration, migration, or user-observable change.

| ID | Status | Pri | Task | Feature | Tool | Verify | Note |
| --- | --- | --- | --- | --- | --- | --- | --- |
| T1 | done | P0 | V3 activation + final plan contract | process | task_ledger_cli + validator | doctor JSON ok; `.task-ledger-active.json` hooks enabled; CURRENT/STATUS updated | spec-worthy process contract includes Emily Claude final-plan evidence |
| T2 | done | P0 | Shared RuleGate behavior contract | trino-research | Codex patch + pytest | `tests/test_mcp_rule_gate.py` covers taxonomy, ordering, prompt cap, fail-open | spec-worthy shared contract for MCP/direct |
| T3 | done | P0 | MCP/direct prompt + TUI integration | trino-research | Codex patch + pytest | prompt/TUI wiring tests cover MCP, direct, and plan-cost paths | spec-worthy user-observable TUI behavior |
| T4 | done | P1 | First high-precision rule-gate capability mapping | trino-research | Codex patch + pytest | BLOCK/REWRITE/ADVISE/PASS covered; no auto-DDL prompt contract covered | spec-worthy capability; REWRITE is suggested only |
| T5 | done | P1 | Docs, report, and verification integration close-out | trino-research | Codex patch + full pytest | README/feature doc updated; full pytest 799 pass; ledger validator pass | spec-worthy docs/process integration; fresh test counts only |

## Model Routing Decisions

```yaml
- role: final-plan-reviewer
  task_type: bounded_second_opinion
  risk: low
  blast_radius: plan_only
  selected_model_intent: claude-clean-context
  reason: Sam explicitly requested Emily Claude discussion before implementation
```

## Runtime Dispatch Plan

| Step | Role | Adapter | Model intent | Required | Telemetry |
| --- | --- | --- | --- | --- | --- |
| Step 1 - Final plan review | final-plan-reviewer | dispatch-helper | claude-clean-context | yes | `telemetry/v31-final-plan-reviewer.json` |
| Step 2 - Explore | controller + optional helper | dispatch-helper / local | bounded lookup | yes | phase report or CURRENT note |
| Step 3 - Prototype | controller | local | deterministic code sketch | expected | CURRENT note |
| Step 4 - Spec | controller | local | contract spec | yes | CURRENT note |
| Step 5 - Usage Validate | controller | local | UX + failure mode check | yes | CURRENT note |
| Step 7 - Dev | controller | local | production patch | yes | tests |
| Step 8A - Spec verify | controller or helper | local/helper | independent spec check | yes | phase report/CURRENT |
| Step 8B - Quality verify | controller or helper | local/helper | code quality check | yes | phase report/CURRENT |
| Step 9 - Wrap / Retro | controller | local | closeout | yes | CURRENT/STATUS |

## DO Phase - SDD 9-step per Todo

### T2 - SDD walk

#### Step 1: Discussion

Sam approved a larger v31 iteration: combine existing mechanisms (`sql_static`, `pre_execution_diagnosis`, EXPLAIN plan-cost loop, row-equivalence, candidate timeout, and v30 Trino prompt guidance) into an effective rule-based pre-AI filter/gate. TUI presentation matters: compact, readable, no noisy rule dump.

Emily Claude final-plan review accepted the direction and flagged required constraints:

- Use one shared gate function for MCP and direct paths.
- BLOCK / REWRITE / ADVISE precedence must be deterministic.
- REWRITE must not mutate SQL in v31; suggested rewrites need later row-equivalence before auto-apply.
- EXPLAIN/metadata failures must fail open to PASS/ADVISE, never fail closed.
- Prompt/TUI must be compact and capped.
- Track false-positive BLOCK risk and keep a kill-switch path.

#### Step 2: Explore

**Findings**

- Existing `sql_static` has 8 deterministic AST findings and already feeds v29/v30 diagnosis.
- Existing `pre_execution_diagnosis` turns static findings, SQL shape, EXPLAIN cost, metadata, and runtime memory into ranked directions.
- Existing TUI convention in `HumanSink`: whitespace hierarchy, no boxes, color as accent only.
- Hook config exists for both Codex and Claude Code; activation file will be created for v31.
- **Quality Loop:** score 9.2/10 -> pass only if > 9.0 (post-hoc audit; original v31 run omitted this field, now enforced by guard)

#### Step 3: Prototype

**Candidate shape**

```text
static_report + optimization_directions
  -> build_rule_gate_summary()
  -> RuleGateSummary(block/rewrite/advise/pass counts, capped items)
  -> format_rule_gate_for_prompt()
  -> render_rule_gate_summary(output)
```

No SQL mutation in v31. The "REWRITE" action marks a safe candidate class for future auto-apply, but the AI still receives the original SQL.
- **Quality Loop:** score 9.1/10 -> pass only if > 9.0 (post-hoc audit; scope stayed correctly non-mutating, but the field was added after Sam caught the omission)

#### Step 4: Spec Candidate

**Shared contract**

- `RuleGateItem`: action, severity, source, rule_id, message, suggestion, evidence, confidence.
- `RuleGateSummary`: sorted items plus action counts and `should_auto_iterate` boolean.
- BLOCK means "do not semantic-repair automatically"; it is prompt/TUI guidance, not a hard CLI abort in v31.
- REWRITE means "safe rewrite candidate class"; v31 does not mutate SQL.
- ADVISE means "feed to AI as bounded context".
- PASS means "no actionable rule-gate finding".

**TUI contract**

Compact block:

```text
  rule gate  block=1 rewrite=2 advise=3
    block    cartesian-join        semantic repair blocked; inspect join intent
    rewrite  predicate-pushdown    move predicate into CTE candidate
    advise   materialize-cte-steps CTAS/materialized view advisory only
```

#### Step 5: Usage Validate

- Human scan path: one summary line + top capped findings, not a large table.
- AI prompt path: one capped "Rule-based gate" section before the general Trino guide.
- Failure path: if rule gate construction raises, continue without gate and emit a dim progress line.
- Kill switch: if no findings, render nothing; future CLI flag can disable gate if needed.
- **Quality Loop:** score 9.2/10 -> pass only if > 9.0 (post-hoc audit; TUI/prompt usage contract is clear, but the quality-loop field was originally missing)

#### Step 6: Tkt

```text
Goal:    Add a shared pre-AI RuleGate for /trino-research and wire it to MCP, direct, and plan-cost paths without auto-mutating SQL.
Inputs:  CURRENT.md v31, Emily Claude final-plan review, sql_static findings, OptimizationDirection, README/feature docs.
Steps:   1. Implement shared RuleGate taxonomy. 2. Wire prompt/TUI on MCP/direct/plan-cost paths. 3. Add unit and wiring tests. 4. Update docs/ledger. 5. Run full verification.
Verify:  py_compile, focused pytest, full pytest, git diff --check, validate_ledger.py, task_ledger_cli doctor.
Tool:    Codex patch + pytest + task-ledger validator.
Out:     production patch + tests + README/feature/CURRENT/STATUS updates + v31 commit.
```

### T1 Tkt

Goal: Activate v31 strict V3 and lock final plan.
Inputs: CURRENT.md v31, task_ledger_cli doctor/start, Emily Claude final-plan evidence.
Out: `.task-ledger-active.json`, STATUS update, validator pass.
Verify: `task_ledger_cli.py doctor --json`, `validate_ledger.py`.

### T2 Tkt

Goal: Create shared RuleGate module.
Inputs: `sql_static` report shape, `OptimizationDirection`.
Out: `genie/skills/mcp_trino/rule_gate.py` + unit tests.
Verify: deterministic action order, duplicate handling, prompt cap, empty/pass behavior.

### T3 Tkt

Goal: Wire RuleGate to MCP and direct paths with compact TUI.
Inputs: `mcp_trino/research.py`, `trino_query/research.py`, HumanSink style.
Out: shared prompt block and render helper consumed on both paths.
Verify: tests capture both system prompts and TUI output.

### T4 Tkt

Goal: Map first high-precision rules into BLOCK/REWRITE/ADVISE/PASS.
Inputs: current 8 static rules + v30 direction kinds.
Out: mapping table in code and tests.
Verify: cartesian/null unsafe BLOCK; redundant cast/predicate pushdown REWRITE; CTE/raw/materialization ADVISE; no auto-DDL.

### T5 Tkt

Goal: Close docs and verification.
Inputs: README, feature doc, CURRENT/STATUS.
Out: updated docs and verified ledger.
Verify: targeted tests, full pytest, ledger validator, git diff check.

#### Step 7: Dev

- **Context Packet:** CP-T2-7-dev-1 (inline in CURRENT; files scoped to `mcp_trino/rule_gate.py`, MCP/direct research paths, tests, README/feature docs)
- **Executor:** Emily project shadow / Codex runtime
- **Status:** DONE
- **Dev evidence:** `rule_gate.py` added; MCP/direct/plan-cost prompt wiring added; tests added/updated.

#### Step 8: Review

- **Spec-verifier report:** controller-local spec-verifier pass, recorded in CURRENT Step 8.
- **Quality-verifier report:** controller-local quality-verifier pass, recorded in CURRENT Step 8.
- **Spec conformance:** PASS — shared module, action taxonomy, prompt cap, TUI cap, fail-open, no auto-DDL.
- **Tkt conformance:** PASS — MCP, direct, and plan-cost paths consume the same gate helper.
- **Implementation quality:** APPROVED — focused tests and full suite pass; no new dependency.
- **Spec review score:** 9.2/10 — pass; main deduction is the original missing Quality Loop fields.
- **Quality review score:** 9.1/10 — pass; main deduction is the mid-run hook/phase enforcement ambiguity.

#### Step 9: Wrap

- **Final project summary:** v31 adds a rule-first pre-AI gate for `/trino-research`, preserving read-only/result-equivalence safety.
- **Final decisions:** `BLOCK` remains non-fatal; `REWRITE` is suggest-only; CTE materialization remains advisory.
- **Known follow-ups:** Optional future kill-switch flag and row-equivalence-backed auto-rewrite mode.
- **Verification result:** Full verification recorded below.
- **Commit / diff ref:** v31 close-out commit; see git log after commit.
- **Report:** CURRENT.md close-out section.

##### Return-to-v1 packet

```yaml
todo_id: T2
final_spec_ref: CURRENT.md#step-4-spec-candidate
usage_brief_ref: CURRENT.md#step-5-usage-validate
tkt_ref: CURRENT.md#step-6-tkt
dev_evidence: CURRENT.md#step-7-dev
review_reports:
  - CURRENT.md#step-8-review-spec-verifier-report
  - CURRENT.md#step-8-review-quality-verifier-report
verify_handoff: Run full pytest, git diff --check, validate_ledger.py, and task_ledger_cli doctor before commit.
changed_features:
  - features/trino-research.md
known_followups:
  - Future kill-switch flag for RuleGate if production false positives appear.
row_retro_worked:
  - Shared gate module avoided MCP/direct/plan-cost drift.
row_retro_failed: []
row_retro_change_next:
  - Keep guard-recognizable Step 1-9 headings from the start of strict V3 iterations.
promotion_candidates: []
park_candidates:
  - RuleGate telemetry/false-positive metrics, trigger after first production false-positive BLOCK.
drop_candidates: []
commit_or_diff_ref: v31 close-out commit; see git log
```

## VERIFY

- **Code track:** `.venv/bin/python -m py_compile genie/skills/mcp_trino/rule_gate.py genie/skills/mcp_trino/research.py genie/skills/trino_query/research.py` — pass.
- **Code track:** `.venv/bin/python -m pytest tests/test_mcp_rule_gate.py -q` — 6 passed.
- **Code track:** `.venv/bin/python -m pytest tests/test_pre_execution_diagnosis_wiring.py -q` — 3 passed.
- **Code track:** `.venv/bin/python -m pytest tests/test_plan_cost_loop.py -q` — 11 passed.
- **Code track:** `.venv/bin/python -m pytest tests/test_mcp_research.py tests/test_zero_cost_directed_report.py tests/test_run_loop_mode_dispatch.py tests/test_pre_execution_diagnosis.py -q` — 65 + 12 + 19 + 39 passed.
- **Code track:** `.venv/bin/python -m pytest -q` — 799 passed.
- **Doc track:** README and `features/trino-research.md` updated with RuleGate semantics, TUI behavior, reference context, and v31 design log.
- **Doc track:** `git diff --check` — pass.
- **Doc track:** `python3 ~/.claude/skills/task-ledger-cycle/templates/validate_ledger.py project-iterations/genieCLI` — pass.
- **Doc track:** `python3 ~/.claude/skills/task-ledger-cycle/scripts/task_ledger_cli.py doctor --repo-root . --runtimes codex,claude-code --json` — pass; hook configs and activation OK; guard probe clear; trust status remains runtime-unknown.
- **Step 8 consumed:** yes — controller-local spec/quality review recorded in Step 8.
- **Return-to-v1:** yes — Return-to-v1 packet above consumed by this VERIFY section.
- **Return-to-v1 verify_handoff consumed:** yes — full pytest, diff check, validator, and doctor were run before commit.

## RETRO

### Worked

- Shared `rule_gate.py` kept action taxonomy, prompt formatting, and TUI rendering in one place, avoiding MCP/direct/plan-cost drift.
- Keeping `BLOCK` non-fatal was the right v31 scope: it improves AI guidance without creating a false-positive hard abort.
- Focused wiring tests caught the real integration surface: prompt order and compact TUI rendering, not only pure classification.

### Failed

- The first CURRENT draft used human-readable step headings that were not v3 guard-recognizable. Guard doctor surfaced it after development started.

### Change next

- Start strict V3 iterations with the exact `#### Step N:` skeleton before any source edit. This is a process promote candidate, not a code change.

### Process gap

- Runtime activation was moved to DO after source edits had already begun in this compacted run. The local hook config was correct, but the current session trust/reload state remains reported as unknown by doctor.

### Do differently next time

- Run `task_ledger_cli.py doctor` immediately after switching frontmatter phase, not only after implementation, so guard-recognizable headings and telemetry refs are fixed before code patches.

## ROLL-OVER

- **Archived:** not archived yet; v31 remains the current completed iteration until the next iteration bootstrap archives it.
- **STATUS.md:** updated with v31 complete status; exact commit hash is reported in final closeout from git.
- **Maturity label:** complete.
