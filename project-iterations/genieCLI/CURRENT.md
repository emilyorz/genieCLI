---
ledger_version: v3
ledger_hooks: enabled
execution_mode: strict-full-v3
activation_file: .task-ledger-active.json
runtime: claude-code
dispatch_adapter: native-claude-agents
phase: VERIFY
current_todo: none (T1-T4 complete)
maturity_label: light-ledger-complete
---
# CURRENT — v30 (light ledger)

## Basic Info

- **Status:** complete — light ledger T1-T4 for long-query default, elapsed timer, candidate timeout, inline reject reasons, and readable TUI layout
- **Started:** 2026-05-28
- **Updated:** 2026-05-28T00:00+0800
- **Predecessor:** [archive/v29.md](archive/v29.md) — directed pre-execution diagnosis (LH-PRISM) on both paths + zero-cost long-query report; 781 pass 0 skip; v3-deviation label

## PLAN

> Prioritize responding quickly — this is a light-weight ledger entry for a small user-facing tuning UX fix, not a full V3 strict run.

### Light Ledger T1 — Long-query default + elapsed timer

**Goal:** `/trino-research` should keep tuning long queries by default, because the normal use case is expensive SQL tuning.

**Scope:**

- Default long-query behavior to proceed with tuning.
- Keep an explicit opt-out (`--no-long-query`) for diagnosis-only behavior after a slow baseline.
- Show elapsed time while long-running baseline / candidate / verify runs are active.
- Update README + feature doc to match behavior.

**Done criteria:**

- CLI help documents `--long-query` as default and `--no-long-query` as opt-out.
- Direct path and MCP path both default `long_query_opt_in=True`.
- Existing directed-report gate still works when `long_query_opt_in=False`.
- Human terminal status includes elapsed seconds.
- Full pytest passes.

### Light Ledger T2 — Candidate timeout at baseline wall-time

**Goal:** Once `/trino-research` enters iteration, a candidate that runs longer than the original baseline is not useful for tuning and should fail fast.

**Scope:**

- Cap MCP candidate / verify runs at baseline wall-time via Trino `query_max_run_time` plus MCP request timeout.
- Cap direct candidate / verify runs at baseline wall-time via Trino cursor `cancel()`.
- Mark timed-out candidates as `timeout_worse` / failed fallback without replacing best SQL.
- Show candidate timeout limits in status labels.

**Done criteria:**

- MCP and direct paths both derive timeout from baseline wall-time.
- Timed-out candidates never become best SQL.
- Tests cover MCP timeout propagation, direct plan-cost timeout propagation, and MCP timeout outcome.
- README + feature doc match behavior.

### Light Ledger T3 — Inline reject reason

**Goal:** MCP iteration summaries should not show a fast metric beside `REVERT` without explaining why it was rejected.

**Scope:**

- Add inline `reason="..."` to MCP iteration result summary lines.
- Pass concrete reasons for semantic drift, execution failure, timeout, no-SQL, and not-faster outcomes.
- Keep the existing hypothesis detail line below the summary.

**Done criteria:**

- A semantic-drift `REVERT` line can show `reason="semantic_drift: row count differs: ..."` on the same line.
- Unit test covers reason rendering.
- README + feature doc match behavior.

### Light Ledger T4 — Readable iteration result layout

**Goal:** MCP iteration summaries should be scan-friendly TUI blocks, not overloaded single rows.

**Scope:**

- Split iteration result into verdict, metric/delta/elapsed, reason, and note lines.
- Keep the style consistent with HumanSink: whitespace hierarchy, no boxes, color not required for meaning.
- Preserve compact output: one small block per iteration outcome.

**Done criteria:**

- KEPT / REVERT output remains easy to identify.
- Metric, delta, elapsed, reason, and note have stable labels.
- Unit tests cover the block layout.
- README + feature doc match behavior.

### Carried promotes from v29 retro (seed, not yet scheduled)

1. ⭐ **P0 S — Test-count honesty rule** → SKILL.md / feature-doc process. Re-run pytest in-turn and quote the literal current figure for every test-count / pass-skip claim; never carry a remembered baseline. (Origin: v29 top Failed — a stale `781+10 skip` vs actual `781+0` was caught by the spec-verifier.)
2. ⭐ **P1 S — Symmetry/parity Todos require the unmocked equivalence test as a Step-6 Tkt Verify line** → SKILL.md. (Origin: v29 T3 retro-fitted the equivalence test; T2 parity code shipped a row ahead of its guard.)
3. ⭐ **P1 S — Upgrade `validate_ledger.py` to recognize the v3 ledger schema** → process. (Origin: v3 ledgers commit with blanket `--no-verify` because the v2-only validator structurally rejects `execution-mode: strict-full-v3` — fail-open latent gap.)

