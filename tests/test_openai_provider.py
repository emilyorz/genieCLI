"""Tests for OpenAIProvider using responses library to mock HTTP."""
from __future__ import annotations

import json

import pytest
import responses as rsps_lib

from genie.core.provider import CompletionRequest
from genie.providers.openai import OpenAIProvider, _history_to_openai, set_debug


# ── _history_to_openai helper ─────────────────────────────────────────────────

def _msg(role: str, text: str) -> dict:
    return {"role": role, "content": [{"type": "text", "text": text}]}


def test_history_to_openai_basic():
    history = [_msg("user", "hello"), _msg("assistant", "hi")]
    result = _history_to_openai(history)
    assert result == [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "hi"},
    ]


def test_history_to_openai_skips_unknown_roles():
    history = [_msg("tool", "data"), _msg("user", "hi")]
    result = _history_to_openai(history)
    assert len(result) == 1
    assert result[0]["role"] == "user"


def test_history_to_openai_content_as_array():
    history = [_msg("user", "hello")]
    result = _history_to_openai(history, content_as_array=True)
    assert result[0]["content"] == [{"type": "text", "text": "hello"}]


def test_history_to_openai_assistant_not_array():
    history = [_msg("assistant", "response")]
    result = _history_to_openai(history, content_as_array=True)
    # assistant stays as string even with content_as_array
    assert result[0]["content"] == "response"


def test_history_to_openai_empty_content():
    history = [{"role": "user", "content": []}]
    result = _history_to_openai(history)
    assert result[0]["content"] == ""


def test_history_to_openai_system_passthrough():
    history = [_msg("system", "be helpful"), _msg("user", "hi")]
    result = _history_to_openai(history)
    assert result[0] == {"role": "system", "content": "be helpful"}


# ── set_debug ─────────────────────────────────────────────────────────────────

def test_set_debug_toggles():
    import genie.providers.openai as mod
    set_debug(True)
    assert mod._DEBUG is True
    set_debug(False)
    assert mod._DEBUG is False


# ── OpenAIProvider basic properties ──────────────────────────────────────────

def test_openai_provider_name():
    provider = OpenAIProvider({"openaiBaseUrl": "https://api.openai.com/v1"})
    assert provider.name == "openai"


def test_openai_provider_capabilities():
    provider = OpenAIProvider({})
    caps = provider.capabilities()
    assert caps.streaming is True
    assert caps.vision is True
    assert caps.tool_calls is False


# ── SSE streaming response ────────────────────────────────────────────────────

SSE_STREAM = (
    'data: {"choices": [{"delta": {"content": "Hello"}}]}\n'
    'data: {"choices": [{"delta": {"content": " world"}}]}\n'
    "data: [DONE]\n"
)


@rsps_lib.activate
def test_openai_provider_sse_stream():
    rsps_lib.add(
        rsps_lib.POST,
        "https://api.openai.com/v1/chat/completions",
        body=SSE_STREAM,
        status=200,
        content_type="text/event-stream",
    )
    provider = OpenAIProvider({"openaiBaseUrl": "https://api.openai.com/v1", "openaiApiKey": "sk-test"})
    req = CompletionRequest(
        messages=[_msg("user", "hi")],
        model="gpt-4o",
    )
    result = provider.complete_text(req)
    assert result == "Hello world"


@rsps_lib.activate
def test_openai_provider_complete_iterator():
    rsps_lib.add(
        rsps_lib.POST,
        "https://api.openai.com/v1/chat/completions",
        body=SSE_STREAM,
        status=200,
        content_type="text/event-stream",
    )
    provider = OpenAIProvider({"openaiBaseUrl": "https://api.openai.com/v1", "openaiApiKey": "sk-test"})
    req = CompletionRequest(messages=[_msg("user", "hi")], model="gpt-4o")
    deltas = list(provider.complete(req))
    assert len(deltas) == 1
    assert deltas[0].text == "Hello world"
    assert deltas[0].finish_reason == "stop"


# ── SSE with reasoning_content ────────────────────────────────────────────────

@rsps_lib.activate
def test_openai_provider_reasoning_content_via_sse():
    body = (
        'data: {"choices": [{"delta": {"reasoning_content": "thinking step"}}]}\n'
        "data: [DONE]\n"
    )
    rsps_lib.add(
        rsps_lib.POST,
        "https://api.openai.com/v1/chat/completions",
        body=body,
        status=200,
        content_type="text/event-stream",
    )
    provider = OpenAIProvider({"openaiBaseUrl": "https://api.openai.com/v1"})
    req = CompletionRequest(messages=[_msg("user", "think")], model="o1")
    result = provider.complete_text(req)
    assert result == "thinking step"


# ── Error handling ────────────────────────────────────────────────────────────

@rsps_lib.activate
def test_openai_provider_http_error_raises():
    rsps_lib.add(
        rsps_lib.POST,
        "https://api.openai.com/v1/chat/completions",
        json={"error": {"message": "Unauthorized"}},
        status=401,
    )
    provider = OpenAIProvider({"openaiBaseUrl": "https://api.openai.com/v1", "openaiApiKey": "bad-key"})
    req = CompletionRequest(messages=[_msg("user", "hi")], model="gpt-4o")
    with pytest.raises(Exception):
        provider.complete_text(req)


