---
covers:
  - "genie/output/*.py"
last_synced: "572f7ff30399bed1a1a3c230918ba037ae874272"
---

## Purpose

Provides two concrete output-sink implementations behind a shared duck-typed interface (`OutputSink`). `HumanSink` renders to an interactive terminal using Rich (single accent colour, whitespace-over-boxes discipline). `MachineSink` emits JSON to stdout and routes all non-data signals to stderr, suitable for piped / scripted consumers. Callers depend only on the common method names; the sink implementation is selected at startup and injected throughout the CLI.

## Exports

from __future__ import annotations
from contextlib import contextmanager
import: threading
import: time
from typing import Any
from rich.console import Console
from rich.markdown import Markdown
from rich.markup import escape
from rich.table import Table
class: HumanSink (human.py:42)
  method: def progress(self, msg) -> None (human.py:47)
  method: def result(self, data) -> None (human.py:50)
  method: def stream(self, text) -> None (human.py:57)
  method: def error(self, msg, code) -> None (human.py:60)
  method: def table(self, rows, headers) -> None (human.py:63)
  method: def confirm(self, prompt) -> bool (human.py:80)
  method: def markdown(self, text) -> None (human.py:87)
  method: def print(self, msg) -> None (human.py:92)
  method: def rule(self, label) -> None (human.py:95)
  method: def kv(self, key, value) -> None (human.py:104)
  method: def tool_call(self, name, args) -> None (human.py:110)
  method: def tool_result(self, result) -> None (human.py:123)
  method: def status(self, message) (human.py:128)
from __future__ import annotations
import: json
import: sys
from typing import Any
class: MachineSink (machine.py:9)
  method: def progress(self, msg) -> None (machine.py:12)
  method: def result(self, data) -> None (machine.py:16)
  method: def stream(self, text) -> None (machine.py:19)
  method: def error(self, msg, code) -> None (machine.py:23)
  method: def table(self, rows, headers) -> None (machine.py:27)
  method: def confirm(self, prompt) -> bool (machine.py:30)
  method: def markdown(self, text) -> None (machine.py:34)
  method: def print(self, msg) -> None (machine.py:38)
  method: def tool_call(self, name, args) -> None (machine.py:46)
  method: def tool_result(self, result) -> None (machine.py:50)
  method: def status(self, message) (machine.py:54)

## Invariants

- `HumanSink` uses a single module-level `Console` instance (`human.py:25`) with `highlight=False`; callers must not create their own `Console` to avoid interleaved output.
- `HumanSink.status()` returns a context manager (not `None`); the spinner ticks every 0.5 s on a daemon thread and cleans up on exit (`human.py:138–168`). `MachineSink.status()` returns `contextlib.nullcontext()` (`machine.py:54–57`) so callers can use `with sink.status(...)` unconditionally across both sinks.
- `MachineSink.progress()` is a no-op (`machine.py:12–14`); `MachineSink.tool_result()` is a no-op (`machine.py:50–52`). Do not rely on these channels for data in machine mode.
- `MachineSink.error()` writes JSON `{"error": ..., "code": ...}` to **stderr**, not stdout (`machine.py:23–25`). Stdout is reserved for data (`result`, `stream`, `table`, `markdown`).
- `MachineSink.confirm()` always returns `True` (`machine.py:30–32`) — non-interactive callers proceed unconditionally.
- `MachineSink.print()` strips Rich markup tags via regex before writing to stderr (`machine.py:38–44`); it is not a data channel.
- `HumanSink.table()` silently emits `(empty)` when `rows` is empty (`human.py:64–66`) rather than raising.
- `HumanSink.tool_result()` truncates preview to 100 characters (`human.py:124`).

## Change log

- 572f7ff30399bed1a1a3c230918ba037ae874272: fix MachineSink.tool_result line number (50, not 54); 54 is status; update Invariants citations accordingly
