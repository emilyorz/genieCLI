---
ledger_version: v3
ledger_hooks: enabled
execution_mode: strict-full-v3
activation_file: .task-ledger-active.json
runtime: claude-code
dispatch_adapter: native-claude-agents
phase: PLAN
current_todo: T1
maturity_label: not-started
---
# CURRENT — v30 (PLAN pending)

## Basic Info

- **Status:** not started — awaiting Sam direction / PLAN
- **Started:** —
- **Updated:** 2026-05-27T00:00+0800 (opened at v29 roll-over)
- **Predecessor:** [archive/v29.md](archive/v29.md) — directed pre-execution diagnosis (LH-PRISM) on both paths + zero-cost long-query report; 781 pass 0 skip; v3-deviation label

## PLAN

_Awaiting Sam direction. The v29 retro promoted 3 items (cap 3) into this PLAN's seed — confirm scope with Sam before opening Todos._

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

_None yet — opened at PLAN ack._

## VERIFY

_Pending._

## RETRO

_Pending._
