# genieCLI — Workflow Status

> Living dashboard. First read on every session.
> Migrated to task-ledger-cycle v2 spec on 2026-04-17. v25 closed same day.

## Active Iteration

- **Ledger:** [CURRENT.md](CURRENT.md) (v30 — PLAN pending; v29 closed + archived 2026-05-27)
- **Status:** v29 CLOSED. All 4 Todos shipped + dual-verified (spec SPEC_COMPLIANT + quality >9.0). Full suite **781 pass, 0 skip**, zero regression. v30 awaiting Sam direction.
- **Focus (v29, shipped):** Directed pre-execution diagnosis (LH-PRISM-style) on BOTH paths — deterministic static/explain/metadata/runtime diagnostics → ranked `OptimizationDirection` list → injected into optimizer prompt pre-iter-1 (seeds the AI instead of blind hypothesis). Long-query abort converted to zero-cost directed Markdown report (`--diagnose-only` + gate-trip). Dual-path symmetry guarded by an unmocked equivalence test.
- **Touched features:** [trino-research](features/trino-research.md) + new `mcp_trino/pre_execution_diagnosis.py` leaf
- **Started:** 2026-04-20 (v28) → v29 2026-05-27 close
- **Last commit:** `4acb7c4` docs(trino-research): v29 T4 close-out — directed pre-execution diagnosis design log + dual-path parity docs. Earlier v29 stack: `62e4503` (T3 zero-cost report) / `006a2f9` (T2 prompt wiring + peak_memory metric) / T1 module commit.
- **Resume action:** Open v30 PLAN. Three v29-retro promotes seeded into `CURRENT.md` (test-count honesty rule, symmetry-test-as-Tkt-Verify-line, validate_ledger.py v3 upgrade). **Meta-retro due at v30** (5th iteration under v2 spec). Built under v3-strict with runtime-honesty deviation label `v3-deviation (hooks-installed-not-live, single-runtime, claude-code-only)` — real enforcement came from dispatched spec+quality verifier subagents, not live hooks.

## Next Iteration Focus (promotes from v29 retro)

Top promotes from v29's retro (at cap 3):

1. ⭐ P0 S — Test-count honesty rule → SKILL.md / feature-doc process. Re-run pytest in-turn, quote the literal current figure; never carry a remembered baseline. (Origin: stale `781+10` vs actual `781+0`, spec-verifier caught it.)
2. ⭐ P1 S — Symmetry/parity Todos require the unmocked equivalence test as a Step-6 Tkt Verify line → SKILL.md. (Origin: v29 T3 retro-fitted it; T2 parity code shipped a row ahead of its guard.)
3. ⭐ P1 S — Upgrade `validate_ledger.py` to recognize the v3 ledger schema → process. (Origin: v3 ledgers commit with blanket `--no-verify`; v2-only validator fail-opens on v3.)

