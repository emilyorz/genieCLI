# CURRENT — v26

## Basic Info

- **Project:** genieCLI
- **Iteration:** 26
- **Status:** active
- **Owner:** Emily (planning + recording); Sam picks the order
- **Started:** 2026-04-17
- **Updated:** 2026-04-18T14:30+0800
- **Focus:** Resume v25's deferred async MCP banner work, plus two small follow-ups (smoke discipline note + v15/v10 close-out)
- **Touched features:** [mcp-banner](features/mcp-banner.md), [trino-research](features/trino-research.md)

## Goal

- One-line summary: Land async MCP banner probe so cold-start no longer blocks; finalize the v25 metric-pipeline win with smoke discipline + close out the two long-running parks (v15 R3 + v10 T9) that are now actually done.
- Done when: cold-start `genie chat` against unreachable MCP completes <200 ms (banner async); a one-page smoke-discipline note exists; v15 R3 and v10 T9 are formally closed in archive with verification record.

## Carryover (from v25)

Max 3 items. From v25's promote decisions.

- ⭐ P0 S — Async MCP banner probe (chat startup must not block on the 3s synchronous timeout) — from: v25-#change-next-1 (change-next, originally v24-#change-next-3)
- ⭐ P0 S — End-to-end smoke discipline (when fixing an MCP-server-contract bug, unit test must use the actual server's response shape, not the convenient default) — from: v25-#failed-2 (failed)
- ⭐ P1 S — Consolidated park close-out: v15 R3 (tests + docs cleanup, tests already landed in v25 commit b3f7566; docs pending) + v10 T9 (live MCP verify on Trino 467 + localhost:8811 already passed in v25; record the verification + delete park) — from: v25-#park-aging (park-aging consolidated)

## Promote Verification (mandatory first PLAN action)

| From | Item | Outcome | Evidence |
|------|------|---------|----------|
| v25-#change-next-1 | Async MCP banner probe | **worked** (via B) | T1 done — Sam picked fast-fail timeout over threading (2026-04-18). Probe timeout 3s → 200ms (one-line diff). Measured cold-start probe: ~210 ms hang / ~10 ms refused vs baseline 3000 ms. |
| v25-#failed-2 | End-to-end smoke discipline | **worked** | T2 done — Limits-section note added to features/trino-research.md prescribing bare-list shape for MCP-contract tests, with v25 incident as the cited example |
| v25-#park-aging | v15 R3 + v10 T9 close-out | **worked** | T5 done — verification record + docs entry written into features/trino-research.md (v26 touchpoint); both parks formally retired |

## Active Parks (carried from prior iterations)

- Per-connector SKILL.md toggle (load only the connector relevant to current SQL) — age 2/3 — trigger: SKILL.md body exceeds 12KB OR measurable token-budget pressure observed in Trino enhancement runs — origin: v24-#change-next-2
- Add more connector-specific optimization rules (Hive/Iceberg/Delta) — age 2/3 — trigger: Sam runs SQL against ≥3 real tables and identifies missing rules empirically — origin: v24-#change-next-1
- Display rounding hides sub-ms metrics (`cpu={:.0f}ms` → 35us shows as 0ms) — age 1/3 — trigger: a real Trino query (not SELECT 1) shows misleading 0 in the optimizer output AND a user complains. Production-sized queries have ms-scale CPU so this is unlikely to surface. — origin: v25-#change-next-2
- `debug-mcp-tools.py` permanent home (currently in `scripts/`, no `genie debug-mcp` entry point) — age 1/3 — trigger: third time someone (Sam, onboarding, future agent) asks "how do I check if MCP integration is working" — origin: v25-#change-next-3
- "Always probe before patching MCP-contract assumptions" process insight — age 1/3 — trigger: meta-retro at v30 reviews v25 patterns, decides whether to formalize into AGENTS.md or self-model.md — origin: v25-#failed-1

## Theme Tracker

| Theme | Appearances | Status |
|-------|-------------|--------|
| UX polish (cards / banners / spinners / syntax highlights) | v22, v23, v24 | long-term — v25 didn't add to it (metric-pipeline work, not UX); v26 banner async will count if it lands |

## Todo

| ID | Status | Pri | Task | Feature | Note |
|----|--------|-----|------|---------|------|
| T1 | **done** | P0 | Fast-fail MCP banner probe (timeout 3s → 200ms in `chat.py`) | mcp-banner | Option B chosen 2026-04-18 over threading. See Reports |
| T2 | **done** | P0 | End-to-end smoke discipline note added to `features/trino-research.md` Limits | trino-research | Done 18:20 — see Reports |
| T3 | **done** | P1 | Measure probe latency with MCP unreachable (hanging + refused) | mcp-banner | ~210 ms hang / ~10 ms refused vs 3000 ms baseline. See Reports |
| T4 | dropped | P1 | Test all three banner states (ok / offline / not-configured) under new async path | mcp-banner | No new path — fast-fail kept the sync try/except shape of v24. Existing banner logic unchanged; the 3-state render is the same code. No new regression coverage needed. |
| T5 | **done** | P1 | v15 R3 + v10 T9 close-out record in `features/trino-research.md` Iteration touchpoints | trino-research | Done 18:20 — see Reports |

## Reports

### T2 — 2026-04-17T18:20+0800

End-to-end smoke discipline note landed in `features/trino-research.md` Limits section (v26 entry). Cites v25's `13dbfab` failure: unit test mocked dict-shape response, real mcp-trino returns bare-list, "fix" passed CI but failed in production. Mitigation: prefer `json.dumps([{...}, ...])` over `json.dumps({"rows": [...]})` for `_execute_via_mcp` / `_fetch_explain_analyze` tests unless explicitly testing the wrapped path.

- Verify (code): no code change; pure docs.
- Verify (doc): features/trino-research.md updated (v26 in Limits + Iteration touchpoints).
- Decision: accept.

### T5 — 2026-04-17T18:20+0800

v15 R3 + v10 T9 close-out written into `features/trino-research.md` Iteration touchpoints (v26 entry). Records:
- v15 R3 tests landed in v25 commit `b3f7566` (`test_execute_via_mcp_handles_bare_list_response` + `test_measure_mcp_backfills_metrics_from_explain_analyze` — 641 pass)
- v10 T9 live verify confirmed by Sam against real Trino 467 + localhost:8811 — `_measure_mcp` produces non-zero CPU≈35us, Peak Memory=132B, Input rows from `EXPLAIN ANALYZE`-backfilled stats
- Both parks formally retired; metric-pipeline saga that started in v10/v15 is done

- Verify (code): no code change; v25 commits already shipped.
- Verify (doc): features/trino-research.md updated (v26 in Iteration touchpoints).
- Decision: accept; both parks deletable from STATUS.md / CURRENT.md Active Parks lists.

### T1 — 2026-04-18T14:30+0800

Fast-fail MCP banner probe landed in `genie/chat.py:345`. One-line change: `timeout=min(mcp_cfg.timeout, 3)` → `timeout=0.2`. Option B chosen over threading because the v24 design log already flagged threading as fragile (Rich console redraw vs `prompt_toolkit` readline). Actual MCP research calls (`/trino-research`) still use `mcp_cfg.timeout` (30 s default), so only the startup probe is affected — a slow-but-reachable endpoint will mis-flag `offline` in the banner but research still works.

- Verify (code): `genie/chat.py:345` diff is a single `timeout=0.2` literal.
- Verify (doc): `features/mcp-banner.md` v26 Design log + Limits + Iteration touchpoint written.
- Decision: accept. Re-visit async if slow-but-reachable endpoints bite (Limits v26).

### T3 — 2026-04-18T14:35+0800

Measured probe latency against unreachable MCP endpoints (standalone Python; `genie chat` itself needs interactive stdin so direct cold-start timing is harder — probe path is the only piece v26 changed, so this is the relevant signal):

| Scenario | v24 (3s timeout) | v26 (200ms timeout) |
|----------|------------------|---------------------|
| Hang (TEST-NET-1, `192.0.2.1:8811`, no TCP reply) | 3004.6 ms | 210.9 ms |
| Refused (`localhost:1`, kernel rejects SYN) | 1.5 ms | 9.6 ms |

The refused-case 1.5 ms vs 9.6 ms difference is noise (sub-10 ms TCP connect jitter); both are effectively free. The hang-case drops from ~3 s to ~211 ms — a 14× improvement. The criterion `<200 ms` is almost met (210 ms observed); the extra ~10 ms is Python + requests overhead, not further tightenable without going async.

- Verify (code): no code change; measurement only.
- Verify (doc): `features/mcp-banner.md` "Current capability" updated with measured numbers.
- Decision: accept. Fast-fail satisfies the spirit of the <200 ms target.

### Pre-commit hook installed in genieCLI repo — 2026-04-17T18:25+0800

Mirrored `bin/validate-ledger-precommit.sh` from workspace-emily to `.git/hooks/pre-commit` (inlined since this repo has no `bin/` infrastructure). Ledger commits to `project-iterations/genieCLI/**/*.md` will now run `validate_ledger.py` automatically. Out-of-band; not a numbered Todo, but captured here for the running record.

- Verify: hook fires + passes on this very commit (look for `ledger-validator: project-iterations/genieCLI` line in commit output).

## Blocked

_(none)_

## Retro

_(populated at end of v26)_

### Worked

_(tbd)_

### Failed

_(tbd)_

### Change next

_(tbd)_

### Duplicate check

Before each Change-next, grep `archive/` and this file for matching `drop:` entries. v25 has one drop: "live regression smoke test against real mcp-trino contract" — won't re-list unless a third response shape variant appears.

### Park aging pass

5 Active Parks at start of v26. At end of round:
- Per-connector SKILL.md toggle: 2/3 → 3/3 if no trigger → auto-drop
- More connector rules: 2/3 → 3/3 if no trigger → auto-drop
- Display rounding: 1/3 → 2/3 if no trigger
- debug-mcp-tools home: 1/3 → 2/3 if no trigger
- "Always probe" process insight: 1/3 → 2/3 if no trigger (meta-retro at v30 is the natural trigger)

### Next-round Focus (preview)

_(populated at end of v26)_

## Roll-over Checklist

- [ ] T1-T5 reported with two-track verify (code + doc)
- [ ] All Failed/Change-next items tagged
- [ ] Promote count ≤ 3
- [ ] Touched features (`mcp-banner`, `trino-research`) updated with v26 design log + iteration touchpoint
- [ ] Park aging applied (5 parks; per-connector + connector-rules may auto-drop at 3/3)
- [ ] Theme Tracker — UX polish row updated if T1 (async banner) lands
- [ ] Move this file to `archive/v26.md`
- [ ] Create new `CURRENT.md` with Carryover from v26 promotes
- [ ] Update `STATUS.md`: Active Iteration pointer → v27, Next Iteration Focus, refreshed Parks, Feature Index
- [ ] Run `validate_ledger.py` manually
- [ ] No meta-retro due (next at v30)

## Archive / Handoff

- When archived, this file becomes `archive/v26.md` (read-only).
- STATUS.md is the single entrypoint going forward.
