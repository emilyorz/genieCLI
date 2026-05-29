# TASK-LEDGER

## Basic Info

- Project: genieCLI v22 — /trino-research UX sprint
- Repo Folder: project-iterations/genieCLI/
- Iteration: 22 (4 rounds bundled into one PR)
- Owner: Emily (Claude Code)
- Status: done
- Updated: 2026-04-16T23:10+0800
- Focus: Make `/trino-research` _feel_ different. Clearer pre-launch
  card, visible SQL diffs, colored iteration outcomes, structured
  final summary.

## Motivation

Sam: "GenieCLI 的迭代 目標是增加使用者體驗 user experience… 希望是有感的提升."

The loop was working correctly but the terminal experience was
opaque — plain `output.progress()` lines, no indication of what the
AI changed iteration-to-iteration, no visual improvement summary.
This sprint makes every phase of the loop legible at a glance.

## Guiding constraints

- Respect HumanSink's design language: cyan accent, green/yellow/red
  status, dim metadata, **no emojis**, gutter-aligned
- Don't break MachineSink (JSON mode) — all new helpers gate on
  `output is None`, and use existing Rich markup that MachineSink
  doesn't interpret
- Zero regression to existing tests

## Round 1 — SQL diff between iterations

Developers running an optimization loop need to see **what changed**,
not just a one-line "hypothesis". New `_render_sql_diff()` uses
`difflib.unified_diff` to show colored +/- lines between current best
and AI's proposed SQL. Caps at 20 lines + trailing truncation note.

## Round 2 — Structured iteration status block with timing

Replaces four scattered `output.progress()` calls per iteration with
one structured render:

```
KEPT   1/5  query_time_ms=0.053  Δ=-0.012  (1.24s)
       add partition filter on event_date
```

`_render_iteration_result()` color-codes status:

- KEPT (green), WORSE (yellow), REVERT (red), FAIL (red), SKIP (dim)

Each iteration tracked with `time.monotonic()` — elapsed time visible
so user knows if anything is stuck.

## Round 3 — Pre-launch research plan card

Before the baseline run, `_render_plan_card()` prints a compact
one-shot card with the full plan:

```
── Research Plan ──
sql          query.sql (45 lines, 1,234B)
metric       query_time_ms (lower is better)
iterations   5
verify       3 runs per candidate (median)
server       http://localhost:8811/mcp
safe-limit   LIMIT 1000 wrapper active
timeout      300s per query
```

User sees exactly what's about to run — SQL source + size, params,
server, safety settings. Removes "wait what's it doing?" moments.

## Round 4 — Final visual summary with improvement bar

Replaces the plain Enhancement Summary with a visual card:

```
── Final Result ──
baseline        1.0    ██████████████████████████████
best            0.4    ████████████
change         -0.6 (-60.0%) ↓
data check     PASS
iterations     5 rounds
```

Bar width scales to peak value. Arrow + color indicates improvement
direction. Data check renders PASS/FAIL with consistency reason.

## Supporting helper

`_fmt_metric_value()` — adaptive precision formatter reused by all
four helpers. Same logic as v21 PR #46's `_fmt_ms`, applied to live
output in addition to the markdown report.

## Changes

| File                                 | Change                                                                                                                                                                                                                                                                                                                      |
| ------------------------------------ | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `genie/skills/mcp_trino/research.py` | Added `_fmt_metric_value`, `_render_plan_card`, `_render_sql_diff`, `_render_iteration_result`, `_render_summary_card`. Wired into `run_mcp_enhancement` iteration loop + `run_trino_research_via_mcp` pre-launch. Replaced legacy Enhancement Summary with summary card. Removed scattered progress() lines per iteration. |
| `tests/test_mcp_research.py`         | New `TestUxHelpers` class — 8 tests covering all renderers + edge cases                                                                                                                                                                                                                                                     |

## Verification

- 628 tests pass (+8 new UX tests) — 0 regression
- Manual inspection: output is structured, colored, and one-line-per-iteration
- MachineSink path untouched (helpers noop on `output is None`, and Rich
  markup is stripped by MachineSink's plain print)

## Retro

- **Worked:** Keeping the four helpers pure (output + kwargs in, nothing
  else) made them trivially unit-testable. All 8 tests mock output and
  assert on rendered strings — no MCP or provider needed.
- **Failed:** Nothing — the tests caught a signature mismatch on
  `_render_iteration_result` early. Using mock capture was cleaner
  than asserting Rich output bytes.
- **Change next:**
  - Consider a live spinner via `rich.live` for the "AI thinking…" step
    (currently a static dim line); potential v23 scope
  - The AI's hypothesis extraction (first non-code line) is brittle —
    could be improved with a hypothesis prompt that asks for a one-line
    summary explicitly
  - Report format (markdown) could also gain an ASCII bar in the
    Performance Comparison table — currently only the live summary has it
