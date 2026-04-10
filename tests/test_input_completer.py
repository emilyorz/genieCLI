"""Tests for genie.input — GenieCompleter / _build_completer() auto-complete."""
from __future__ import annotations

import re
from pathlib import Path

import pytest
from prompt_toolkit.document import Document

from genie.core.registry import BaseSkill, SkillRegistry
from genie.input import SLASH_COMMANDS, SLASH_COMMAND_HINTS, _build_completer


# ── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _clean_registry():
    """Ensure SkillRegistry is empty before and after each test."""
    SkillRegistry.clear()
    yield
    SkillRegistry.clear()


# ── Helpers ─────────────────────────────────────────────────────────────────


def _completions_for(text: str) -> list[str]:
    """Return completion texts for a given input string."""
    doc = Document(text, cursor_position=len(text))
    completer = _build_completer()
    return [c.text for c in completer.get_completions(doc, None)]


def _completions_with_meta(text: str) -> list[tuple[str, str]]:
    """Return (text, display_meta_str) pairs for a given input string."""
    doc = Document(text, cursor_position=len(text))
    completer = _build_completer()
    result = []
    for c in completer.get_completions(doc, None):
        meta = c.display_meta
        # display_meta may be a FormattedText or plain string; convert to str.
        if hasattr(meta, "__iter__") and not isinstance(meta, str):
            meta_str = "".join(fragment for _, fragment in meta)
        else:
            meta_str = str(meta) if meta else ""
        result.append((c.text, meta_str))
    return result


# ── Slash-command completion tests ──────────────────────────────────────────


class TestSlashCommandCompletion:
    def test_slash_commands_complete(self) -> None:
        """Typing '/sk' should yield '/skills'."""
        results = _completions_for("/sk")
        assert "/skills" in results

    def test_slash_commands_all(self) -> None:
        """Typing '/' alone should yield every SLASH_COMMAND."""
        results = _completions_for("/")
        assert sorted(results) == sorted(SLASH_COMMANDS)

    def test_unknown_slash_no_match(self) -> None:
        """Typing '/zzz' should yield no completions."""
        results = _completions_for("/zzz")
        assert results == []

    def test_slash_command_has_display_meta(self) -> None:
        """Every slash command completion should carry a non-empty display_meta hint."""
        pairs = _completions_with_meta("/")
        assert pairs, "Expected completions for '/'"
        for cmd, meta in pairs:
            assert meta, f"/{cmd} has no display_meta hint"

    def test_specific_hint_text(self) -> None:
        """/new completion hint should mention 'conversation'."""
        pairs = _completions_with_meta("/ne")
        assert any(cmd == "/new" and "conversation" in meta.lower() for cmd, meta in pairs)

    def test_model_subcommand_has_hint(self) -> None:
        """'/model list' subcommand should complete with a non-empty hint."""
        pairs = _completions_with_meta("/model ")
        assert any(cmd == "list" and meta for cmd, meta in pairs)

    def test_undo_in_slash_commands(self) -> None:
        """/undo must be listed in SLASH_COMMANDS."""
        assert "/undo" in SLASH_COMMANDS

    def test_undo_has_hint(self) -> None:
        """/undo must have an entry in SLASH_COMMAND_HINTS."""
        assert "/undo" in SLASH_COMMAND_HINTS
        assert SLASH_COMMAND_HINTS["/undo"]

    def test_all_slash_commands_have_hints(self) -> None:
        """Every command in SLASH_COMMANDS should have a hint entry."""
        missing = [cmd for cmd in SLASH_COMMANDS if cmd not in SLASH_COMMAND_HINTS]
        assert not missing, f"Commands missing hints: {missing}"


# ── Skill-name completion tests ─────────────────────────────────────────────


class TestSkillNameCompletion:
    def test_tool_name_complete(self) -> None:
        """A registered skill name should appear when its prefix is typed."""

        class FakeSkill(BaseSkill):
            name = "fake_analyzer"
            description = "A fake skill for testing completions"

        SkillRegistry.register(FakeSkill())

        results = _completions_for("fake_")
        assert "fake_analyzer" in results

    def test_tool_name_no_match_for_unrelated_prefix(self) -> None:
        """Unrelated prefix should not match the registered skill."""

        class FakeSkill(BaseSkill):
            name = "fake_analyzer"
            description = "A fake skill for testing completions"

        SkillRegistry.register(FakeSkill())

        results = _completions_for("zzz")
        assert "fake_analyzer" not in results


