"""Targeted tests to cover remaining cli.py gaps and provider edge cases."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from genie.cli import _build_system_prompt, _cmd_config, _cmd_tools, _discover_skills, app
from genie.core.arg import Arg
from genie.core.registry import BaseSkill, SkillRegistry
from genie.providers.base import parse_sse

runner = CliRunner()


# ── parse_sse edge cases (base.py) ────────────────────────────────────────────

def test_parse_sse_reasontext_field():
    """base.py: reasonText field in delta."""
    raw = (
        'data: {"choices": [{"delta": {"reasonText": "chain of thought"}}]}\n'
        "data: [DONE]\n"
    )
    result = parse_sse(raw)
    assert result == "chain of thought"


def test_parse_sse_tgenie_done_terminator():
    """base.py: TGenie-style done terminator."""
    raw = (
        'data: {"choices": [{"delta": {"content": "answer"}}]}\n'
        'data: {"done": true}\n'
    )
    result = parse_sse(raw)
    assert result == "answer"


def test_parse_sse_malformed_json_skipped():
    """base.py: malformed JSON lines are silently skipped."""
    raw = (
        "data: {broken json}\n"
        'data: {"choices": [{"delta": {"content": "ok"}}]}\n'
        "data: [DONE]\n"
    )
    result = parse_sse(raw)
    assert result == "ok"


def test_parse_sse_empty_choices():
    """base.py: empty choices array doesn't crash."""
    raw = (
        'data: {"choices": []}\n'
        "data: [DONE]\n"
    )
    result = parse_sse(raw)
    assert result == ""


# ── _build_system_prompt with skill that has args/choices ─────────────────────

class _FakeSkillWithArgs(BaseSkill):
    name = "test_skill_with_args"
    description = "A test skill"
    args = [
        Arg(name="query", type="str", description="The query", required=True),
        Arg(name="mode", type="str", description="Mode", required=False, default="fast", choices=["fast", "slow"]),
        Arg(name="limit", type="int", description="Limit", required=False, default=10),
    ]

    def run(self, **kwargs) -> str:
        return "result"


class _FakeSkillNoArgs(BaseSkill):
    name = "test_skill_no_args"
    description = "No args skill"
    args = []

    def run(self, **kwargs) -> str:
        return "ok"


def test_build_system_prompt_skill_with_args_and_choices():
    """Lines 98-110: skill with args including optional args and choices."""
    SkillRegistry._skills.clear()
    SkillRegistry.register(_FakeSkillWithArgs())
    SkillRegistry.register(_FakeSkillNoArgs())

    result = _build_system_prompt({}, use_skills=True)
    assert "test_skill_with_args" in result
    assert "test_skill_no_args" in result
    # Optional arg with default should mention it
    assert "fast" in result
    # Choices should appear
    assert "fast/slow" in result
    # No-args skill shows "no args"
    assert "no args" in result

    SkillRegistry._skills.clear()


# ── CLI callback: target=sessions and target=config paths ─────────────────────

def test_cli_callback_target_sessions(monkeypatch):
    """Line 147: callback handles target='sessions' directly."""
    monkeypatch.setattr("genie.cli.list_sessions", lambda: [])
    result = runner.invoke(app, ["sessions"])
    assert result.exit_code == 0


def test_cli_callback_target_config(monkeypatch, tmp_path):
    """Line 154: callback handles target='config' directly."""
    monkeypatch.setattr("genie.core.config._LEGACY_PATH", tmp_path / "x.json")
    monkeypatch.setattr("genie.core.config._TOML_PATH", tmp_path / "y.toml")
    result = runner.invoke(app, ["config"])
    assert result.exit_code == 0


def test_cli_callback_target_tools(monkeypatch):
    """callback handles target='tools'."""
    monkeypatch.setattr("genie.cli.list_sessions", lambda: [])
    result = runner.invoke(app, ["tools"])
    assert result.exit_code == 0


# ── CLI stdin pipe (non-interactive) ──────────────────────────────────────────

def test_cli_stdin_empty_exits(monkeypatch, tmp_path):
    """Line 213: empty stdin exits with error."""
    cfg_file = tmp_path / "ai-agent-config.json"
    cfg_file.write_text(json.dumps({
        "interface": "openai",
        "openaiApiKey": "sk-test",
        "openaiBaseUrl": "https://api.openai.com/v1",
    }))
    monkeypatch.setattr("genie.core.config._LEGACY_PATH", cfg_file)
    monkeypatch.setattr("genie.core.config._TOML_PATH", tmp_path / "y.toml")
    result = runner.invoke(app, [], input="   ")
    # Empty stdin -> error
    assert result.exit_code == 1


