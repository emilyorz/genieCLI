# CURRENT — v25

## Basic Info

- **Project:** genieCLI
- **Iteration:** 25
- **Status:** active
- **Owner:** Emily (Sam tests / runs at office; Emily plans / records)
- **Started:** 2026-04-17
- **Updated:** 2026-04-17T08:18+0800
- **Focus:** Async MCP banner probe — chat startup must not block on the 3s synchronous timeout
- **Touched features:** [mcp-banner](features/mcp-banner.md)

## Goal

- One-line summary: `genie chat` cold-start with an unreachable MCP endpoint completes in <200 ms (vs current ~3000 ms), while warm-start with reachable MCP still shows correct status.
- Done when: latency criterion measured; banner shows correct ok/offline/not-configured state in all three scenarios (reachable, unreachable, not-configured); 629 tests still pass.

## Carryover (from v24 — back-filled under new spec)

> v24 retro listed three Change-next items under the old format. New spec forced 1 promote + 2 parks (cap = 3 across Failed + Change-next, so room to spare; promoted only the one with proven user-felt impact).

- ⭐ P0 S — Async MCP banner probe — from: v24-#change-next-3 (change-next)
  Why: 3-second synchronous timeout on every chat startup is the most-felt UX cost. Isolated change in `chat.py`. Half-day work.

## Promote Verification (mandatory first PLAN action)

| From | Item | Outcome | Evidence |
|------|------|---------|----------|
| v24-#change-next-3 | Async MCP banner probe | still-pending | not started — this is what v25 ships |

(v24 had no formally promoted items under the old spec; this is the first round of genieCLI under the new lifecycle. Promote Verification will be substantive starting at v26.)

## Active Parks (carried from prior iterations)

- Per-connector SKILL.md toggle (load only the connector relevant to current SQL) — age 1/3 — trigger: SKILL.md body exceeds 12KB OR measurable token-budget pressure observed in Trino enhancement runs — origin: v24-#change-next-2
- Add more connector-specific optimization rules (Hive/Iceberg/Delta) — age 1/3 — trigger: Sam runs SQL against ≥3 real tables and identifies missing rules empirically — origin: v24-#change-next-1
- v15 R3 cleanup (tests + docs) — age 1/3 — trigger: Sam's E2E run on Trino 467 confirms CPU/Memory/Input/Output metrics non-zero — origin: v15-R3
- v10 T9 live MCP verify against Sam's localhost:8811 — age 1/3 — trigger: Sam runs live E2E end-to-end and signs off — origin: v10-T9

## Theme Tracker

| Theme | Appearances | Status |
|-------|-------------|--------|
| UX polish (cards / banners / spinners / syntax highlights) | v22, v23, v24 | long-term — v25 inherits this theme; no per-item lifecycle on UX micro-changes |

## Todo

| ID | Status | Pri | Task | Feature | Note |
|----|--------|-----|------|---------|------|
| T1 | pending | P0 | Refactor `chat.py` MCP banner probe to run on background thread; banner shows `mcp    checking...` immediately, updates to ok/offline when probe finishes | mcp-banner | Probe still uses 3s timeout but no longer blocks startup |
| T2 | pending | P1 | Add `--no-mcp-probe` CLI flag for users who want zero startup probe overhead | mcp-banner | Escape hatch; honors `[mcp.trino].enabled = false` already as fallback |
| T3 | pending | P0 | Measure cold-start latency: time from `genie chat` invocation to first prompt, MCP unreachable scenario | mcp-banner | Criterion: <200 ms (down from ~3000 ms) |
| T4 | pending | P1 | Test all three banner states still render correctly (ok / offline / not-configured) under the new async path | mcp-banner | Regression coverage for the refactor |

## Reports

_(empty — populated as Todos complete)_

## Blocked

_(none — Sam's office E2E test for v15/v10 is independent of v25 work)_

## Retro

_(populated at end of v25)_

### Worked

_(tbd)_

### Failed

_(tbd)_

### Change next

_(tbd)_

### Duplicate check

Before each Change-next, grep `archive/` and this file for matching `drop:` entries. Currently no `drop:` entries (v1-v24 predate the spec).

### Park aging pass

All 4 Active Parks at age 1/3 entering this round. At end of v25, any not promoted with un-fired triggers → age 2/3.

### Next-round Focus (preview)

_(populated at end of v25)_

## Roll-over Checklist

- [ ] T1, T2, T3, T4 reported with verify (code + doc tracks)
- [ ] All Failed/Change-next items tagged
- [ ] Promote count ≤ 3
- [ ] `features/mcp-banner.md` updated (Current capability + Design log + Iteration touchpoint v25)
- [ ] Park aging applied to all 4 Active Parks
- [ ] Theme Tracker UX polish row updated if v25 work qualifies as UX polish (likely yes — async banner is UX)
- [ ] Move this file to `archive/v25.md`
- [ ] Create new `CURRENT.md` with Carryover from v25 promotes
- [ ] Update `STATUS.md`: Active Iteration pointer → v26, Next Iteration Focus, refreshed Parks, Feature Index
- [ ] Run `validate_ledger.py` (workspace-emily hook will run if commit hits workspace; for genieCLI repo, run manually or symlink the hook)
- [ ] No meta-retro due (next at v30)

## Archive / Handoff

- When archived, this file becomes `archive/v25.md` (read-only).
- STATUS.md is the single entrypoint going forward. Old `MIGRATION-MAP.md` is unrelated (v8 skill-architecture history).
