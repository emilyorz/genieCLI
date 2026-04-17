# TASK-LEDGER

## Basic Info

- Project: genieCLI v23 — UX sprint pt.2 (spinners, progress, inline help)
- Repo Folder: project-iterations/genieCLI/
- Iteration: 23 (3 rounds, 1 PR)
- Owner: Emily (Claude Code)
- Status: done
- Updated: 2026-04-16T23:55+0800
- Focus: Close the last "is it stuck?" gaps — live spinners for AI
  thinking, per-run progress for verify loops, inline help for the
  command itself.

## Motivation

Sam gave autonomy to keep iterating UX tonight. After v22 shipped the
structured outputs, the remaining rough edges were:

- "AI thinking..." was a static line — users had no signal whether
  the request was in flight or hung
- `_measure_mcp` ran N verify runs silently — on multi-run candidates
  there was a long dead period with no output
- `/trino-research` had no discoverable flag-level help — new users
  needed to read the code or the ledger

## Round 5 — Live spinner during AI thinking

Added `HumanSink.status(message)` returning Rich's `Console.status`
context manager (dots spinner). Matched with a `nullcontext` on
`MachineSink` for uniform call-sites. Iteration loop now does:

```python
with output.status("AI thinking..."):
    reply = provider.complete_text(req)
```

Clears automatically on exit. Respects muted palette.

## Round 6 — Per-run progress for verify loops

`_measure_mcp` now accepts `output` + `label`. When present and the
sink supports `status()`, wraps each run in a spinner showing
`baseline: run 2/3` or `iter 3 candidate: run 1/3`. Callers in
`run_mcp_enhancement` pass the label through.

Silent mode preserved when `output=None` (existing call sites that
don't opt in stay unchanged).

## Round 7 — Inline help for /trino-research

Added `--help`/`-h` handling in the chat command dispatcher. Prints
a one-screen help card:

```
/trino-research — iterative Trino SQL optimizer (via MCP)

Usage
  /trino-research [--file <path>] [--metric <m>] [--iterations <n>] [--runs <n>]
                  [--safe-limit <n>] [--query-timeout <sec>] [--direct]

Flags
  --file <path>        SQL file; prompts interactively if omitted
  --metric <m>         query_time_ms | cpu_time_ms | wall_time_ms | ...
  --iterations <n>     max optimization rounds (default 5)
  --runs <n>           runs per candidate for median (default 3)
  ...

Examples
  /trino-research --file query.sql --metric query_time_ms --iterations 5
  /trino-research --file q.sql --safe-limit 10000
  /trino-research --direct
```

## Changes

| File | Change |
|------|--------|
| `genie/output/human.py` | Added `status()` method returning Rich spinner context |
| `genie/output/machine.py` | Added no-op `status()` returning `nullcontext()` |
| `genie/skills/mcp_trino/research.py` | Wrapped AI call + `_measure_mcp` runs in `output.status()`; added `output` + `label` params to `_measure_mcp` |
| `genie/chat.py` | `--help`/`-h` branch at top of `/trino-research` handler |
| `tests/test_mcp_research.py` | New test asserting `_measure_mcp` invokes `status()` per run |

## Verification

- 629 tests pass (+1 new) — no regression
- `nullcontext` imported lazily in MachineSink — no top-level cost
- HumanSink changes don't affect any existing test; `status()` is
  additive

## Retro

- **Worked:** Duck-typing via `hasattr(output, "status")` kept the
  spinner opt-in without forcing a new interface on every sink. Any
  future sink can implement or skip.
- **Failed:** Considered using `rich.progress` for multi-step progress
  bars (baseline + candidates) but scope-creeped. Left for a future
  v24 if Sam wants richer progress visuals.
- **Change next (v24 candidates):**
  - Rich.progress multi-task bar for baseline + each iteration
  - `/help` index with jump links (`/help trino-research`)
  - Session auto-save after each iteration (resilience to crashes
    mid-loop)
  - Syntax-highlight the Original SQL rendered in the plan card
    (rich already has `Syntax("sql", ...)`)