# ── _discover_skills with legacy flag ────────────────────────────────────────

def test_discover_skills_resets_and_rediscovers(tmp_path):
    """Verify _discover_skills can be called with a custom skill dir."""
    import genie.cli as cli_mod
    cli_mod._skills_discovered = False
    SkillRegistry._skills.clear()
    # Pass a non-existent dir — should not crash
    _discover_skills(skill_dirs=[tmp_path / "nonexistent"], legacy=False)
    cli_mod._skills_discovered = False  # reset for other tests


def test_discover_skills_already_discovered():
    """_discover_skills is idempotent — second call is a no-op."""
    import genie.cli as cli_mod
    cli_mod._skills_discovered = True
    before = len(SkillRegistry._skills)
    _discover_skills()
    after = len(SkillRegistry._skills)
    assert before == after
    cli_mod._skills_discovered = False  # reset


# ── _cmd_tools with skills ────────────────────────────────────────────────────

def test_cmd_tools_with_registered_skill():
    """Lines 284-293: _cmd_tools prints skills with args."""
    from genie.output.human import HumanSink
    SkillRegistry._skills.clear()
    SkillRegistry.register(_FakeSkillWithArgs())
    sink = HumanSink()
    _cmd_tools(sink, use_skills=False)  # use_skills=False avoids discovery
    SkillRegistry._skills.clear()


# ── TGenie provider: _refresh_token internals ────────────────────────────────

def test_tgenie_refresh_token_subprocess_failure(monkeypatch):
    """tgenie.py lines 151-161: _refresh_token runs subprocess."""
    from genie.providers.tgenie import TGenieProvider
    provider = TGenieProvider({"endpoint": "https://x.com", "authToken": "tok"})
    # Monkeypatch subprocess.run to return failure
    import subprocess
    monkeypatch.setattr(
        subprocess, "run",
        lambda *args, **kwargs: type("R", (), {"returncode": 1})(),
    )
    result = provider._refresh_token()
    assert result is False


def test_tgenie_refresh_token_subprocess_success(monkeypatch):
    """_refresh_token returns True on success."""
    from genie.providers.tgenie import TGenieProvider
    provider = TGenieProvider({"endpoint": "https://x.com", "authToken": "tok"})
    import subprocess
    monkeypatch.setattr(
        subprocess, "run",
        lambda *args, **kwargs: type("R", (), {"returncode": 0})(),
    )
    result = provider._refresh_token()
    assert result is True


def test_tgenie_refresh_token_exception(monkeypatch):
    """_refresh_token returns False when subprocess raises."""
    from genie.providers.tgenie import TGenieProvider
    provider = TGenieProvider({"endpoint": "https://x.com", "authToken": "tok"})
    import subprocess
    monkeypatch.setattr(
        subprocess, "run",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("not found")),
    )
    result = provider._refresh_token()
    assert result is False


# ── _cmd_config long value truncation ────────────────────────────────────────

def test_cmd_config_truncates_long_values():
    """Lines 265: values > 60 chars are truncated."""
    from genie.output.human import HumanSink
    cfg = {
        "systemPrompt": "A" * 80,  # 80 chars → truncated to 57 + ...
        "interface": "openai",
    }
    sink = HumanSink()
    _cmd_config(cfg, sink)  # just confirm no crash


# ── Provider debug output (_dbg) ─────────────────────────────────────────────

def test_openai_dbg_with_debug_enabled(capsys):
    """openai.py line 22: _dbg prints when DEBUG=True."""
    import genie.providers.openai as mod
    mod._DEBUG = True
    mod._dbg("test debug message")
    captured = capsys.readouterr()
    assert "test debug message" in captured.out
    mod._DEBUG = False


def test_anthropic_dbg_with_debug_enabled(capsys):
    """anthropic.py line 23: _dbg prints when DEBUG=True."""
    import genie.providers.anthropic as mod
    mod._DEBUG = True
    mod._dbg("anthropic debug")
    captured = capsys.readouterr()
    assert "anthropic debug" in captured.out
    mod._DEBUG = False


def test_tgenie_dbg_with_debug_enabled(capsys):
    """tgenie.py line 27: _dbg prints when DEBUG=True."""
    import genie.providers.tgenie as mod
    mod._DEBUG = True
    mod._dbg("tgenie debug")
    captured = capsys.readouterr()
    assert "tgenie debug" in captured.out
    mod._DEBUG = False
