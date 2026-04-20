# genieCLI — Workflow Status

> Living dashboard. First read on every session.
> Migrated to task-ledger-cycle v2 spec on 2026-04-17. v25 closed same day.

## Active Iteration

- **Ledger:** [CURRENT.md](CURRENT.md) (v27)
- **Status:** DO — PLAN v2 ack'd by Sam via Telegram msg 811; T5 dropped per msg 813, executing T1→T4
- **Focus:** `/trino-research` UX overhaul — terminal slim (drop full Optimized-SQL dump) + iteration-centric report (one Best SQL, full hypothesis, per-iteration mini diff) + `./report/` subdir
- **Touched features:** [trino-research](features/trino-research.md)
- **Started:** 2026-04-20

## Next Iteration Focus (preview from v26 retro)

Both v26 promotes were **deferred** at v27 PLAN ack (Sam Telegram msg 807 + 809) in favor of UX overhaul. Deferred items moved to Active Parks with revival triggers; v27's own Change-next items will repopulate this section at retro.

## Active Parks

7 parks entering v27 DO (5 carried + 2 deferred from v26 promotes):

- Display rounding hides sub-ms metrics — age 2/3 — trigger: real Trino query (not SELECT 1) shows misleading 0 AND user complains — origin: v25-#change-next-2
- `debug-mcp-tools.py` permanent home — age 2/3 — trigger: third time someone asks "how do I check if MCP integration is working" — origin: v25-#change-next-3
- "Always probe before patching MCP-contract assumptions" process insight — age 2/3 — trigger: meta-retro at v30 — origin: v25-#failed-1
- Ledger roll-over drag — age 1/3 — trigger: a second iteration closes >1 day after its final Todo is accepted — origin: v26-#failed-2
- Autoresearch product-value signal — age 1/3 — trigger: v27's E2E mode decision lands AND smoke mode picked — origin: v26-#change-next-3
- E2E smoke mode labelling — age 0/3 — trigger: next time `kept=0/2` in an E2E report causes a human to suspect a product regression when it isn't — origin: v26-#change-next-1 (deferred at v27 ack)
- Cron plumbing (`E2E-REPORT.md outside repo` + `HTTP 401`) — age 0/3 — trigger: Sam actually tries to open an auto-generated E2E PR and finds the branch exists but no PR — origin: v26-#change-next-2 (deferred at v27 ack)

(Auto-dropped in v26 aging pass: "Per-connector SKILL.md toggle" and "More connector-specific rules (Hive/Iceberg/Delta)" — both aged 3/3 without trigger. See `archive/v26.md` Park aging pass.)

## Theme Tracker

| Theme | Appearances | Status |
|-------|-------------|--------|
| UX polish (cards / banners / spinners / syntax highlights / output layout) | v22, v23, v24, v26 (banner fast-fail), v27 (trino-research output) | long-term — no per-instance lifecycle; v27 is a substantial new entry |
| E2E signal hygiene (what the test measures vs what the output looks like) | v27 parks (deferred) | dormant — activates when a park trigger fires |

## Feature Index

| Feature | Doc | Last touched |
|---------|-----|--------------|
| trino-research | [features/trino-research.md](features/trino-research.md) | v26 (smoke discipline note in Limits); v27 target (output UX overhaul — terminal slim, iteration-centric report, `./report/` subdir) |
| mcp-banner | [features/mcp-banner.md](features/mcp-banner.md) | v26 (fast-fail 200ms probe; 14× cold-start improvement) |

## Archive

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
