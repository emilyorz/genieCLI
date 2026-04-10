"""Tests for genie.input — GenieCompleter / _build_completer() auto-complete."""
from __future__ import annotations

import re
from pathlib import Path

import pytest
from prompt_toolkit.document import Document

from genie.core.registry import BaseSkill, SkillRegistry
from genie.input import SLASH_COMMANDS, _build_completer


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
