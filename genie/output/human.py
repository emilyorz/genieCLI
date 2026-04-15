"""HumanSink — Rich console output with a distilled visual language.

Design principles:
  * One accent colour (cyan). Warnings use yellow, errors red. Nothing else.
  * Whitespace over boxes. Two-space left gutter in normal output.
  * Hierarchy from bold + dim, not from boxes and colour vomit.
  * Lower-case labels. No emojis, no decorative bullets.
  * Messages are terse; the content carries the weight.
  * One exception: the interactive startup banner (see chat._render_banner)
    uses ASCII-art branding — it's the single "ritual" moment of the CLI.
"""
from __future__ import annotations

from typing import Any

from rich.console import Console
from rich.markdown import Markdown
from rich.markup import escape
from rich.table import Table

# Single shared console. Highlighting off — we decide the palette, not Rich.
_console = Console(force_terminal=True, highlight=False)

# ── palette ──────────────────────────────────────────────────────────────────
ACCENT = "cyan"
MUTED = "dim"
WARN = "yellow"
ERROR = "red"
OK = "green"

# Gutter every line lives behind. Two spaces = enough breathing room to be
# visually distinct from the prompt without looking indented like code.
GUTTER = "  "

# Single-column rule used instead of boxed banners.
RULE = "─"


class HumanSink:
    """OutputSink implementation for interactive terminal use."""

    # ── primary channels ─────────────────────────────────────────────────────

    def progress(self, msg: str) -> None:
        _console.print(f"{GUTTER}[{MUTED}]{escape(msg)}[/{MUTED}]")

    def result(self, data: Any) -> None:
        if isinstance(data, str):
            _console.print(data)
        else:
            import json
            _console.print(json.dumps(data, indent=2, ensure_ascii=False, default=str))

    def stream(self, text: str) -> None:
        _console.print(text, end="")

    def error(self, msg: str, code: int = 1) -> None:
        _console.print(f"{GUTTER}[bold {ERROR}]ERROR[/bold {ERROR}]  {escape(msg)}")

    def table(self, rows: list[dict], headers: list[str] | None = None) -> None:
        if not rows:
            _console.print(f"{GUTTER}[{MUTED}](empty)[/{MUTED}]")
            return
        cols = headers or list(rows[0].keys())
        t = Table(
            *cols,
            show_header=True,
            header_style=f"bold {ACCENT}",
            box=None,              # no ASCII box — whitespace carries the grid
            padding=(0, 2),
            show_edge=False,
        )
        for row in rows:
            t.add_row(*[str(row.get(c, "")) for c in cols])
        _console.print(t)

    def confirm(self, prompt: str) -> bool:
        try:
            ans = input(f"{GUTTER}{prompt} [y/N] ").strip().lower()
            return ans in ("y", "yes")
        except (EOFError, KeyboardInterrupt):
            return False

    def markdown(self, text: str) -> None:
        _console.print(Markdown(text))

    # ── convenience ──────────────────────────────────────────────────────────

    def print(self, msg: str) -> None:
        _console.print(msg)

    def rule(self, label: str = "") -> None:
        """Thin horizontal rule. Replaces the old ASCII `+===+` banner."""
        if label:
            _console.print(f"{GUTTER}[{MUTED}]{RULE * 2}[/{MUTED}] "
                           f"[bold {ACCENT}]{escape(label)}[/bold {ACCENT}] "
                           f"[{MUTED}]{RULE * 20}[/{MUTED}]")
        else:
            _console.print(f"{GUTTER}[{MUTED}]{RULE * 40}[/{MUTED}]")

    def kv(self, key: str, value: str) -> None:
        """Aligned key/value row for status blocks (model, skills, trino…)."""
        _console.print(
            f"{GUTTER}[{MUTED}]{escape(key):<8}[/{MUTED}]  {escape(value)}"
        )

    def tool_call(self, name: str, args: dict) -> None:
        # Tool calls are noisy by nature — keep them one line, dimmed args.
        if args:
            args_str = ", ".join(f"{k}={v!r}" for k, v in args.items())
            _console.print(
                f"{GUTTER}[{ACCENT}]→[/{ACCENT}] [bold]{escape(name)}[/bold]"
                f"  [{MUTED}]{escape(args_str)}[/{MUTED}]"
            )
        else:
            _console.print(
                f"{GUTTER}[{ACCENT}]→[/{ACCENT}] [bold]{escape(name)}[/bold]"
            )

    def tool_result(self, result: str) -> None:
        preview = result[:100] + ("..." if len(result) > 100 else "")
        # Single-line, dimmed — tool output is a side-effect, not the story.
        _console.print(f"{GUTTER}[{MUTED}]  {escape(preview)}[/{MUTED}]")
