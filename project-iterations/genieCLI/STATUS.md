# genieCLI — Workflow Status

> Living dashboard. First read on every session.
> Migrated to task-ledger-cycle v2 spec on 2026-04-17. v25 closed same day.

## Active Iteration

- **Ledger:** [CURRENT.md](CURRENT.md) holds **v31** (last _formal_ strict-V3 iteration, DONE). **v32 shipped as an out-of-process hotfix** — see [archive/v32.md](archive/v32.md).
- **Status:** v31 (formal) + v32 (deviation hotfix) both complete and on `main`. **No iteration currently active — ready to start v33.**
- **v32 (out-of-process, on `main` `12e954a`):** rule_id contract repair (revived v31's never-firing rule-gate `REWRITE` class) + per-iteration re-diagnosis + observational direction efficacy, both paths. **809 pass.** Built hooks-off (V3 deviation, no telemetry) after the live-hook bootstrap dead-locked three times — full retro in archive/v32.md.
- **Touched features:** [trino-research](features/trino-research.md)
- **Last commit:** `12e954a` (v32) → merged to `main` `1fc1f81`.
- **Resume action:** Start the next product change as **v33**. NOTE: `CURRENT.md` still reads v31 because v32 was out-of-process (a DONE strict CURRENT.md would require dispatch telemetry that v32 honestly never produced); v33 PLAN creates the next formal CURRENT.md and consumes the v32 residual below. For a Claude-Code-driven iteration, author CURRENT.md **atomically** OR run **hooks-off as a documented deviation** — do NOT switch activation `runtime` mid-iteration (it orphans telemetry and hard-blocks every tool).

## Next Iteration Focus (v33 — carryover from v32 retro)

From v32 retro (see [archive/v32.md](archive/v32.md)):

1. ⭐ P1 — **T3 (deferred from v32):** dual-path unmocked equivalence test using **REAL rule_ids** + close the **MCP long-query plan-cost asymmetry** (MCP runs `_measure_mcp` per iter while `--direct` uses `_run_plan_cost_loop`) + feature-doc parity.
2. ⭐ P1 — **T4 (deferred from v32):** memory-pressure threshold calibration from `query.max-memory-per-node`/session (currently hard-coded 1 GiB) + cite official Trino docs in rule files.
3. ⭐ P1 — Upgrade `validate_ledger.py` to recognize the v3 ledger schema (v29→v31 promote, still unaddressed → v3 ledgers still need blanket `--no-verify`).

(3 promotes — at cap.) Also open, below cap: MCP-path T2 efficacy **integration** test (loop-driven, not just unit+mirror); **process** — a Claude-Code V3 iteration must be authored atomically-or-hooks-off, never switching activation runtime mid-iteration. Backlog: EXPLAIN depth (join distribution/build-side); `--direct` permanent `table_metadata=None` asymmetry; LH-PRISM predictive cost engine.

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

| Theme                                                                      | Appearances                                                        | Status                                                                |
| -------------------------------------------------------------------------- | ------------------------------------------------------------------ | --------------------------------------------------------------------- |
| UX polish (cards / banners / spinners / syntax highlights / output layout) | v22, v23, v24, v26 (banner fast-fail), v27 (trino-research output) | long-term — no per-instance lifecycle; v27 is a substantial new entry |
| E2E signal hygiene (what the test measures vs what the output looks like)  | v27 parks (deferred)                                               | dormant — activates when a park trigger fires                         |

## Feature Index

| Feature        | Doc                                                      | Last touched                                                                                                                 |
| -------------- | -------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------- |
| trino-research | [features/trino-research.md](features/trino-research.md) | v32 (rule_id contract repair + per-iteration re-diagnosis + observational direction efficacy; out-of-process hotfix on main) |
| mcp-banner     | [features/mcp-banner.md](features/mcp-banner.md)         | v26 (fast-fail 200ms probe; 14× cold-start improvement)                                                                      |

## Archive

- [archive/v32.md](archive/v32.md) — directed-loop repair (out-of-process hotfix, V3 deviation): rule_id contract fix (revived v31's never-firing rule-gate `REWRITE` class) + per-iteration re-diagnosis + observational direction efficacy, both paths; 809 pass; commit `12e954a` on main
- [archive/v31.md](archive/v31.md) — rule-first pre-AI gate (BLOCK/REWRITE/ADVISE/PASS) with compact TUI + shared MCP/direct/plan-cost behavior; 799 pass; commits `bc82bdf`/`21ca729`/`f274f10`
- [archive/v29.md](archive/v29.md) — directed pre-execution diagnosis (LH-PRISM) on both paths: ranked `OptimizationDirection` injected pre-iter-1 + zero-cost long-query report (`--diagnose-only`/gate-trip) + dual-path symmetry test; C2 hypothesis-structure closed; 781 pass 0 skip; built v3-strict (label `v3-deviation`, enforcement via dispatched spec+quality verifiers not live hooks); commits `006a2f9`/`62e4503`/`4acb7c4`
- [archive/v30.md](archive/v30.md) — long-query UX + Trino optimization input refresh: long-query default, elapsed stopwatch, candidate timeout, inline reject reasons, readable TUI blocks, SQL-shape diagnosis, README references; full suite 792 pass; commits through `c354b34`
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
