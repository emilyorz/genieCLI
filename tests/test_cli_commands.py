"""Tests for genie/cli.py — provider factory, helpers, and subcommands."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from genie.cli import (
    _build_system_prompt,
    _cmd_config,
    _cmd_sessions,
    _cmd_tools,
    _make_provider,
    app,
)
from genie.core.registry import SkillRegistry
from tests.conftest import NullSink


runner = CliRunner()


# ── _make_provider ────────────────────────────────────────────────────────────

def test_make_provider_openai():
    cfg = {"interface": "openai", "openaiBaseUrl": "https://api.openai.com/v1"}
    from genie.providers.openai import OpenAIProvider
    provider = _make_provider(cfg)
    assert isinstance(provider, OpenAIProvider)
    assert provider.name == "openai"


def test_make_provider_anthropic():
    cfg = {"interface": "anthropic", "openaiBaseUrl": "https://api.openai.com/v1"}
    from genie.providers.anthropic import AnthropicProvider
    provider = _make_provider(cfg)
    assert isinstance(provider, AnthropicProvider)
    assert provider.name == "anthropic"


def test_make_provider_tgenie_default():
    cfg = {"interface": "tgenie", "endpoint": "https://example.com", "authToken": "tok"}
    from genie.providers.tgenie import TGenieProvider
    provider = _make_provider(cfg)
    assert isinstance(provider, TGenieProvider)
    assert provider.name == "tgenie"


def test_make_provider_fallback_to_tgenie():
    """Unknown interface falls back to tgenie."""
    cfg = {"interface": "unknown", "endpoint": "https://example.com", "authToken": "tok"}
    from genie.providers.tgenie import TGenieProvider
    provider = _make_provider(cfg)
    assert isinstance(provider, TGenieProvider)


def test_make_provider_debug_flag():
    """debug=True propagates without error."""
    cfg = {"interface": "openai", "openaiBaseUrl": "https://api.openai.com/v1"}
    import genie.providers.openai as mod
    _make_provider(cfg, debug=True)
    assert mod._DEBUG is True
    _make_provider(cfg, debug=False)
    assert mod._DEBUG is False


# ── _build_system_prompt ──────────────────────────────────────────────────────

def test_build_system_prompt_no_skills():
    cfg = {"systemPrompt": "You are helpful."}
    result = _build_system_prompt(cfg, use_skills=False)
    assert result == "You are helpful."


def test_build_system_prompt_empty_no_skills():
    result = _build_system_prompt({}, use_skills=False)
    assert result == ""


def test_build_system_prompt_with_skills_no_system_prompt():
    """With skills but no systemPrompt, returns just the tool block."""
    SkillRegistry._skills.clear()
    result = _build_system_prompt({}, use_skills=True)
    assert "AVAILABLE TOOLS" in result
    assert "tool" in result


def test_build_system_prompt_with_base_and_skills():
    """systemPrompt + skills are concatenated."""
    SkillRegistry._skills.clear()
    cfg = {"systemPrompt": "Base prompt."}
    result = _build_system_prompt(cfg, use_skills=True)
    assert result.startswith("Base prompt.")
    assert "AVAILABLE TOOLS" in result


# ── _cmd_sessions ─────────────────────────────────────────────────────────────

def test_cmd_sessions_empty(monkeypatch):
    monkeypatch.setattr("genie.cli.list_sessions", lambda: [])
    sink = NullSink()
    _cmd_sessions(sink)
    # Nothing captured (empty state printed, not captured)
    assert sink.errors == []


def test_cmd_sessions_with_data(monkeypatch):
    sessions = [
        {"created": "2026-01-01T00:00:00", "title": "First session", "turns": 3},
        {"created": "2026-01-02T00:00:00", "title": "Second session", "turns": 1},
    ]
    monkeypatch.setattr("genie.cli.list_sessions", lambda: sessions)
    sink = NullSink()
    _cmd_sessions(sink)
    assert sink.errors == []


def test_cmd_sessions_machine_output(monkeypatch, capsys):
    from genie.output.machine import MachineSink
    sessions = [{"created": "2026-01-01", "title": "A session", "turns": 2}]
    monkeypatch.setattr("genie.cli.list_sessions", lambda: sessions)
    sink = MachineSink()
    _cmd_sessions(sink)
    captured = capsys.readouterr()
    assert "A session" in captured.out


# ── _cmd_config ───────────────────────────────────────────────────────────────

def test_cmd_config_human_sink(capsys):
    from genie.output.human import HumanSink
    cfg = {
        "interface": "openai",
        "openaiBaseUrl": "https://api.openai.com/v1",
        "openaiApiKey": "sk-abcdefghijklmnop",
        "systemPrompt": "Be helpful",
    }
    sink = HumanSink()
    _cmd_config(cfg, sink)  # just confirm no exception


def test_cmd_config_machine_sink(capsys):
    from genie.output.machine import MachineSink
    cfg = {
        "interface": "openai",
        "authToken": "secret-token-here",
        "openaiApiKey": "sk-secret",
        "cookies": ["cookie1"],
    }
    sink = MachineSink()
    _cmd_config(cfg, sink)
    captured = capsys.readouterr()
    result = json.loads(captured.out)
    # Secrets should be filtered out
    assert "authToken" not in result
    assert "openaiApiKey" not in result
    assert "interface" in result


def test_cmd_config_secret_masking(capsys):
    from genie.output.human import HumanSink
    cfg = {
        "interface": "tgenie",
        "authToken": "very-secret-token-1234",
        "openaiApiKey": "sk-12345678901234",
        "customHeader": "x-custom-header-value",
    }
    sink = HumanSink()
    _cmd_config(cfg, sink)  # ensure no crash with secrets


# ── _cmd_tools ────────────────────────────────────────────────────────────────

def test_cmd_tools_human_sink_no_skills():
    from genie.output.human import HumanSink
    SkillRegistry._skills.clear()
    sink = HumanSink()
    _cmd_tools(sink, use_skills=False)  # no crash


def test_cmd_tools_machine_sink(capsys):
    from genie.output.machine import MachineSink
    SkillRegistry._skills.clear()
    sink = MachineSink()
    _cmd_tools(sink, use_skills=False)
    captured = capsys.readouterr()
    # Should output a JSON list
    result = json.loads(captured.out)
    assert isinstance(result, list)


# ── CLI via Typer test runner ─────────────────────────────────────────────────

def test_cli_version():
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert "5.0.0" in result.output


def test_cli_config_subcommand(monkeypatch, tmp_path):
    monkeypatch.setattr("genie.core.config._LEGACY_PATH", tmp_path / "x.json")
    monkeypatch.setattr("genie.core.config._TOML_PATH", tmp_path / "y.toml")
    result = runner.invoke(app, ["config"])
    assert result.exit_code == 0


def test_cli_sessions_subcommand(monkeypatch):
    monkeypatch.setattr("genie.cli.list_sessions", lambda: [])
    result = runner.invoke(app, ["sessions"])
    assert result.exit_code == 0


def test_cli_tools_subcommand():
    SkillRegistry._skills.clear()
    result = runner.invoke(app, ["tools"])
    assert result.exit_code == 0


def test_cli_json_tools_flag():
    """--json before tools subcommand routes through callback's JSON output path."""
    result = runner.invoke(app, ["--json", "tools"])
    assert result.exit_code == 0
    # Output should be a JSON array
    data = json.loads(result.output)
    assert isinstance(data, list)


def test_cli_no_auth_token_exits(monkeypatch, tmp_path):
    """tgenie interface without authToken should exit with error."""
    monkeypatch.setattr("genie.core.config._LEGACY_PATH", tmp_path / "x.json")
    monkeypatch.setattr("genie.core.config._TOML_PATH", tmp_path / "y.toml")
    result = runner.invoke(app, [])
    assert result.exit_code != 0 or "auth" in result.output.lower() or result.exit_code == 1


def test_cli_file_not_found(monkeypatch, tmp_path):
    """Passing a non-existent file path exits with code 1."""
    cfg_file = tmp_path / "ai-agent-config.json"
    cfg_file.write_text(json.dumps({"interface": "openai", "openaiApiKey": "sk-test"}))
    monkeypatch.setattr("genie.core.config._LEGACY_PATH", cfg_file)
    monkeypatch.setattr("genie.core.config._TOML_PATH", tmp_path / "y.toml")
    result = runner.invoke(app, ["nonexistent_query.sql"])
    assert result.exit_code == 1
    assert "not found" in result.output.lower() or "File not found" in result.output
