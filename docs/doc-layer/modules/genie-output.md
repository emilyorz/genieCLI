---
covers:
  - "genie/output/*.py"
last_synced: "dfeabc1a64fb3dcf297942cf39e4cf5ba55f334b"
---

## Purpose

`genie/output` owns the output abstraction layer for genieCLI. It provides two concrete sink implementations — `HumanSink` for interactive terminal use (Rich-based, opinionated visual language) and `MachineSink` for piped/non-interactive use (newline-delimited JSON to stdout, errors to stderr). Callers depend only on the shared method contract; switching between human and machine output requires no logic changes upstream.

## Exports

> See exports file: /Users/leeabc/work/emilyorz/genieCLI/docs/doc-layer/exports/genie-output.md

- HumanSink: Rich console sink for interactive terminal output
- MachineSink: JSON-over-stdout sink for piped/machine consumers
- HumanSink.status: Live spinner context manager with elapsed time display
- HumanSink.tool_call: One-line dimmed display for tool invocations
- MachineSink.confirm: Always returns True (non-interactive, never blocks)
- MachineSink.error: Emits JSON error payload to stderr with exit code
- HumanSink.table: Renders boxless Rich table with cyan headers
- HumanSink.kv: Aligned key/value row for status blocks

## Invariants

- `HumanSink` uses a single shared `_console` — human.py:25 — `"_console = Console(force_terminal=True, highlight=False)"`
- `MachineSink.result` always emits to stdout — machine.py:17 — `"print(json.dumps(data, ensure_ascii=False, default=str))"`
- `MachineSink.error` always emits to stderr — machine.py:25 — `"print(payload, file=sys.stderr)"`
- `MachineSink.progress` is a no-op; progress chatter is suppressed in machine mode — machine.py:13 — `"# Swallowed — progress chatter is not useful in machine mode"`
- `MachineSink.confirm` always returns `True`; non-interactive consumers never block on input — machine.py:31 — `"# Non-interactive: always proceed"`
- `HumanSink` palette is constrained to five named constants; no ad-hoc colours elsewhere — human.py:28 — `"ACCENT = \"cyan\""`
- `HumanSink.status` updates the spinner label with elapsed seconds every 0.5 s via a daemon thread — human.py:153 — `"while not stop.wait(0.5):"`
- `MachineSink.status` is a `nullcontext` no-op; it matches `HumanSink.status` signature without spinning — machine.py:57 — `"return nullcontext()"`
- `HumanSink.table` renders with `box=None`; ASCII box borders are explicitly prohibited — human.py:72 — `"box=None,              # no ASCII box — whitespace carries the grid"`
- `HumanSink.tool_result` truncates previews at 100 chars to keep tool output a side note — human.py:124 — `"preview = result[:100] + (\"...\" if len(result) > 100 else \"\")"`
## Change log

- dfeabc1a64fb3dcf297942cf39e4cf5ba55f334b: initial card created