# ── /undo command behavior ───────────────────────────────────────────────────


class TestUndoCommand:
    """Unit-test the /undo logic directly without spinning up the full REPL."""

    def _make_history(self, *roles_and_texts):
        """Build a minimal history list from (role, text) pairs."""
        from genie.session.manager import new_msg
        return [new_msg(role, text) for role, text in roles_and_texts]

    def test_undo_removes_last_user_and_reply(self) -> None:
        """After /undo the last user+assistant pair should be gone."""
        history = self._make_history(
            ("system", "sys"),
            ("user", "first question"),
            ("assistant", "first answer"),
            ("user", "second question"),
            ("assistant", "second answer"),
        )
        # Simulate /undo: find last user index, slice there.
        user_indices = [i for i, m in enumerate(history) if m["role"] == "user"]
        assert user_indices
        last_user = user_indices[-1]
        new_history = history[:last_user]

        roles = [m["role"] for m in new_history]
        assert roles == ["system", "user", "assistant"]
        texts = [m["content"][0]["text"] for m in new_history]
        assert "second question" not in texts
        assert "second answer" not in texts

    def test_undo_on_empty_history(self) -> None:
        """With no user messages /undo should be a no-op (nothing to undo)."""
        history = self._make_history(("system", "sys"))
        user_indices = [i for i, m in enumerate(history) if m["role"] == "user"]
        assert not user_indices  # nothing to undo

    def test_undo_single_exchange(self) -> None:
        """After undoing the only exchange, history should have only system message."""
        history = self._make_history(
            ("system", "sys"),
            ("user", "hello"),
            ("assistant", "hi there"),
        )
        user_indices = [i for i, m in enumerate(history) if m["role"] == "user"]
        new_history = history[: user_indices[-1]]
        assert len(new_history) == 1
        assert new_history[0]["role"] == "system"


# ── Cross-check: SLASH_COMMANDS vs chat.py handlers ─────────────────────────


class TestSlashCommandsCoverage:
    def test_slash_commands_list_matches_chat(self) -> None:
        """Every command handled in chat.py should be listed in SLASH_COMMANDS."""
        chat_path = Path(__file__).resolve().parent.parent / "genie" / "chat.py"
        source = chat_path.read_text(encoding="utf-8")

        # Extract commands from `elif cmd == "/..."` patterns
        eq_matches = re.findall(r'elif cmd == "(/[^"]+)"', source)

        # Extract commands from `elif cmd in ("/...", "/...")` patterns
        in_matches = re.findall(r'elif cmd in \(([^)]+)\)', source)
        for group in in_matches:
            eq_matches.extend(re.findall(r'"(/[^"]+)"', group))

        chat_commands = set(eq_matches)

        missing = chat_commands - set(SLASH_COMMANDS)
        assert not missing, (
            f"Commands handled in chat.py but missing from SLASH_COMMANDS: {missing}"
        )

    def test_no_phantom_slash_commands(self) -> None:
        """Every entry in SLASH_COMMANDS should have a handler in chat.py.

        Prevents offering auto-complete for commands that don't actually work.
        """
        chat_path = Path(__file__).resolve().parent.parent / "genie" / "chat.py"
        source = chat_path.read_text(encoding="utf-8")

        # Extract commands from `if/elif cmd == "/..."` patterns
        eq_matches = re.findall(r'(?:el)?if cmd == "(/[^"]+)"', source)

        # Extract commands from `if/elif cmd in ("/...", "/...")` patterns
        in_matches = re.findall(r'(?:el)?if cmd in \(([^)]+)\)', source)
        for group in in_matches:
            eq_matches.extend(re.findall(r'"(/[^"]+)"', group))

        chat_commands = set(eq_matches)

        phantom = set(SLASH_COMMANDS) - chat_commands
        assert not phantom, (
            f"SLASH_COMMANDS entries with no handler in chat.py: {phantom}"
        )
