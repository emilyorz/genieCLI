# genieCLI — Workflow Status

> Living dashboard. First read on every session.
> Migrated to task-ledger-cycle v2 spec on 2026-04-17 (second pilot after bullet-monitor).

## Active Iteration

- **Ledger:** [CURRENT.md](CURRENT.md) (v25)
- **Focus:** Async MCP banner probe — chat startup must not block on the 3s timeout
- **Touched features:** [mcp-banner](features/mcp-banner.md)
- **Started:** 2026-04-17

## Next Iteration Focus (preview from last retro)

> v24 retro under old spec — back-filled into v25's Carryover + Active Parks under new lifecycle.

## Active Parks

Items waiting for a trigger condition. Each ages by 1 per retro round; auto-drops at 3/3.

- Per-connector SKILL.md toggle (load only the connector relevant to current SQL) — age 1/3 — trigger: SKILL.md body exceeds 12KB OR measurable token-budget pressure observed in Trino enhancement runs — origin: v24-#change-next-2
- Add more connector-specific optimization rules (Hive/Iceberg/Delta) — age 1/3 — trigger: Sam runs SQL against ≥3 real tables and identifies missing rules empirically — origin: v24-#change-next-1
- v15 R3 cleanup (tests + docs) — age 1/3 — trigger: Sam's E2E run on Trino 467 confirms CPU/Memory/Input/Output metrics non-zero — origin: v15-R3 (carried since 2026-04-15)
- v10 T9 live MCP verify against Sam's localhost:8811 — age 1/3 — trigger: Sam runs live E2E end-to-end and signs off — origin: v10-T9 (carried since 2026-04-12)

## Theme Tracker

| Theme | Appearances | Status |
|-------|-------------|--------|
| UX polish (cards / banners / spinners / syntax highlights) | v22, v23, v24 | long-term — sub-items tracked at theme level, not individually |

## Feature Index

| Feature | Doc | Last touched |
|---------|-----|--------------|
| mcp-banner | [features/mcp-banner.md](features/mcp-banner.md) | v24 |
| trino-research | [features/trino-research.md](features/trino-research.md) | v24 |

## Archive

All historical iteration ledgers in [archive/](archive/) — naming kept as `TASK-LEDGER-v*.md` for git history clarity. v1 → v24 plus a v25 demo file from spec design (safe to delete).

## Meta-retro Log

- No meta-retro yet — first one due at v30 (5 iterations from v25).
- Full history: [LEARNINGS.md](LEARNINGS.md)

## Notes

- `MIGRATION-MAP.md` is pre-existing v8 skill-architecture migration record — left alone, not part of the new spec.
- Pre-commit hook from workspace-emily lives at workspace-level. To wire validation in this repo too: `ln -sf "$(pwd)/../../bin/validate-ledger-precommit.sh" .git/hooks/pre-commit` (or symlink to the workspace-emily copy).