@rsps_lib.activate
def test_openai_provider_network_error_raises():
    import requests as req_lib
    rsps_lib.add(
        rsps_lib.POST,
        "https://api.openai.com/v1/chat/completions",
        body=req_lib.exceptions.ConnectionError("connection refused"),
    )
    provider = OpenAIProvider({"openaiBaseUrl": "https://api.openai.com/v1"})
    req = CompletionRequest(messages=[_msg("user", "hi")], model="gpt-4o")
    with pytest.raises(RuntimeError, match="connection refused"):
        provider.complete_text(req)


@rsps_lib.activate
def test_openai_provider_empty_sse_raises():
    rsps_lib.add(
        rsps_lib.POST,
        "https://api.openai.com/v1/chat/completions",
        body="data: [DONE]\n",
        status=200,
        content_type="text/event-stream",
    )
    provider = OpenAIProvider({"openaiBaseUrl": "https://api.openai.com/v1"})
    req = CompletionRequest(messages=[_msg("user", "hi")], model="gpt-4o")
    with pytest.raises(RuntimeError, match="no content"):
        provider.complete_text(req)


@rsps_lib.activate
def test_openai_provider_empty_body_raises():
    rsps_lib.add(
        rsps_lib.POST,
        "https://api.openai.com/v1/chat/completions",
        body="",
        status=200,
        content_type="text/plain",
    )
    provider = OpenAIProvider({"openaiBaseUrl": "https://api.openai.com/v1"})
    req = CompletionRequest(messages=[_msg("user", "hi")], model="gpt-4o")
    with pytest.raises(RuntimeError, match="Empty response"):
        provider.complete_text(req)


# ── Custom base URL ───────────────────────────────────────────────────────────

@rsps_lib.activate
def test_openai_provider_custom_base_url():
    # Code routes Ollama to native /api/chat (not /v1/chat/completions).
    # Response format is JSON with {"message": {"content": ...}}.
    rsps_lib.add(
        rsps_lib.POST,
        "http://localhost:11434/api/chat",
        json={"message": {"role": "assistant", "content": "ollama says hi"}, "done": True},
        status=200,
    )
    provider = OpenAIProvider({"openaiBaseUrl": "http://localhost:11434/v1"})
    req = CompletionRequest(messages=[_msg("user", "hi")], model="llama3")
    result = provider.complete_text(req)
    assert result == "ollama says hi"


# ── Vision / file attachment ──────────────────────────────────────────────────

@rsps_lib.activate
def test_openai_provider_with_file_attachment():
    body = (
        'data: {"choices": [{"delta": {"content": "I see an image"}}]}\n'
        "data: [DONE]\n"
    )
    rsps_lib.add(
        rsps_lib.POST,
        "https://api.openai.com/v1/chat/completions",
        body=body,
        status=200,
        content_type="text/event-stream",
    )
    provider = OpenAIProvider({"openaiBaseUrl": "https://api.openai.com/v1"})
    req = CompletionRequest(
        messages=[_msg("user", "what's this?")],
        model="gpt-4o",
        files=[{"filename": "img.png", "content_type": "image/png", "data": b"\x89PNG"}],
    )
    result = provider.complete_text(req)
    assert result == "I see an image"


# ── content_as_array mode ─────────────────────────────────────────────────────

@rsps_lib.activate
def test_openai_provider_content_as_array_mode():
    body = (
        'data: {"choices": [{"delta": {"content": "array mode ok"}}]}\n'
        "data: [DONE]\n"
    )
    rsps_lib.add(
        rsps_lib.POST,
        "https://api.openai.com/v1/chat/completions",
        body=body,
        status=200,
        content_type="text/event-stream",
    )
    provider = OpenAIProvider({
        "openaiBaseUrl": "https://api.openai.com/v1",
        "openaiContentArray": True,
    })
    req = CompletionRequest(messages=[_msg("user", "hello")], model="gpt-4o")
    result = provider.complete_text(req)
    assert result == "array mode ok"


# ── SSE data lines in non-streaming response ──────────────────────────────────

@rsps_lib.activate
def test_openai_provider_sse_data_lines_in_text_plain():
    """If response has data: lines but content-type isn't event-stream, fall back to SSE parsing."""
    body = (
        'data: {"choices": [{"delta": {"content": "fallback"}}]}\n'
        "data: [DONE]\n"
    )
    rsps_lib.add(
        rsps_lib.POST,
        "https://api.openai.com/v1/chat/completions",
        body=body,
        status=200,
        content_type="text/plain",
    )
    provider = OpenAIProvider({"openaiBaseUrl": "https://api.openai.com/v1"})
    req = CompletionRequest(messages=[_msg("user", "hi")], model="gpt-4o")
    result = provider.complete_text(req)
    assert result == "fallback"