### Meta-retro due

v30 is the 5th iteration under the task-ledger-cycle v2 spec (v25→v30) — **first meta-retro is due this iteration** (see STATUS.md Meta-retro Log).

## Active Parks (carried into v30)

- E2E smoke mode labelling — age 2/3 — trigger: `kept=0/2` in an E2E report misread as a regression — origin: v26-#change-next-1
- Cron plumbing (E2E-REPORT outside repo + HTTP 401) — age 2/3 — trigger: Sam opens an auto-generated E2E PR, finds branch but no PR — origin: v26-#change-next-2
- Telegram allowlist plumbing — age 1/3 — trigger: Telegram reply errors on a status-flip ack — origin: v27-#failed-2
- Explain-runner closures untested — age 0/3 — trigger: a row-shape change in either explain runner (`_build_mcp_explain_runner` / `_direct_explain_runner`) ships a regression mocked tests miss — origin: v29-#failed-2
- Symmetry test can't compare explain-sourced axis — age 0/3 — trigger: Sam runs from a live cluster and a cross-path explain-direction divergence appears — origin: v29-#failed-3

(Dropped at v29 retro, both aged 3/3 without trigger: Ledger roll-over drag, Autoresearch product-value signal. See `archive/v29.md` Park aging pass.)

## Todos

| ID | Todo | Status | Tool | Verify |
| -- | ---- | ------ | ---- | ------ |
| T1 | Long-query default + elapsed timer | done | Codex patch + pytest | `py_compile`; 151 targeted tests; 785 full tests |
| T2 | Candidate timeout at baseline wall-time | done | Codex patch + pytest | `py_compile`; 128 targeted tests; 788 full tests |
| T3 | Inline reject reason | done | Codex patch + pytest | `py_compile`; 77 targeted tests; 788 full tests |
| T4 | Readable iteration result layout | done | Codex patch + pytest | `py_compile`; 77 targeted tests; 788 full tests |

## VERIFY

- `python -m py_compile genie/output/human.py genie/skills/trino_query/research.py genie/skills/mcp_trino/research.py genie/chat.py` — pass.
- `.venv/bin/python -m pytest tests/test_mcp_research.py tests/test_mcp_preflight.py tests/test_plan_cost_loop.py tests/test_output_human.py tests/test_zero_cost_directed_report.py -q` — 151 passed.
- `.venv/bin/python -m pytest -q` — 785 passed.
- `python3 ~/.claude/skills/task-ledger-cycle/templates/validate_ledger.py project-iterations/genieCLI` — pass.
- `python -m py_compile genie/skills/mcp_trino/preflight.py genie/skills/mcp_trino/client.py genie/skills/mcp_trino/research.py genie/skills/trino_query/research.py genie/chat.py` — pass.
- `.venv/bin/python -m pytest tests/test_mcp_preflight.py tests/test_mcp_research.py tests/test_plan_cost_loop.py tests/test_zero_cost_directed_report.py -q` — 128 passed.
- `.venv/bin/python -m pytest -q` — 788 passed.
- `python -m py_compile genie/skills/mcp_trino/research.py` — pass.
- `.venv/bin/python -m pytest tests/test_mcp_research.py tests/test_zero_cost_directed_report.py -q` — 77 passed.
- `.venv/bin/python -m pytest -q` — 788 passed.
- `python -m py_compile genie/skills/mcp_trino/research.py` — pass.
- `.venv/bin/python -m pytest tests/test_mcp_research.py tests/test_zero_cost_directed_report.py -q` — 77 passed.
- `.venv/bin/python -m pytest -q` — 788 passed.

## RETRO

- Old long-query gate did abort follow-up tuning unless `--long-query` was passed. New default is tuning-on; `--no-long-query` preserves the directed-report stop path.
- Existing HumanSink status became the single timer surface, so direct path, MCP baseline/candidates/verifies, AI thinking, and MCP EXPLAIN ANALYZE waits all show elapsed seconds without changing machine output.
- T2 tightens the cost guard from 1.2x baseline to 1.0x baseline for candidates. This matches Sam's tuning intent: a slower candidate is already a failed candidate.
- T3 fixes an output ambiguity Sam caught: a candidate can be much faster by metric but still be invalid. `REVERT` now carries the rejection reason inline.
- T4 keeps the same information but makes it scan-friendly: verdict first, numbers second, reason third.
