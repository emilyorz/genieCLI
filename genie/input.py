"""genie/input.py — Terminal input helpers.

Provides interactive readline / multiline / editor input for the chat loop.
State: a single prompt_toolkit PromptSession singleton (_ps) is lazily
created and reused across all read functions in the same process.
"""
from __future__ import annotations

import os
import shlex
import subprocess
import tempfile
from pathlib import Path

_ps = None


def _get_prompt_session():
    from prompt_toolkit import PromptSession
    from prompt_toolkit.history import FileHistory

    hist = Path.home() / ".tgenie_history"
    return PromptSession(history=FileHistory(str(hist)))


def _read_input(prompt_str: str = "  You > ") -> str:
    global _ps
    if _ps is None:
        _ps = _get_prompt_session()
    try:
        return _ps.prompt(prompt_str)
    except EOFError:
        return "/exit"
    except KeyboardInterrupt:
        return ""


def _read_paste_mode() -> str:
    from prompt_toolkit.key_binding import KeyBindings
    from rich.console import Console

    kb = KeyBindings()
    _con = Console(force_terminal=True, highlight=False)

    @kb.add("c-d", eager=True)
    def _submit(event):
        event.current_buffer.validate_and_handle()

    _con.print("  [dim](paste mode — Ctrl-D to send, Ctrl-C to cancel)[/dim]")
    try:
        global _ps
        if _ps is None:
            _ps = _get_prompt_session()
        return _ps.prompt("  ... ", multiline=True, key_bindings=kb)
    except (KeyboardInterrupt, EOFError):
        return ""


def _read_editor_mode() -> str:
    from rich.console import Console
    from rich.markup import escape

    _con = Console(force_terminal=True, highlight=False)
    editor_env = os.environ.get("EDITOR", "")
    editor_parts = shlex.split(editor_env) if editor_env else []
    chosen_parts: list[str] = []
    for candidate_parts in [editor_parts, ["vim"], ["nano"]]:
        if candidate_parts and subprocess.run(
            ["which", candidate_parts[0]], capture_output=True
        ).returncode == 0:
            chosen_parts = candidate_parts
            break
    if not chosen_parts:
        _con.print("  [red][ERROR] No editor found. Set $EDITOR, or install vim/nano.[/red]")
        return ""
    with tempfile.NamedTemporaryFile(suffix=".sql", mode="w", delete=False) as f:
        tmpfile = f.name
    try:
        result = subprocess.run(chosen_parts + [tmpfile])
        if result.returncode != 0:
            return ""
        return Path(tmpfile).read_text(encoding="utf-8", errors="replace").strip()
    except Exception as exc:
        _con.print(f"  [red][ERROR] Editor failed: {escape(str(exc))}[/red]")
        return ""
    finally:
        try:
            os.unlink(tmpfile)
        except OSError:
            pass
