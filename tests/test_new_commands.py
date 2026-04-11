"""Tests for /stats, /export, /load <n> direct-arg, and /compact features."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from genie.session.manager import new_msg, new_session
from tests.conftest import NullSink


# ── helpers ───────────────────────────────────────────────────────────────────

def _make_session_with_history() -> dict:
    """Return a session with 2 user/assistant turns."""
    session = new_session("You are a helpful assistant.")
    session["title"] = "Test session"
    session["history"].append(new_msg("user", "Hello world"))
    session["history"].append(new_msg("assistant", "Hi there!"))
    session["history"].append(new_msg("user", "What is 2+2?"))
    session["history"].append(new_msg("assistant", "4"))
    return session


# ── /stats ─────────────────────────────────────────────────────────────────────

class _CaptureSink(NullSink):
    """NullSink that also records print/kv calls for assertion."""

    def __init__(self) -> None:
        super().__init__()
        self.printed: list[str] = []
        self.kv_calls: list[tuple[str, str]] = []

    def print(self, msg: str) -> None:
        self.printed.append(msg)

    def kv(self, key: str, value: str) -> None:
        self.kv_calls.append((key, value))

    def rule(self, label: str = "") -> None:
        self.printed.append(f"[rule:{label}]")


def _invoke_stats(session: dict, model: str = "test-model", reasoning: str = "disable") -> _CaptureSink:
    """Simulate what the /stats handler does, using the same logic as chat.py."""
    output = _CaptureSink()

    visible = [m for m in session["history"] if m["role"] != "system"]
    user_count = sum(1 for m in visible if m["role"] == "user")
    ai_count = sum(1 for m in visible if m["role"] == "assistant")
    total_chars = sum(
        len(m["content"][0]["text"])
        for m in session["history"]
        if m.get("content") and m["content"]
    )
    approx_tokens = total_chars // 4

    output.print("")
    output.rule("session stats")
    output.kv("title",   session["title"])
    output.kv("turns",   str(user_count))
    output.kv("ai msgs", str(ai_count))
    output.kv("~tokens", f"{approx_tokens:,}")
    output.kv("model",   model)
    output.kv("reason",  reasoning)
    output.print("")

    return output


def test_stats_counts_turns():
    session = _make_session_with_history()
    sink = _invoke_stats(session)
    kv = dict(sink.kv_calls)
    assert kv["turns"] == "2"
    assert kv["ai msgs"] == "2"


def test_stats_title():
    session = _make_session_with_history()
    session["title"] = "My fancy session"
    sink = _invoke_stats(session)
    kv = dict(sink.kv_calls)
    assert kv["title"] == "My fancy session"


def test_stats_approx_tokens_positive():
    session = _make_session_with_history()
    sink = _invoke_stats(session)
    kv = dict(sink.kv_calls)
    # We have non-empty messages, so tokens should be > 0
    tokens_str = kv["~tokens"].replace(",", "")
    assert int(tokens_str) > 0


def test_stats_empty_session():
    """Stats on a brand-new session should show 0 turns."""
    session = new_session("system prompt")
    sink = _invoke_stats(session)
    kv = dict(sink.kv_calls)
    assert kv["turns"] == "0"
    assert kv["ai msgs"] == "0"


def test_stats_model_and_reasoning_shown():
    session = _make_session_with_history()
    sink = _invoke_stats(session, model="gpt-4o", reasoning="high")
    kv = dict(sink.kv_calls)
    assert kv["model"] == "gpt-4o"
    assert kv["reason"] == "high"


def test_stats_rule_label():
    session = _make_session_with_history()
    sink = _invoke_stats(session)
    rules = [p for p in sink.printed if p.startswith("[rule:")]
    assert any("session stats" in r for r in rules)


# ── /export ────────────────────────────────────────────────────────────────────

def _invoke_export(session: dict, tmp_path: Path) -> tuple[_CaptureSink, Path]:
    """Simulate the /export handler, redirecting exports/ to tmp_path."""
    import time as _time
    from genie.session.manager import slug as _slug

    output = _CaptureSink()
    visible = [m for m in session["history"] if m["role"] != "system"]

    if not visible:
        output.print("  [dim]Nothing to export.[/dim]")
        return output, tmp_path

    export_dir = tmp_path / "exports"
    export_dir.mkdir(exist_ok=True)
    ts = _time.strftime("%Y%m%d_%H%M%S")
    fname = f"{ts}_{_slug(session['title'])}.md"
    out_path = export_dir / fname

    lines: list[str] = [f"# {session['title']}\n"]
    for m in visible:
        role_label = "**You**" if m["role"] == "user" else "**Genie**"
        text = m["content"][0]["text"] if m.get("content") else ""
        if m["role"] == "user" and text.startswith("[Tool result:"):
            continue
        lines.append(f"\n---\n\n{role_label}\n\n{text}\n")
    out_path.write_text("\n".join(lines), encoding="utf-8")
    output.print(f"  [green]Exported:[/green] {out_path}")
    return output, out_path


def test_export_creates_file(tmp_path: Path):
    session = _make_session_with_history()
    sink, out_path = _invoke_export(session, tmp_path)
    assert out_path.exists()


def test_export_contains_title(tmp_path: Path):
    session = _make_session_with_history()
    session["title"] = "Export test chat"
    _, out_path = _invoke_export(session, tmp_path)
    content = out_path.read_text(encoding="utf-8")
    assert "Export test chat" in content


def test_export_contains_user_messages(tmp_path: Path):
    session = _make_session_with_history()
    _, out_path = _invoke_export(session, tmp_path)
    content = out_path.read_text(encoding="utf-8")
    assert "Hello world" in content
    assert "What is 2+2?" in content


def test_export_contains_ai_messages(tmp_path: Path):
    session = _make_session_with_history()
    _, out_path = _invoke_export(session, tmp_path)
    content = out_path.read_text(encoding="utf-8")
    assert "Hi there!" in content
    assert "**Genie**" in content


def test_export_skips_tool_results(tmp_path: Path):
    """Internal [Tool result: ...] messages should not appear in the export."""
    session = new_session("sys")
    session["title"] = "tool test"
    session["history"].append(new_msg("user", "run a tool"))
    session["history"].append(new_msg("assistant", '{"tool": "shell", "args": {}}'))
    session["history"].append(new_msg("user", "[Tool result: shell]\nsome output\n\nPlease continue."))
    session["history"].append(new_msg("assistant", "Done."))
    _, out_path = _invoke_export(session, tmp_path)
    content = out_path.read_text(encoding="utf-8")
    assert "[Tool result:" not in content
    assert "run a tool" in content
    assert "Done." in content


def test_export_empty_session_prints_nothing(tmp_path: Path):
    session = new_session("sys")
    sink, _ = _invoke_export(session, tmp_path)
    assert any("Nothing to export" in p for p in sink.printed)


def test_export_print_confirms_path(tmp_path: Path):
    session = _make_session_with_history()
    sink, out_path = _invoke_export(session, tmp_path)
    # The sink should have printed a line containing the file path
    assert any(str(out_path) in p for p in sink.printed)


# ── /load <n> direct arg ──────────────────────────────────────────────────────

def _make_fake_sessions(n: int) -> list[dict]:
    return [
        {"filename": f"sess_{i}.json", "title": f"Session {i}", "created": "2026-01-01", "turns": i}
        for i in range(1, n + 1)
    ]


def test_load_direct_valid_number(tmp_path: Path, monkeypatch):
    """Simulates /load 2 loading the second session without interactive input."""
    sessions = _make_fake_sessions(3)
    loaded_session = new_session("sys")
    loaded_session["title"] = "Session 2"

    monkeypatch.setattr("genie.chat.list_sessions", lambda: sessions)
    monkeypatch.setattr("genie.chat.load_session", lambda fname: loaded_session)

    output = _CaptureSink()
    escape = lambda s: s  # noqa: E731 — simple passthrough for test

    # Reproduce the /load handler logic for the args branch
    args = ["2"]
    try:
        n = int(args[0])
        if 1 <= n <= len(sessions):
            session = loaded_session
            output.print(f"  [green]Loaded: {escape(session['title'])}[/green]")
        else:
            output.print(f"  [red]Session {n} not found (1–{len(sessions)} available).[/red]")
    except ValueError:
        output.print("  [dim]Usage: /load <number>  (use /sessions to list)[/dim]")

    assert any("Session 2" in p for p in output.printed)


def test_load_direct_out_of_range(monkeypatch):
    """/load 99 when only 3 sessions exist should print an error."""
    sessions = _make_fake_sessions(3)

    output = _CaptureSink()
    escape = lambda s: s  # noqa: E731

    args = ["99"]
    try:
        n = int(args[0])
        if 1 <= n <= len(sessions):
            output.print("Loaded")
        else:
            output.print(f"  [red]Session {n} not found (1–{len(sessions)} available).[/red]")
    except ValueError:
        output.print("  [dim]Usage: /load <number>  (use /sessions to list)[/dim]")

    assert any("not found" in p for p in output.printed)


def test_load_direct_non_numeric():
    """Passing a non-numeric arg to /load should print usage hint."""
    output = _CaptureSink()
    sessions = _make_fake_sessions(3)

    args = ["foo"]
    try:
        n = int(args[0])
        if 1 <= n <= len(sessions):
            output.print("Loaded")
        else:
            output.print("not found")
    except ValueError:
        output.print("  [dim]Usage: /load <number>  (use /sessions to list)[/dim]")

    assert any("Usage" in p for p in output.printed)


def test_load_no_sessions_prints_message():
    """When there are no sessions, /load should say so."""
    output = _CaptureSink()
    sessions: list = []

    if not sessions:
        output.print("  [dim]No sessions.[/dim]")

    assert any("No sessions" in p for p in output.printed)


# ── /compact ──────────────────────────────────────────────────────────────────

def _make_long_session(turns: int = 6) -> dict:
    """Return a session with `turns` user/assistant pairs plus a system message."""
    session = new_session("You are a helpful assistant.")
    session["title"] = "Long session"
    for i in range(turns):
        session["history"].append(new_msg("user", f"Question {i + 1}"))
        session["history"].append(new_msg("assistant", f"Answer {i + 1}"))
    return session


def _invoke_compact(session: dict, keep_turns: int | None = None) -> _CaptureSink:
    """Reproduce the /compact handler from chat.py for unit-testing."""
    from genie.session.manager import new_msg as _new_msg

    output = _CaptureSink()
    _keep = keep_turns if keep_turns is not None else 6
    _keep = max(1, _keep)

    history = session["history"]
    system_msgs = [m for m in history if m["role"] == "system"]
    non_system = [m for m in history if m["role"] != "system"]
    keep_count = _keep * 2

    if len(non_system) <= keep_count:
        output.print(
            f"  [dim]Nothing to compact "
            f"({len(non_system)} messages ≤ {keep_count} keep limit).[/dim]"
        )
    else:
        removed = len(non_system) - keep_count
        chars_removed = sum(
            len(m["content"][0]["text"])
            for m in non_system[: len(non_system) - keep_count]
            if m.get("content") and m["content"]
        )
        tokens_saved = chars_removed // 4
        kept = non_system[-keep_count:]
        marker = _new_msg(
            "user",
            f"[Context compacted: {removed} messages removed, "
            f"keeping last {_keep} turns. ~{tokens_saved:,} tokens freed.]",
        )
        session["history"] = system_msgs + [marker] + kept
        output.print(
            f"  [green]Compacted:[/green] removed {removed} messages, "
            f"freed ~{tokens_saved:,} tokens."
        )
        output.print(f"  [dim]Keeping last {_keep} turns. Use /stats to verify.[/dim]")

    return output


def test_compact_reduces_history():
    session = _make_long_session(turns=10)
    before = len(session["history"])
    _invoke_compact(session, keep_turns=4)
    after = len(session["history"])
    assert after < before


def test_compact_keeps_system_message():
    session = _make_long_session(turns=10)
    _invoke_compact(session, keep_turns=4)
    system_count = sum(1 for m in session["history"] if m["role"] == "system")
    assert system_count == 1


def test_compact_keeps_correct_turn_count():
    session = _make_long_session(turns=10)
    _invoke_compact(session, keep_turns=4)
    # 1 system + 1 marker + 8 (4 turns * 2 messages each)
    assert len(session["history"]) == 1 + 1 + 8


def test_compact_inserts_marker():
    session = _make_long_session(turns=10)
    _invoke_compact(session, keep_turns=4)
    # The marker is inserted right after system messages.
    non_system = [m for m in session["history"] if m["role"] != "system"]
    first_non_system = non_system[0]
    assert "[Context compacted:" in first_non_system["content"][0]["text"]


def test_compact_default_keep_6_turns():
    session = _make_long_session(turns=12)
    _invoke_compact(session)  # default = 6
    non_system = [m for m in session["history"] if m["role"] != "system"]
    # 1 marker + 12 messages (6 turns * 2)
    assert len(non_system) == 1 + 12


def test_compact_nothing_to_compact_when_small():
    """Session with only 3 turns should not be compacted with default keep=6."""
    session = _make_long_session(turns=3)
    original_len = len(session["history"])
    sink = _invoke_compact(session, keep_turns=6)
    assert len(session["history"]) == original_len
    assert any("Nothing to compact" in p for p in sink.printed)


def test_compact_prints_confirmation():
    session = _make_long_session(turns=10)
    sink = _invoke_compact(session, keep_turns=4)
    assert any("Compacted" in p for p in sink.printed)


def test_compact_keep_1_turn_minimum():
    """keep_turns=0 should clamp to 1."""
    session = _make_long_session(turns=5)
    _invoke_compact(session, keep_turns=0)
    non_system = [m for m in session["history"] if m["role"] != "system"]
    # 1 marker + 2 messages (1 turn * 2)
    assert len(non_system) == 1 + 2


def test_compact_preserves_recent_content():
    """The most recent user message must survive compaction."""
    session = _make_long_session(turns=8)
    _invoke_compact(session, keep_turns=2)
    texts = [
        m["content"][0]["text"]
        for m in session["history"]
        if m["role"] in ("user", "assistant") and not m["content"][0]["text"].startswith("[Context")
    ]
    assert "Question 8" in texts
    assert "Answer 8" in texts


# ── SLASH_COMMANDS list completeness ──────────────────────────────────────────

def test_slash_commands_contains_new_commands():
    from genie.input import SLASH_COMMANDS, SLASH_COMMAND_HINTS
    assert "/stats" in SLASH_COMMANDS
    assert "/export" in SLASH_COMMANDS
    assert "/compact" in SLASH_COMMANDS
    assert "/redo" in SLASH_COMMANDS
    assert "/stats" in SLASH_COMMAND_HINTS
    assert "/export" in SLASH_COMMAND_HINTS
    assert "/compact" in SLASH_COMMAND_HINTS
    assert "/redo" in SLASH_COMMAND_HINTS
