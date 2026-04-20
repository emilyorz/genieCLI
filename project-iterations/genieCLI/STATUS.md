# genieCLI — Workflow Status

> Living dashboard. First read on every session.
> Migrated to task-ledger-cycle v2 spec on 2026-04-17. v25 closed same day.

## Active Iteration

- **Ledger:** [CURRENT.md](CURRENT.md) (v27)
- **Status:** PLAN — blocked on ack gate (three Open questions + production path)
- **Focus:** E2E mode disambiguation (stop misreading smoke's `kept=0` as product regression) + cron plumbing fix (E2E-REPORT.md path + gh auth)
- **Touched features:** [trino-research](features/trino-research.md)
- **Started:** 2026-04-20

## Next Iteration Focus (preview from v26 retro)

Top promotes from v26's retro, with target features:

1. ⭐ P0 S — E2E mode disambiguation → features/trino-research.md (from v26 change-next-1)
2. ⭐ P1 S — Cron plumbing fix (E2E-REPORT.md git add + gh HTTP 401) → non-product (launchd/cron wrapper; no feature doc touch)

(2 promotes — under the cap of 3.)

## Active Parks

- Display rounding hides sub-ms metrics — age 2/3 — trigger: real Trino query (not SELECT 1) shows misleading 0 in optimizer output AND user complains — origin: v25-#change-next-2
- `debug-mcp-tools.py` permanent home — age 2/3 — trigger: third time someone asks "how do I check if MCP integration is working" — origin: v25-#change-next-3
- "Always probe before patching MCP-contract assumptions" process insight — age 2/3 — trigger: meta-retro at v30 reviews v25-v30 patterns and decides whether to formalize — origin: v25-#failed-1
- Ledger roll-over drag ("closing retro doesn't happen until Sam asks for next thing") — age 1/3 — trigger: a second iteration closes >1 day after its final Todo is accepted; would force a "same-day retro or flag blocker" rule into AGENTS.md — origin: v26-#failed-2
- Autoresearch product-value signal (separate from pipeline smoke) — age 1/3 — trigger: v27's E2E mode decision lands AND smoke mode is picked; this park then activates as "build a weekly product-value run against Sam's real PBB queries" in v28 or later. If v27 picks product mode instead, this park drops as subsumed. — origin: v26-#change-next-3

(Auto-dropped in v26 aging pass: "Per-connector SKILL.md toggle" and "More connector-specific rules (Hive/Iceberg/Delta)" — both aged 3/3 without trigger. See `archive/v26.md` Park aging pass.)

## Theme Tracker

| Theme | Appearances | Status |
|-------|-------------|--------|
| UX polish (cards / banners / spinners / syntax highlights) | v22, v23, v24, v26 (banner fast-fail) | long-term — no per-instance lifecycle |
| E2E signal hygiene (what the test measures vs what the output looks like) | v27 (new — triggered by v26 failed-1) | active — watch for second instance in v28-v29 |

## Feature Index

| Feature | Doc | Last touched |
|---------|-----|--------------|
| trino-research | [features/trino-research.md](features/trino-research.md) | v26 (smoke discipline note in Limits + v15 R3 / v10 T9 close-out); v27 target (smoke semantics + e2e_mode doc) |
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
