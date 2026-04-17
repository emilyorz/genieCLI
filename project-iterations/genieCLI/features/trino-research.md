# Feature: trino-research

> The `/trino-research` slash command — iterative SQL optimizer that uses MCP-Trino (or direct driver as fallback) to test candidate rewrites against a real Trino server.

## Current capability

- Invoked from `genie chat` as `/trino-research [--file <path>] [--metric <m>] [--iterations <n>] [--runs <n>] [--safe-limit <n>] [--query-timeout <sec>] [--direct] [--help|-h]`.
- Default routing: when `[mcp.trino].enabled = true`, calls into MCP-based execution (`run_trino_research_via_mcp`); `--direct` forces the legacy direct-driver path.
- Pre-launch UX (v22): renders a Plan card showing sql / metric / iterations / verify-runs / server / safety-limit / timeout. v24 added 5-line SQL syntax highlight preview at the top of the card.
- Loop UX (v22-v23): per-iteration status block (`KEPT / WORSE / REVERT / FAIL / SKIP` color-coded) with elapsed time; SQL diff between candidates; live spinner during AI thinking; per-run progress label for verify loops.
- Post-loop UX (v22): final summary card with baseline vs best bar chart and improvement arrow.
- Help (v23 inline + v24 routing): `/trino-research --help` and `/help trino-research` both show the same help card with usage / flags / examples.
- Safety (v19): preflight gate enforces read-only whitelist + EXPLAIN-based size estimation + row cap; `--safe-limit` opt-in wraps query in `LIMIT n`; default query timeout 300s.
- Output sinks (v18+): HumanSink (Rich, colored, no emojis) by default; MachineSink (JSON) when piped.

## Design log (append-only)

- **v17:** `/trino-research` hard-requires MCP, no silent fallback. `--direct` is opt-in. Reason: silent fallback hid configuration errors; users were debugging "why is MCP not used" without knowing the path was silently swapping to direct. Alternatives considered: warn-and-fallback — rejected, warnings get lost in normal output.
- **v18:** Trino best practices moved from hardcoded Python into `mcp_trino/SKILL.md` body, injected via `parse_skill_md_body() + SkillRegistry.get_instructions(group)`. Reason: tuning the AI's optimization knowledge required code changes; SKILL.md body lets us iterate via markdown.
- **v19:** Preflight gate before any execution. Reason: v18 could OOM on huge result sets, AI could (in principle) emit DML, default 30s timeout was too short for real queries. Triple-defense: read-only whitelist + EXPLAIN size cap + opt-in `--safe-limit` wrapper.
- **v22:** UX overhaul prioritized "make every loop phase legible" over "richer output". Reason: Sam said "希望是有感的提升" — opaque progress() lines were the felt pain. Helpers stay pure (output + kwargs in, nothing else) so they're trivially unit-testable.
- **v24:** MCP status banner runs synchronously at startup with 3s timeout. Reason: simplest path to first-pass visibility; symmetric with `genie doctor`'s probe so users learn one failure mode. Alternatives considered: async background probe — rejected for v24 because Rich console redraw during prompt readline was deemed fragile (revisited in v25).
- **v25:** `_execute_via_mcp` now handles **bare-list response shape** (`[{...}, ...]`) in addition to wrapped (`{"rows": [...]}`). Reason: mcp-trino's actual contract returns a bare JSON list; the previous `data.get("rows", []) if isinstance(data, dict)` silently dropped to empty rows on every query. Side-effect bug fixed at the same time: optimizer's `capture_rows=True` equivalence check was always comparing `[]` vs `[]` → silently treating different SQL outputs as equivalent. Alternatives considered: require all MCP servers to wrap responses — rejected, no standard exists; servers in the wild use both shapes.
- **v25:** `_measure_mcp` now backfills server-side metrics from `EXPLAIN ANALYZE` text when the server's response carries no structured `metrics` dict. Reason: mcp-trino executes the SQL but returns only rows, so `cpu_time_ms`, `peak_memory_bytes`, `processed_rows` were always 0 and the optimizer couldn't rank candidates by anything except wall-clock elapsed. The existing `_parse_explain_stages` already knew how to extract metrics from Trino's text output; reuse it. Cost: 2N queries instead of N when backfill triggers. Alternatives considered: require MCP servers to populate metrics dict — rejected, can't change contract for servers we don't own; transparent backfill keeps both shapes working.

## Limits (append-only)

- **v17:** Direct-driver path retained but de-prioritized — only invoked under explicit `--direct` flag. Reason: maintaining two execution paths is testing burden. Revisit when: MCP becomes 100% reliable in production AND zero users need direct mode.
- **v18:** SKILL.md body limit not enforced — currently 6KB after v24 expansion. Reason: no measured token-budget pressure yet. Revisit when: SKILL.md body exceeds 12KB OR Trino enhancement runs show truncation.
- **v22:** SQL diff capped at 20 lines with truncation note. Reason: terminal real estate; longer diffs become unscrollable. Revisit if users ask for `--full-diff` flag.
- **v24:** MCP banner probe is synchronous, blocks chat startup for ~3000 ms when MCP is unreachable. Reason: see v24 design log. Revisit: **v25 in flight** — async refactor.

## Open questions

- Does the AI's hypothesis extraction (first non-code line) need a more structured prompt? Currently brittle — sometimes the AI buries the hypothesis in code comments.
- Should the report's Performance Comparison table also gain ASCII bars (currently only the live summary card has them)?
- Is there a configurable "give up early" rule for the iteration loop? Right now it always runs N iterations even if rounds 1-2 already converged.

## Iteration touchpoints

- **v17:** MCP hard-requirement; `--direct` opt-in; dynamic tool name + SQL parameter discovery.
- **v18:** SKILL.md body injection; Trino best practices moved to markdown.
- **v19:** Preflight gate (read-only / size estimate / row cap / safe-limit / 300s timeout default).
- **v20:** Report polish — compact EXPLAIN summary, sample-note, table-suggestions messaging, Lakehouse footer.
- **v21:** Bug fixes — paste-mode leak, null-tool guard, EXPLAIN us/ns parser.
- **v22:** UX sprint pt.1 — SQL diff, iteration status block, plan card, summary card.
- **v23:** UX sprint pt.2 — live spinner, per-run progress, inline `--help`.
- **v24:** UX sprint pt.3 — SQL preview in plan card, `/help trino-research` routing, MCP banner, SKILL.md expert content expansion.
- **v25:** Metric pipeline fix — `_execute_via_mcp` handles bare-list response shape (was silently dropping all rows on mcp-trino), `_measure_mcp` backfills server metrics from `EXPLAIN ANALYZE` text via existing `_parse_explain_stages`. Net effect: optimizer can now rank candidates by real CPU/Memory/Input rows on mcp-trino, not just wall-clock. Side-effect bug also fixed: row-equivalence check was a no-op. Validated end-to-end on Sam's Trino 467 + localhost:8811. 641 tests pass (+2 new).
