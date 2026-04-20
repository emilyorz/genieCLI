# CURRENT — v28

## Basic Info

- **Project:** genieCLI
- **Iteration:** 28
- **Status:** PLAN (open — no Sam ack yet; awaiting Focus pick)
- **Owner:** Emily (planning + execution); Sam picks direction
- **Started:** 2026-04-20
- **Updated:** 2026-04-20T22:35+0800
- **Focus:** _(unset — pending Sam ack on Carryover preview vs alternative)_
- **Touched features:** _(unset)_

## Goal

- **One-line summary:** _(populated when Focus is acked)_
- **Done when:** _(populated when Focus is acked)_

## Carryover (from v27)

Max 3 items. v27 produced 2 promotes; both default to v28 unless Sam picks something else.

- ⭐ P1 S — PLAN-time validator dry-run (process change; no feature doc target unless we land it as a one-line addition to `~/.claude/skills/task-ledger-cycle/SKILL.md`)
- ⭐ P2 S — Hypothesis prompt structure → `features/trino-research.md` (extraction in `genie/skills/trino_query/research.py:260-268`)

## Promote Verification (mandatory first PLAN action)

Walk every Carryover item from v27. Outcome: `worked` (T-level evidence + commit), `failed-second-time` (back to ledger as a real failed item), or `still-relevant-defer` (one-time defer permitted; second defer must be tagged drop-or-park-with-trigger).

| From | Item | Outcome | Evidence |
|------|------|---------|----------|
| v27-#change-next-1 | PLAN-time validator dry-run | _pending — verify at v28 PLAN ack_ | _tbd_ |
| v27-#change-next-2 | Hypothesis prompt structure | _pending — verify at v28 PLAN ack_ | _tbd_ |

## Active Parks (carried from prior iterations + new from v27)

5 parks entering v28 PLAN (3 auto-dropped at v27 aging pass; 4 carried from v27 + 1 new from v27-#failed-2).

- Ledger roll-over drag — age 2/3 — trigger: a second iteration closes >1 day after final Todo accepted — origin: v26-#failed-2
- Autoresearch product-value signal — age 2/3 — trigger: v28+ E2E mode decision lands AND smoke mode picked — origin: v26-#change-next-3
- E2E smoke mode labelling — age 1/3 — trigger: next time `kept=0/2` in an E2E report causes a human to suspect a product regression when it isn't — origin: v26-#change-next-1 (deferred at v27 ack)
- Cron plumbing (`E2E-REPORT.md outside repo` + `HTTP 401`) — age 1/3 — trigger: Sam actually tries to open an auto-generated E2E PR and finds the branch exists but no PR — origin: v26-#change-next-2 (deferred at v27 ack)
- **Telegram allowlist plumbing (new)** — age 0/3 — trigger: next time Telegram reply errors AND the message was a status-flip ack ("now doing X" / "T1 done") — origin: v27-#failed-2

### Auto-dropped at v27 aging pass

- Display rounding hides sub-ms metrics — aged 3/3 without trigger (no real Trino query produced misleading 0 in v25-v27) — origin: v25-#change-next-2
- `debug-mcp-tools.py` permanent home — aged 3/3 without trigger (no third "how to check MCP" question in v25-v27; script remains in `scripts/` per existing usage) — origin: v25-#change-next-3
- "Always probe before patching MCP-contract assumptions" process insight — aged 3/3; held for meta-retro at v30 was the original plan, but a meta-retro doesn't need a parked item to surface the pattern (meta-retro reviews all retros in window). Drop here; if the insight matters, v30 meta-retro will rediscover it. — origin: v25-#failed-1

## Theme Tracker

| Theme | Appearances | Status |
|-------|-------------|--------|
| UX polish (cards / banners / spinners / syntax highlights / output layout) | v22, v23, v24, v26 (banner fast-fail), v27 (trino-research output trio) | long-term — no per-instance lifecycle |
| E2E signal hygiene (what the test measures vs what the output looks like) | v27 parks (deferred) | dormant — activates when a park trigger fires |

## Hardthink — Alternatives considered

_(populated at PLAN time once Focus is acked)_

## Hardthink — Scope

_(populated at PLAN time once Focus is acked)_

### In

_(tbd)_

### Out (explicitly deferred)

_(tbd)_

## Hardthink — Open questions

_(populated at PLAN time; if any, ack gate blocks DO)_

## Todo

| ID | Status | Pri | Task | Feature | Tool | Verify | Note |
|----|--------|-----|------|---------|------|--------|------|
| <id> | <status> | <pri> | <task> | <feature> | <tool> | <verify> | <note> |

## Reports

_(populated as Todo items complete)_

## Blocked

_(none yet)_

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

_(tbd — 8 parks entering v28; 3 at-cap items will auto-drop unless triggers fire)_

## Process gap

_(populated at end of v28)_

## Do differently next time

_(populated at end of v28)_

### Next-round Focus (preview)

_(populated at end of v28)_

## Roll-over Checklist

- [ ] Promote Verification table filled (v27 promotes verified or re-tagged)
- [ ] All Failed/Change-next items tagged
- [ ] Promote count ≤ 3
- [ ] Affected feature doc(s) updated with v28 Design log + Iteration touchpoint
- [ ] Park aging applied (8 parks entering; 3 at-cap items checked)
- [ ] Theme Tracker — relevant rows bumped
- [ ] Move this file to `archive/v28.md`
- [ ] Create new `CURRENT.md` with Carryover from v28 promotes
- [ ] Update `STATUS.md`: Active Iteration pointer → v29, Next Iteration Focus, refreshed Parks, Feature Index
- [ ] Run `scripts/validate_ledger.py` manually
- [ ] No meta-retro due (next at v30; v28 is round 4 of post-spec run)

## Archive / Handoff

- When archived, this file becomes `archive/v28.md` (read-only).
- STATUS.md is the single entrypoint going forward.
