# genieCLI — Workflow Status

> Living dashboard. First read on every session.
> Migrated to task-ledger-cycle v2 spec on 2026-04-17. v25 closed same day.

## Active Iteration

- **Ledger:** [CURRENT.md](CURRENT.md) (v26)
- **Focus:** Resume v25's deferred async MCP banner work + smoke-discipline note + v15/v10 close-out
- **Touched features:** [mcp-banner](features/mcp-banner.md), [trino-research](features/trino-research.md)
- **Started:** 2026-04-17

## Next Iteration Focus (preview from v25 retro)

Top 3 promotes from v25's retro, with target features:

1. ⭐ P0 S — Async MCP banner probe → features/mcp-banner.md (carries from v24, deferred in v25)
2. ⭐ P0 S — End-to-end smoke discipline note → features/trino-research.md (failed-tag from v25 retro)
3. ⭐ P1 S — v15 R3 + v10 T9 consolidated close-out → features/trino-research.md (park-aging promote)

## Active Parks

- Per-connector SKILL.md toggle (load only the connector relevant to current SQL) — age 2/3 — trigger: SKILL.md body exceeds 12KB OR measurable token-budget pressure observed in Trino enhancement runs — origin: v24-#change-next-2
- Add more connector-specific optimization rules (Hive/Iceberg/Delta) — age 2/3 — trigger: Sam runs SQL against ≥3 real tables and identifies missing rules empirically — origin: v24-#change-next-1
- Display rounding hides sub-ms metrics — age 1/3 — trigger: real Trino query (not SELECT 1) shows misleading 0 in optimizer output AND user complains — origin: v25-#change-next-2
- `debug-mcp-tools.py` permanent home — age 1/3 — trigger: third time someone asks "how do I check if MCP integration is working" — origin: v25-#change-next-3
- "Always probe before patching MCP-contract assumptions" process insight — age 1/3 — trigger: meta-retro at v30 reviews v25 patterns and decides whether to formalize — origin: v25-#failed-1

## Theme Tracker

| Theme | Appearances | Status |
|-------|-------------|--------|
| UX polish (cards / banners / spinners / syntax highlights) | v22, v23, v24 | long-term — sub-items tracked at theme level; v25 didn't add (metric-pipeline work) |

## Feature Index

| Feature | Doc | Last touched |
|---------|-----|--------------|
| trino-research | [features/trino-research.md](features/trino-research.md) | v25 (metric pipeline fix — bare-list response shape + EA backfill) |
| mcp-banner | [features/mcp-banner.md](features/mcp-banner.md) | v24 (sync probe added). v25 = deferred (async work moved to v26) |

## Archive

- [archive/v25.md](archive/v25.md) — metric pipeline fix (bare-list response handling + EXPLAIN ANALYZE backfill); pivoted from original async-banner focus
- All historical `TASK-LEDGER-v*.md` (v1-v24) under [archive/](archive/), naming kept for git-history clarity

## Meta-retro Log

- No meta-retro yet — first one due at v30 (5 iterations from v25 — the first under the new spec).
- Full history: [LEARNINGS.md](LEARNINGS.md)

## Notes

- `MIGRATION-MAP.md` is pre-existing v8 skill-architecture migration record — left alone, not part of the new spec.
- `scripts/debug-mcp-tools.py` is a permanent diagnostic for MCP-contract issues. Three modes: default (list tools + show resolver choice), `--probe` (raw response shape per query), `--measure SQL` (full `_measure_mcp` chain dump). Used during v25 to find the bare-list response bug; kept for future MCP server compatibility checks.
- Pre-commit hook from workspace-emily lives at workspace-level only. To wire validation in genieCLI repo: copy `bin/validate-ledger-precommit.sh` from workspace-emily and install at `.git/hooks/pre-commit`. Until then, run `python3 ~/.claude/skills/task-ledger-cycle/templates/validate_ledger.py project-iterations/genieCLI` manually before committing ledger changes.
