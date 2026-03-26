"""HumanSink — Rich console output with spinners, color, Markdown, and prompts."""
from __future__ import annotations

from typing import Any

from rich.console import Console
from rich.markdown import Markdown
from rich.markup import escape
from rich.table import Table

_console = Console(force_terminal=True, highlight=False)


class HumanSink:
    """OutputSink implementation for interactive terminal use."""

    def progress(self, msg: str) -> None:
        _console.print(f"  [dim]{escape(msg)}[/dim]")

    def result(self, data: Any) -> None:
        if isinstance(data, str):
            _console.print(data)
        else:
            import json
            _console.print(json.dumps(data, indent=2, ensure_ascii=False, default=str))

    def stream(self, text: str) -> None:
        _console.print(text, end="")

    def error(self, msg: str, code: int = 1) -> None:
        _console.print(f"  [red][ERROR] {escape(msg)}[/red]")

    def table(self, rows: list[dict], headers: list[str] | None = None) -> None:
        if not rows:
            _console.print("  [dim](empty)[/dim]")
            return
        cols = headers or list(rows[0].keys())
        t = Table(*cols, show_header=True, header_style="bold cyan")
        for row in rows:
            t.add_row(*[str(row.get(c, "")) for c in cols])
        _console.print(t)

    def confirm(self, prompt: str) -> bool:
        try:
            ans = input(f"  {prompt} [y/N] ").strip().lower()
            return ans in ("y", "yes")
        except (EOFError, KeyboardInterrupt):
            return False

    def markdown(self, text: str) -> None:
        _console.print(Markdown(text))

    # ── Convenience helpers used by cli.py ────────────────────────────────────

    def print(self, msg: str) -> None:
        _console.print(msg)

    def tool_call(self, name: str, args: dict) -> None:
        if args:
            args_str = ", ".join(f"{k}={repr(v)}" for k, v in args.items())
            _console.print(
                f"  [yellow][Tool] {escape(name)}[/yellow] [dim]({escape(args_str)})[/dim]"
            )
        else:
            _console.print(f"  [yellow][Tool] {escape(name)}[/yellow]")

    def tool_result(self, result: str) -> None:
        preview = result[:100] + ("..." if len(result) > 100 else "")
        _console.print(f"  [dim][Result] {escape(preview)}[/dim]")
