# genieCLI — Workflow Status

> Living dashboard. First read on every session.
> Migrated to task-ledger-cycle v2 spec on 2026-04-17. v25 closed same day.

## Active Iteration

- **Ledger:** [CURRENT.md](CURRENT.md) holds **v31** (last _formal_ strict-V3 iteration, DONE). v32 + v33 + v34 shipped as out-of-process iterations — see archives.
- **Status:** v31 (formal) + v32 + v33 + v34 (deviation iterations) all complete and on `main`. **No iteration currently active — ready to start v35.**
- **v34 (Task-Ledger-V4 pilot, `8ecced9`):** Memory-pressure threshold live wiring — per-node limit resolved from env / SHOW SESSION / 1 GiB fallback; `GENIE_TRINO_MEMORY_LIMIT_PER_NODE_BYTES` + `GENIE_TRINO_MEMORY_PRESSURE_FRACTION` env knobs; five call sites threaded; 53 new tests; **880 pass, 10 skipped, 0 failures**. Two back-edges (explore→attempt-2 caught total-vs-per-node inversion; review→develop caught nan ValueError). Full retro: [archive/v34.md](archive/v34.md). **4 residuals surfaced — see below.**
- **v33 (out-of-process, orchestrated):** MCP long-query plan-cost parity (T1, quality-verified 9.3) + dual-path real-rule_id equivalence test (T2) + memory-pressure threshold calibration **mechanism** (T3 — wired to a live limit in v34). **819 pass.** Full retro: archive/v33.md.
- **v32 (on `main` `12e954a`):** rule_id contract repair (revived v31's never-firing `REWRITE` class) + per-iteration re-diagnosis + observational direction efficacy. archive/v32.md.
- **Touched features:** [trino-research](features/trino-research.md)
- **Last commit:** v34 (`8ecced9`) → on `main`.
- **Resume action:** Start the next product change as **v35**; consume the v34 residuals below. `CURRENT.md` still reads v31 (v32/v33/v34 were out-of-process). If v35 runs via the **codex-runner under live hooks** it authors the next formal CURRENT.md; if **Claude-orchestrated**, run hooks-off + dispatched review + archive record.

## Next Iteration Focus (v35 — carryover from v34 retro)

From v34 retro (see [archive/v34.md](archive/v34.md)):

1. ⭐ P1 — **Live-cluster numeric validation (#1):** verify whether Sam's office mcp-trino (`SHOW SESSION` on port 8811) exposes `query_max_memory_per_node`, its real string format/casing, and whether `0.5 × limit` is the right production fraction. Env override is the production path until validated.
2. ⭐ P1 — **Strengthen + clean (carried from v33):** pin expected kinds for all 8 rules in the dual-path equivalence test (T2 gap); remove the dead `mcp_explain_runner is not None` sub-condition; track/​fix the partial-EXPLAIN cost distortion `(rows or 0)*(bytes or 1)` (both paths) + extract the shared plan-cost iteration core before a 3rd copy.
3. ⭐ P1 — Upgrade `validate_ledger.py` to recognize the v3 ledger schema (carried v29→v34, still open).
4. P2 — **Stale docstring on `make_query_max_run_time_sql`** (`preflight.py:332–336`): still reads "1.0×/1000ms" but actual behavior uses `CANDIDATE_TIMEOUT_HEADROOM = 2.0` / 2000ms floor.
5. P2 — **T-F-24 conditional assertion never fires** (`tests/test_per_node_memory_limit.py:588`): `if plan_cost_loop_kwargs:` silently skips because `_run_mcp_plan_cost_loop` is never entered in the current test setup; `peak_memory_limit_bytes` threading to call site 5 is correct in code but has no executable coverage.
6. P2 — **`bad_env_fallthrough` breadcrumb omits SHOW SESSION outcome**: when env var is bad AND SHOW SESSION also fails, the breadcrumb still says "falling through to SHOW SESSION" without noting 1 GiB fallback is in effect.

(3 promotes at cap; items 4–6 are v34 new minors, below cap — surface at v35 PLAN.)
**Process (carried):** strict-V3 telemetry bottlenecks ad-hoc iterations on BOTH runtimes — reserve full strict V3 for codex-runner iterations; ad-hoc work = hooks-off + dispatched review + archive record. TLV4 flat SDD pilot (v34) ran to DONE without ceremony blockage — see archive/v34.md for pilot evidence. Backlog: EXPLAIN depth (join distribution/build-side); `--direct` permanent `table_metadata=None` asymmetry; LH-PRISM predictive cost engine.

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

| Feature        | Doc                                                      | Last touched                                                                                                                                 |
| -------------- | -------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------- |
| trino-research | [features/trino-research.md](features/trino-research.md) | v33 (MCP long-query plan-cost parity + dual-path rule_id equivalence test + memory-threshold calibration mechanism; out-of-process, on main) |
| mcp-banner     | [features/mcp-banner.md](features/mcp-banner.md)         | v26 (fast-fail 200ms probe; 14× cold-start improvement)                                                                                      |

## Archive

- [archive/v34.md](archive/v34.md) — memory-pressure threshold live wiring (Task-Ledger-V4 pilot, flat 9-step SDD): per-node limit from env / SHOW SESSION / 1 GiB fallback; `GENIE_TRINO_MEMORY_LIMIT_PER_NODE_BYTES` + `GENIE_TRINO_MEMORY_PRESSURE_FRACTION` env knobs; 5 call sites threaded; 53 new tests; **880 pass**, 0 regression; commit `8ecced9`; two back-edges caught real defects (explore: total-vs-per-node inversion; review: nan ValueError)
- [archive/v33.md](archive/v33.md) — directed-loop continuation (out-of-process, hooks-off + full dispatched review): MCP long-query plan-cost parity (T1, quality 9.3) + dual-path real-rule_id equivalence test (T2) + memory-threshold calibration mechanism (T3, not yet wired to a live limit); 819 pass; orchestrator=Claude, executor=local Codex CLI, verifiers=Claude sonnet/opus
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
