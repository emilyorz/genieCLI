"""Tests for /branch <exchange-index> conversation branching."""
from __future__ import annotations

from genie.chat import _redo_stack
from genie.session.manager import new_msg, new_session


# ── helpers ───────────────────────────────────────────────────────────────────


class _CaptureSink:
    """Minimal sink that records print() calls for assertion."""

    def __init__(self) -> None:
        self.printed: list[str] = []

    def print(self, msg: str) -> None:
        self.printed.append(msg)

    def progress(self, msg: str) -> None:
        pass

    def error(self, msg: str, code: int = 1) -> None:
        self.printed.append(f"[error] {msg}")

    def kv(self, key: str, value: str) -> None:
        pass

    def rule(self, label: str = "") -> None:
        pass

    def markdown(self, text: str) -> None:
        pass


def _make_session(turns: int = 4) -> dict:
    """Return a session with `turns` real user/assistant exchange pairs."""
    session = new_session("system")
    for i in range(1, turns + 1):
        session["history"].append(new_msg("user", f"Question {i}"))
        session["history"].append(new_msg("assistant", f"Answer {i}"))
    return session


def _invoke_branch(session: dict, raw_args: list[str]) -> _CaptureSink:
    """Run the /branch handler logic (mirrors chat.py) against `session`."""
    output = _CaptureSink()
    history = session["history"]

    real_user_indices = [
        i for i, m in enumerate(history)
        if m["role"] == "user"
        and not m["content"][0]["text"].startswith("[Tool result:")
    ]

    if not raw_args:
        output.print(
            "  [dim]Usage: /branch <exchange-number>  "
            "(use /history to see exchange numbers)[/dim]"
        )
        return output

    if not real_user_indices:
        output.print("  [dim]Nothing to branch.[/dim]")
        return output

    try:
        n = int(raw_args[0])
    except ValueError:
        output.print(
            "  [red]Exchange number must be an integer. "
            "Usage: /branch <number>[/red]"
        )
        return output

    total = len(real_user_indices)
    if n < 1 or n > total:
        output.print(
            f"  [red]Exchange {n} out of range. Use 1\u2013{total}.[/red]"
        )
    elif n == total:
        output.print(f"  [dim]Already at exchange {n}. Nothing to branch.[/dim]")
    else:
        cut = real_user_indices[n]
        _redo_stack(session).clear()
        session["history"] = history[:cut]
        output.print(
            f"  [green]Branched at exchange {n}. "
            f"History trimmed to {n} exchange(s).[/green]"
        )

    return output


# ── no-arg / bad-arg ──────────────────────────────────────────────────────────


def test_branch_missing_arg_prints_usage():
    session = _make_session(3)
    sink = _invoke_branch(session, [])
    assert any("Usage" in p for p in sink.printed)


def test_branch_non_numeric_arg_prints_error():
    session = _make_session(3)
    sink = _invoke_branch(session, ["foo"])
    assert any("integer" in p for p in sink.printed)


def test_branch_zero_prints_out_of_range():
    session = _make_session(3)
    sink = _invoke_branch(session, ["0"])
    assert any("out of range" in p for p in sink.printed)


def test_branch_negative_prints_out_of_range():
    session = _make_session(3)
    sink = _invoke_branch(session, ["-1"])
    assert any("out of range" in p for p in sink.printed)


def test_branch_exceeds_total_prints_out_of_range():
    session = _make_session(3)
    sink = _invoke_branch(session, ["99"])
    assert any("out of range" in p for p in sink.printed)


# ── empty history ─────────────────────────────────────────────────────────────


def test_branch_empty_history_prints_nothing_to_branch():
    session = new_session("sys")
    sink = _invoke_branch(session, ["1"])
    assert any("Nothing to branch" in p for p in sink.printed)


# ── noop: branching at the last exchange ─────────────────────────────────────


def test_branch_last_exchange_is_noop():
    session = _make_session(4)
    original_len = len(session["history"])
    sink = _invoke_branch(session, ["4"])
    assert len(session["history"]) == original_len
    assert any("Nothing to branch" in p or "Already at exchange" in p for p in sink.printed)


# ── happy-path branching ──────────────────────────────────────────────────────


def test_branch_trims_history_correctly():
    """Branch at exchange 2 of 4 should keep exactly 2 user/assistant pairs + system."""
    session = _make_session(4)
    _invoke_branch(session, ["2"])
    # system + (user + assistant) * 2 = 5 messages
    assert len(session["history"]) == 5


def test_branch_keeps_system_message():
    session = _make_session(4)
    _invoke_branch(session, ["1"])
    system_count = sum(1 for m in session["history"] if m["role"] == "system")
    assert system_count == 1


def test_branch_keeps_correct_content():
    """After /branch 2, exchange 3 and 4 messages must not be in history."""
    session = _make_session(4)
    _invoke_branch(session, ["2"])
    texts = [m["content"][0]["text"] for m in session["history"]]
    assert "Question 1" in texts
    assert "Question 2" in texts
    assert "Question 3" not in texts
    assert "Question 4" not in texts


def test_branch_prints_confirmation():
    session = _make_session(4)
    sink = _invoke_branch(session, ["2"])
    assert any("Branched at exchange 2" in p for p in sink.printed)


def test_branch_at_first_exchange():
    """Branch at 1 keeps just the first exchange."""
    session = _make_session(4)
    _invoke_branch(session, ["1"])
    # system + user + assistant = 3
    assert len(session["history"]) == 3
    user_texts = [
        m["content"][0]["text"] for m in session["history"] if m["role"] == "user"
    ]
    assert user_texts == ["Question 1"]


# ── interaction with redo stack ───────────────────────────────────────────────


def test_branch_clears_redo_stack():
    """Branching discards any pending redo entries."""
    session = _make_session(4)
    # Simulate a prior /undo: push something onto the redo stack.
    _redo_stack(session).append([new_msg("user", "ghost"), new_msg("assistant", "ghost reply")])
    assert len(_redo_stack(session)) == 1

    _invoke_branch(session, ["2"])
    assert _redo_stack(session) == []


def test_branch_does_not_modify_redo_on_noop():
    """A no-op branch (at last exchange) must not clear the redo stack."""
    session = _make_session(3)
    _redo_stack(session).append([new_msg("user", "ghost"), new_msg("assistant", "ghost")])
    _invoke_branch(session, ["3"])  # last exchange — noop
    assert len(_redo_stack(session)) == 1


# ── tool-result messages are invisible to branch indexing ─────────────────────


def test_branch_skips_tool_result_messages_in_index():
    """Tool-result user messages don't count as exchanges."""
    session = new_session("sys")
    session["history"].append(new_msg("user", "do something"))
    session["history"].append(new_msg("assistant", '{"tool": "shell", "args": {}}'))
    session["history"].append(new_msg("user", "[Tool result: shell]\nok\n\nPlease continue."))
    session["history"].append(new_msg("assistant", "done"))
    session["history"].append(new_msg("user", "follow-up"))
    session["history"].append(new_msg("assistant", "follow-up reply"))

    # Exchange 1 = "do something", exchange 2 = "follow-up"
    _invoke_branch(session, ["1"])

    texts = [m["content"][0]["text"] for m in session["history"]]
    # Exchange 1 and its internal tool round-trip survive the branch.
    assert "do something" in texts
    assert any(t.startswith("[Tool result: shell]") for t in texts)
    # Exchange 2 is gone.
    assert "follow-up" not in texts
    assert "follow-up reply" not in texts
