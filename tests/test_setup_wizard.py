"""Tests for genie.setup_wizard — interactive config wizards."""
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from genie.setup_wizard import _write_toml, setup_check, setup_llm, setup_mcp, setup_trino


@pytest.fixture
def tmp_home(tmp_path, monkeypatch):
    """Redirect config paths to tmp directory."""
    import genie.setup_wizard as sw
    monkeypatch.setattr(sw, "_TOML_PATH", tmp_path / ".genie" / "config.toml")
    monkeypatch.setattr(sw, "_TRINO_PATH", tmp_path / ".config" / "genie" / "trino.json")
    monkeypatch.setattr(sw, "_MCP_PATH", tmp_path / ".config" / "genie" / "mcp.json")
    return tmp_path


def test_write_toml_creates_file(tmp_home):
    import genie.setup_wizard as sw
    _write_toml({"interface": "openai", "defaultModel": "gpt-4o"})
    content = sw._TOML_PATH.read_text()
    assert 'interface = "openai"' in content
    assert 'defaultModel = "gpt-4o"' in content


def test_setup_llm_ollama(tmp_home):
    """Test Ollama backend setup."""
    import genie.setup_wizard as sw
    inputs = iter(["1", "qwen3.5:4b", "http://localhost:11434/v1"])
    with patch("builtins.input", side_effect=lambda _: next(inputs)):
        setup_llm()
    content = sw._TOML_PATH.read_text()
    assert 'interface = "openai"' in content
    assert 'openaiApiKey = "ollama"' in content


def test_setup_trino(tmp_home):
    """Test Trino connection setup."""
    import genie.setup_wizard as sw
    inputs = iter(["mytrino", "trino-host.com", "8080", "sam", "https", "hive", "dw", "Prod"])
    with patch("builtins.input", side_effect=lambda _: next(inputs)):
        setup_trino()
    data = json.loads(sw._TRINO_PATH.read_text())
    assert data["active"] == "mytrino"
    assert data["profiles"]["mytrino"]["host"] == "trino-host.com"
    assert data["profiles"]["mytrino"]["port"] == 8080


def test_setup_mcp(tmp_home):
    """Test MCP server setup."""
    import genie.setup_wizard as sw
    inputs = iter(["http://localhost:8811", "30"])
    with patch("builtins.input", side_effect=lambda _: next(inputs)):
        setup_mcp()
    data = json.loads(sw._MCP_PATH.read_text())
    assert data["trino"]["url"] == "http://localhost:8811"
    assert data["trino"]["enabled"] is True


def test_setup_check_no_config(tmp_home, capsys):
    """setup_check should not crash when no config files exist."""
    setup_check()
    captured = capsys.readouterr()
    assert "Setup Check" in captured.out
    assert "[FAIL]" in captured.out
    assert "not configured" in captured.out


def test_setup_check_reports_all_layers(tmp_home, capsys):
    """setup_check should report on LLM, Trino, and MCP."""
    setup_check()
    captured = capsys.readouterr()
    assert "LLM Backend" in captured.out
    assert "Trino" in captured.out
    assert "MCP Trino" in captured.out
