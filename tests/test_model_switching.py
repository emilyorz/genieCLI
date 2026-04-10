"""Tests for model switching helpers in genie/chat.py."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from genie.chat import _get_ollama_models, _is_ollama, _list_models, _validate_model


class FakeOutput:
    def __init__(self) -> None:
        self.lines: list[str] = []

    def print(self, msg: str) -> None:
        self.lines.append(msg)


# ── _is_ollama ───────────────────────────────────────────────────────────────


def test_is_ollama_true() -> None:
    cfg = {"interface": "openai", "openaiBaseUrl": "http://localhost:11434/v1"}
    assert _is_ollama(cfg) is True


def test_is_ollama_false_wrong_interface() -> None:
    cfg = {"interface": "tgenie", "openaiBaseUrl": "http://localhost:11434/v1"}
    assert _is_ollama(cfg) is False


def test_is_ollama_false_wrong_url() -> None:
    cfg = {"interface": "openai", "openaiBaseUrl": "https://api.openai.com/v1"}
    assert _is_ollama(cfg) is False


# ── _validate_model ──────────────────────────────────────────────────────────


def test_validate_model_non_ollama_allows_anything() -> None:
    cfg = {"interface": "tgenie"}
    valid, msg = _validate_model(cfg, "any-model-name")
    assert valid is True
    assert msg == ""


@patch("genie.chat.requests.get")
def test_validate_model_ollama_valid(mock_get: MagicMock) -> None:
    mock_get.return_value.json.return_value = {
        "models": [{"name": "llama3:latest"}, {"name": "mistral:latest"}],
    }
    mock_get.return_value.raise_for_status = MagicMock()

    cfg = {"interface": "openai", "openaiBaseUrl": "http://localhost:11434/v1"}
    valid, msg = _validate_model(cfg, "llama3:latest")
    assert valid is True
    assert msg == ""


@patch("genie.chat.requests.get")
def test_validate_model_ollama_invalid(mock_get: MagicMock) -> None:
    mock_get.return_value.json.return_value = {
        "models": [{"name": "llama3:latest"}, {"name": "mistral:latest"}],
    }
    mock_get.return_value.raise_for_status = MagicMock()

    cfg = {"interface": "openai", "openaiBaseUrl": "http://localhost:11434/v1"}
    valid, msg = _validate_model(cfg, "nonexistent-model")
    assert valid is False
    assert "nonexistent-model" in msg
    assert "not found" in msg.lower()


@patch("genie.chat.requests.get")
def test_validate_model_ollama_unreachable(mock_get: MagicMock) -> None:
    mock_get.side_effect = ConnectionError("refused")

    cfg = {"interface": "openai", "openaiBaseUrl": "http://localhost:11434/v1"}
    valid, msg = _validate_model(cfg, "whatever")
    # Can't validate when Ollama is unreachable → allow the model
    assert valid is True
    assert msg == ""


# ── _list_models ─────────────────────────────────────────────────────────────


@patch("genie.chat.requests.get")
def test_list_models_ollama_shows_marker(mock_get: MagicMock) -> None:
    mock_get.return_value.json.return_value = {
        "models": [{"name": "llama3:latest"}, {"name": "mistral:latest"}],
    }
    mock_get.return_value.raise_for_status = MagicMock()

    cfg = {"interface": "openai", "openaiBaseUrl": "http://localhost:11434/v1"}
    output = FakeOutput()
    _list_models(cfg, output, current_model="llama3:latest")

    joined = "\n".join(output.lines)
    # Active model should have the ● marker
    assert "●" in joined
    # Both models listed
    assert "llama3:latest" in joined
    assert "mistral:latest" in joined


def test_list_models_non_ollama_shows_default() -> None:
    cfg = {"interface": "tgenie", "defaultModel": "gpt-4o"}
    output = FakeOutput()
    _list_models(cfg, output)

    joined = "\n".join(output.lines)
    assert "gpt-4o" in joined
    assert "Ollama only" in joined
