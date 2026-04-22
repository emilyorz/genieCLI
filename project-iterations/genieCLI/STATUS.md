# genieCLI — Workflow Status

> Living dashboard. First read on every session.
> Migrated to task-ledger-cycle v2 spec on 2026-04-17. v25 closed same day.

## Active Iteration

- **Ledger:** [CURRENT.md](CURRENT.md) (v28 — partial landing, session parked at review/waiting 2026-04-22)
- **Status:** DO — T2 + T5 + T6 complete; **T1 / T3 / T4 / T7 carried over** (all blocked on localhost:8811 MCP-trino being up). No design re-ack needed — Option 1 (L1 + L3 + K=3 retry) is locked.
- **Focus:** `/trino-research` long-query handling — Baseline × N-iteration × N-run geometric blow-up resolved for the upfront gate; L1 structural-invariant loop rewrite pending live-env probe.
- **Touched features:** [trino-research](features/trino-research.md)
- **Started:** 2026-04-20
- **Last commit:** `9c5d7a6` feat(trino-research): upfront cost gate + plan_cost helper (v28 T2 + T5) — not yet pushed (Sam's hold)
- **Resume action:** `curl -s -m 3 -o /dev/null -w "%{http_code}\n" http://localhost:8811/` — if 200, pick up T1 from CURRENT.md; if 000, flag to Sam rather than start parallel work.

## Next Iteration Focus (preview from v27 retro)

Top promotes from v27's retro, with target features:

1. ⭐ P1 S — PLAN-time validator dry-run → process change (no feature doc; potentially a one-line addition to `~/.claude/skills/task-ledger-cycle/SKILL.md` PLAN section)
2. ⭐ P2 S — Hypothesis prompt structure → features/trino-research.md (extraction in `genie/skills/trino_query/research.py:260-268`)

(2 promotes — under cap of 3.)

## Active Parks

5 parks entering v28 PLAN (3 auto-dropped at v27 retro; 4 carried + 1 new):

- Ledger roll-over drag — age 2/3 — trigger: a second iteration closes >1 day after its final Todo is accepted — origin: v26-#failed-2
- Autoresearch product-value signal — age 2/3 — trigger: v28+ E2E mode decision lands AND smoke mode picked — origin: v26-#change-next-3
- E2E smoke mode labelling — age 1/3 — trigger: next time `kept=0/2` in an E2E report causes a human to suspect a product regression when it isn't — origin: v26-#change-next-1
- Cron plumbing (`E2E-REPORT.md outside repo` + `HTTP 401`) — age 1/3 — trigger: Sam actually tries to open an auto-generated E2E PR and finds the branch exists but no PR — origin: v26-#change-next-2
- Telegram allowlist plumbing — age 0/3 — trigger: next time Telegram reply errors AND the message was a status-flip ack — origin: v27-#failed-2

(Auto-dropped at v27 retro: "Display rounding hides sub-ms metrics", "`debug-mcp-tools.py` permanent home", "Always probe before patching MCP-contract assumptions" — all aged 3/3 without trigger. See `archive/v27.md` Park aging pass.)

(Auto-dropped in v26 aging pass: "Per-connector SKILL.md toggle" and "More connector-specific rules (Hive/Iceberg/Delta)" — both aged 3/3 without trigger. See `archive/v26.md` Park aging pass.)

## Theme Tracker

| Theme | Appearances | Status |
|-------|-------------|--------|
| UX polish (cards / banners / spinners / syntax highlights / output layout) | v22, v23, v24, v26 (banner fast-fail), v27 (trino-research output) | long-term — no per-instance lifecycle; v27 is a substantial new entry |
| E2E signal hygiene (what the test measures vs what the output looks like) | v27 parks (deferred) | dormant — activates when a park trigger fires |

## Feature Index

| Feature | Doc | Last touched |
|---------|-----|--------------|
| trino-research | [features/trino-research.md](features/trino-research.md) | v27 (output UX overhaul — terminal slim, iteration-centric report, `./report/` subdir); v28 candidate target (hypothesis prompt structure, if Sam picks Carryover #2) |
| mcp-banner | [features/mcp-banner.md](features/mcp-banner.md) | v26 (fast-fail 200ms probe; 14× cold-start improvement) |

## Archive

- [archive/v27.md](archive/v27.md) — `/trino-research` output UX overhaul (terminal slim + iteration-centric report + `./report/` subdir); single-commit T1-T4; hypothesis un-truncated; 641 tests pass; both v26 promotes deferred to parks at PLAN ack
- [archive/v26.md](archive/v26.md) — fast-fail MCP banner (T1 3000→211ms); smoke discipline note; v15 R3 + v10 T9 close-out; first post-hardthink-gate retro (process-gap + do-differently-next-time sections populated)
- [archive/v25.md](archive/v25.md) — metric pipeline fix (bare-list response handling + EXPLAIN ANALYZE backfill); pivoted from original async-banner focus
- All historical `TASK-LEDGER-v*.md` (v1-v24) under [archive/](archive/), naming kept for git-history clarity

## Meta-retro Log

- No meta-retro yet — first one due at v30 (5 iterations from v25 — the first under the new spec).
- Full history: [LEARNINGS.md](LEARNINGS.md)

## Notes

- `MIGRATION-MAP.md` is pre-existing v8 skill-architecture migration record — left alone, not part of the new spec.
- `scripts/debug-mcp-tools.py` is a permanent diagnostic for MCP-contract issues. Three modes: default (list tools + show resolver choice), `--probe` (raw response shape per query), `--measure SQL` (full `_measure_mcp` chain dump). Used during v25 to find the bare-list response bug; kept for future MCP server compatibility checks.
- Pre-commit hook from workspace-emily lives at workspace-level only. To wire validation in genieCLI repo: copy `bin/validate-ledger-precommit.sh` from workspace-emily and install at `.git/hooks/pre-commit`. Until then, run `python3 ~/.claude/skills/task-ledger-cycle/templates/validate_ledger.py project-iterations/genieCLI` manually before committing ledger changes.
