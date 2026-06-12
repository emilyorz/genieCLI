---
covers:
  - "genie/output/*.py"
last_synced: "df1131522263a60bac2a7a0326499f43bc63c490"
---

## Purpose

`genie/output` owns the output abstraction layer for the genieCLI application. It provides two concrete implementations of the `OutputSink` protocol (defined in `genie/core/context.py`): `HumanSink` for interactive terminal use with Rich-based formatting, and `MachineSink` for non-interactive / piped invocations that emit JSON to stdout and errors to stderr. All user-visible rendering decisions are centralised here; callers talk only to the `OutputSink` interface and stay backend-agnostic.

## Exports

### `genie/output/human.py`

```
class HumanSink
```
Rich-backed `OutputSink` for interactive terminals. Shared `_console = Console(force_terminal=True, highlight=False)` — one instance per process.

| Method | Signature | Description |
|---|---|---|
| `progress` | `(msg: str) -> None` | Dimmed status line printed inline. |
| `result` | `(data: Any) -> None` | Prints str verbatim; other types serialised via `json.dumps`. |
| `stream` | `(text: str) -> None` | Writes text without newline (for LLM streaming). |
| `error` | `(msg: str, code: int = 1) -> None` | Bold red `ERROR` prefix. |
| `table` | `(rows: list[dict], headers: list[str] \| None = None) -> None` | Box-free Rich table; cyan header; renders `(empty)` when `rows` is empty. |
| `confirm` | `(prompt: str) -> bool` | `[y/N]` interactive prompt; returns `False` on EOF/interrupt. |
| `markdown` | `(text: str) -> None` | Renders via `rich.markdown.Markdown`. |
| `print` | `(msg: str) -> None` | Raw Rich markup passthrough. |
| `rule` | `(label: str = "") -> None` | Thin horizontal rule; optionally titled. |
| `kv` | `(key: str, value: str) -> None` | Left-padded key/value row for status blocks. |
| `tool_call` | `(name: str, args: dict) -> None` | One-line dimmed tool invocation line. |
| `tool_result` | `(result: str) -> None` | First 100 chars dimmed; truncated with `...` |
| `status` | `(message: str)` | Returns a context manager — live elapsed-time spinner (threading + `rich.console.status`). |

Module-level palette constants: `ACCENT="cyan"`, `MUTED="dim"`, `WARN="yellow"`, `ERROR="red"`, `OK="green"`, `GUTTER="  "`, `RULE="─"`.

### `genie/output/machine.py`

```
class MachineSink
```
JSON-over-stdout `OutputSink` for non-interactive / `--json` mode.

| Method | Behaviour |
|---|---|
| `progress` | No-op — progress chatter suppressed. |
| `result` | `json.dumps` to stdout. |
| `stream` | `sys.stdout.write` + flush (no newline). |
| `error` | `{"error": msg, "code": code}` JSON to stderr. |
| `table` | `json.dumps(rows)` to stdout. |
| `confirm` | Always returns `True` (non-interactive). |
| `markdown` | Prints raw text to stdout. |
| `print` | Strips Rich markup tags (`re.sub(r"\[/?[^\]]*\]", "", msg)`), writes to stderr. |
| `tool_call` | `{"event": "tool_call", "tool": name, "args": args}` to stderr. |
| `tool_result` | No-op. |
| `status` | Returns `contextlib.nullcontext()` — no spinner. |

### `genie/output/__init__.py`

Empty. No re-exports; consumers import `HumanSink` and `MachineSink` directly.

## Invariants

1. **Protocol conformance** — both sinks implement every method declared in `OutputSink` (`genie/core/context.py` lines 11–21). Adding a method to the protocol requires adding it to both sinks.
2. **Shared console singleton** — `HumanSink` uses a single `_console` at module level. Do not instantiate additional `Console` objects in this module; that would break interleaved output.
3. **`status()` is always a context manager** — both implementations return a context manager from `status()`; callers use `with output.status(...)`. The `HumanSink` version uses a daemon thread for the ticker; the thread is joined with a 0.2 s timeout on exit.
4. **Machine mode: data to stdout, chatter to stderr** — `result` and `table` go to stdout; `progress`, `tool_call`, and `print` go to stderr. `tool_result` is silently dropped.
5. **No colour in machine mode** — `MachineSink.print` strips all Rich markup tags before writing; callers must not depend on markup being preserved in machine mode.
6. **`confirm` in machine mode always returns `True`** — non-interactive pipelines proceed without blocking.
7. **`HumanSink.error` does not raise or exit** — the `code` parameter is accepted for interface parity but has no effect; the caller is responsible for exit logic.

## Change log

- df1131522263a60bac2a7a0326499f43bc63c490: initial card — documents HumanSink (Rich terminal) and MachineSink (JSON/stderr) from HEAD
