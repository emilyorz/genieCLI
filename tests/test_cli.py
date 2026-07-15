"""Tests for CLI bootstrap and config loading."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from genie.core.config import DEFAULTS, load


def test_load_config_returns_defaults_when_no_file(tmp_path, monkeypatch):
    # Point config paths at non-existent files in tmp_path
    monkeypatch.setattr("genie.core.config._LEGACY_PATH", tmp_path / "ai-agent-config.json")
    monkeypatch.setattr("genie.core.config._TOML_PATH", tmp_path / ".genie" / "config.toml")
    cfg = load()
    assert cfg["interface"] == "tgenie"
    assert cfg["defaultModel"] == DEFAULTS["defaultModel"]


def test_load_config_reads_json(tmp_path, monkeypatch):
    cfg_file = tmp_path / "ai-agent-config.json"
    cfg_file.write_text(json.dumps({"interface": "openai", "openaiApiKey": "sk-test"}))
    monkeypatch.setattr("genie.core.config._LEGACY_PATH", cfg_file)
    monkeypatch.setattr("genie.core.config._TOML_PATH", tmp_path / ".genie" / "config.toml")
    cfg = load()
    assert cfg["interface"] == "openai"
    assert cfg["openaiApiKey"] == "sk-test"
    # Defaults still filled in
    assert cfg["defaultModel"] == DEFAULTS["defaultModel"]


def test_load_config_cli_overrides(tmp_path, monkeypatch):
    monkeypatch.setattr("genie.core.config._LEGACY_PATH", tmp_path / "x.json")
    monkeypatch.setattr("genie.core.config._TOML_PATH", tmp_path / "y.toml")
    cfg = load(overrides={"defaultModel": "gpt-4o", "interface": None})
    # None overrides are ignored
    assert cfg["interface"] == "tgenie"
    assert cfg["defaultModel"] == "gpt-4o"


def test_load_config_env_overrides(tmp_path, monkeypatch):
    monkeypatch.setattr("genie.core.config._LEGACY_PATH", tmp_path / "x.json")
    monkeypatch.setattr("genie.core.config._TOML_PATH", tmp_path / "y.toml")
    monkeypatch.setenv("GENIE_INTERFACE", "anthropic")
    cfg = load()
    assert cfg["interface"] == "anthropic"


def test_fake_provider_records_calls():
    from tests.conftest import FakeProvider
    from genie.core.provider import CompletionRequest

    provider = FakeProvider(["hello", "world"])
    req = CompletionRequest(messages=[{"role": "user", "content": [{"type": "text", "text": "hi"}]}], model="test")
    r1 = provider.complete_text(req)
    r2 = provider.complete_text(req)
    assert r1 == "hello"
    assert r2 == "world"
    assert len(provider.calls) == 2


def test_python_version_check_requires_python_310():
    """Doctor must match the project metadata's Python 3.10 floor."""
    from genie.cli import _python_version_check

    assert _python_version_check((3, 9), "3.9.19") == ("FAIL", "3.9.19 (need ≥ 3.10)")
    assert _python_version_check((3, 10), "3.10.0") == ("OK", "3.10.0")