(3 promotes — at cap. v27's two promotes (validator dry-run, hypothesis prompt structure) both landed in v29 — C2 hypothesis-structure folded into T2/T4; validator-dry-run subsumed by promote #3.)

## Active Parks

5 parks entering v30 PLAN (2 auto-dropped at v29 retro; 3 carried+aged + 2 new):

- E2E smoke mode labelling — age 2/3 — trigger: next time `kept=0/2` in an E2E report causes a human to suspect a product regression when it isn't — origin: v26-#change-next-1
- Cron plumbing (`E2E-REPORT.md outside repo` + `HTTP 401`) — age 2/3 — trigger: Sam actually tries to open an auto-generated E2E PR and finds the branch exists but no PR — origin: v26-#change-next-2
- Telegram allowlist plumbing — age 1/3 — trigger: next time Telegram reply errors AND the message was a status-flip ack — origin: v27-#failed-2
- Explain-runner closures untested — age 0/3 — trigger: a row-shape change in either explain runner (`_build_mcp_explain_runner` / `_direct_explain_runner`) ships a regression mocked tests miss — origin: v29-#failed-2
- Symmetry test can't compare explain-sourced axis — age 0/3 — trigger: Sam runs from a live cluster and a cross-path explain-direction divergence appears — origin: v29-#failed-3

(Auto-dropped at v29 retro: "Ledger roll-over drag" + "Autoresearch product-value signal" — both aged 3/3 without trigger (v29 closed same-day; no E2E decision in 4 iterations). See `archive/v29.md` Park aging pass.)

(Auto-dropped at v27 retro: "Display rounding hides sub-ms metrics", "`debug-mcp-tools.py` permanent home", "Always probe before patching MCP-contract assumptions". See `archive/v27.md`.)

(Auto-dropped in v26 aging pass: "Per-connector SKILL.md toggle" and "More connector-specific rules (Hive/Iceberg/Delta)". See `archive/v26.md`.)

## Theme Tracker

| Theme | Appearances | Status |
|-------|-------------|--------|
| UX polish (cards / banners / spinners / syntax highlights / output layout) | v22, v23, v24, v26 (banner fast-fail), v27 (trino-research output) | long-term — no per-instance lifecycle; v27 is a substantial new entry |
| E2E signal hygiene (what the test measures vs what the output looks like) | v27 parks (deferred) | dormant — activates when a park trigger fires |

## Feature Index

| Feature | Doc | Last touched |
|---------|-----|--------------|
| trino-research | [features/trino-research.md](features/trino-research.md) | v29 (directed pre-execution diagnosis on both paths — ranked `OptimizationDirection` injected pre-iter-1; zero-cost long-query report; dual-path symmetry test; C2 hypothesis-structure folded in) |
| mcp-banner | [features/mcp-banner.md](features/mcp-banner.md) | v26 (fast-fail 200ms probe; 14× cold-start improvement) |

## Archive

- [archive/v29.md](archive/v29.md) — directed pre-execution diagnosis (LH-PRISM) on both paths: ranked `OptimizationDirection` injected pre-iter-1 + zero-cost long-query report (`--diagnose-only`/gate-trip) + dual-path symmetry test; C2 hypothesis-structure closed; 781 pass 0 skip; built v3-strict (label `v3-deviation`, enforcement via dispatched spec+quality verifiers not live hooks); commits `006a2f9`/`62e4503`/`4acb7c4`
- [archive/v28.md](archive/v28.md) — sqlglot AST 8-rule engine + plan_signature + plan-cost long-query loop + no-data dispatch; 724 pass +77 tests; T1/T7 skipped (live MCP probe → Sam env); MCP no-data dispatch hotfix `bd1a97a`
- [archive/v27.md](archive/v27.md) — `/trino-research` output UX overhaul (terminal slim + iteration-centric report + `./report/` subdir); single-commit T1-T4; hypothesis un-truncated; 641 tests pass; both v26 promotes deferred to parks at PLAN ack
- [archive/v26.md](archive/v26.md) — fast-fail MCP banner (T1 3000→211ms); smoke discipline note; v15 R3 + v10 T9 close-out; first post-hardthink-gate retro (process-gap + do-differently-next-time sections populated)
- [archive/v25.md](archive/v25.md) — metric pipeline fix (bare-list response handling + EXPLAIN ANALYZE backfill); pivoted from original async-banner focus
- All historical `TASK-LEDGER-v*.md` (v1-v24) under [archive/](archive/), naming kept for git-history clarity

## Meta-retro Log

- **First meta-retro DUE at v30** (5 iterations from v25 — the first under the new spec). v29 closed 2026-05-27; v30 PLAN must include the meta-retro pass.
- Full history: [LEARNINGS.md](LEARNINGS.md)

## Notes

- `MIGRATION-MAP.md` is pre-existing v8 skill-architecture migration record — left alone, not part of the new spec.
- `scripts/debug-mcp-tools.py` is a permanent diagnostic for MCP-contract issues. Three modes: default (list tools + show resolver choice), `--probe` (raw response shape per query), `--measure SQL` (full `_measure_mcp` chain dump). Used during v25 to find the bare-list response bug; kept for future MCP server compatibility checks.
- Pre-commit hook from workspace-emily lives at workspace-level only. To wire validation in genieCLI repo: copy `bin/validate-ledger-precommit.sh` from workspace-emily and install at `.git/hooks/pre-commit`. Until then, run `python3 ~/.claude/skills/task-ledger-cycle/templates/validate_ledger.py project-iterations/genieCLI` manually before committing ledger changes.
