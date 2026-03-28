"""Tests for AnthropicProvider using responses library to mock HTTP."""
from __future__ import annotations

import pytest
import requests
import responses as rsps_lib

from genie.core.provider import CompletionRequest
from genie.providers.anthropic import AnthropicProvider, set_debug


def _msg(role: str, text: str) -> dict:
    return {"role": role, "content": [{"type": "text", "text": text}]}


SSE_STREAM = (
    'data: {"choices": [{"delta": {"content": "Hello"}}]}\n'
    'data: {"choices": [{"delta": {"content": " world"}}]}\n'
    "data: [DONE]\n"
)


# ── set_debug ─────────────────────────────────────────────────────────────────

def test_set_debug_toggles():
    import genie.providers.anthropic as mod
    set_debug(True)
    assert mod._DEBUG is True
    set_debug(False)
    assert mod._DEBUG is False


# ── Basic properties ──────────────────────────────────────────────────────────

def test_anthropic_provider_name():
    provider = AnthropicProvider({})
    assert provider.name == "anthropic"


def test_anthropic_provider_capabilities():
    provider = AnthropicProvider({})
    caps = provider.capabilities()
    assert caps.streaming is True
    assert caps.vision is True
    assert caps.tool_calls is False


# ── SSE streaming ─────────────────────────────────────────────────────────────

@rsps_lib.activate
def test_anthropic_provider_sse_stream():
    rsps_lib.add(
        rsps_lib.POST,
        "https://api.openai.com/v1/chat/completions",
        body=SSE_STREAM,
        status=200,
        content_type="text/event-stream",
    )
    provider = AnthropicProvider({
        "openaiBaseUrl": "https://api.openai.com/v1",
        "openaiApiKey": "sk-test",
    })
    req = CompletionRequest(messages=[_msg("user", "hi")], model="claude-3-sonnet")
    result = provider.complete_text(req)
    assert result == "Hello world"


@rsps_lib.activate
def test_anthropic_provider_complete_iterator():
    rsps_lib.add(
        rsps_lib.POST,
        "https://api.openai.com/v1/chat/completions",
        body=SSE_STREAM,
        status=200,
        content_type="text/event-stream",
    )
    provider = AnthropicProvider({
        "openaiBaseUrl": "https://api.openai.com/v1",
        "openaiApiKey": "sk-test",
    })
    req = CompletionRequest(messages=[_msg("user", "hi")], model="claude-3-sonnet")
    deltas = list(provider.complete(req))
    assert len(deltas) == 1
    assert deltas[0].text == "Hello world"
    assert deltas[0].finish_reason == "stop"


# ── System prompt extraction (Anthropic wire format) ──────────────────────────

@rsps_lib.activate
def test_anthropic_provider_extracts_system_prompt():
    """System messages must be sent as top-level 'system' field."""
    captured = []

    def request_callback(request):
        import json
        body = json.loads(request.body)
        captured.append(body)
        return (200, {"content-type": "text/event-stream"}, SSE_STREAM)

    rsps_lib.add_callback(
        rsps_lib.POST,
        "https://api.openai.com/v1/chat/completions",
        callback=request_callback,
    )
    provider = AnthropicProvider({
        "openaiBaseUrl": "https://api.openai.com/v1",
        "openaiApiKey": "sk-test",
    })
    req = CompletionRequest(
        messages=[
            _msg("system", "You are a helpful assistant."),
            _msg("user", "hello"),
        ],
        model="claude-3-sonnet",
    )
    provider.complete_text(req)
    assert len(captured) == 1
    body = captured[0]
    assert body.get("system") == "You are a helpful assistant."
    # system message should NOT appear in messages list
    for m in body["messages"]:
        assert m["role"] != "system"


@rsps_lib.activate
def test_anthropic_provider_no_system_prompt():
    """Without a system message, no 'system' key is added."""
    captured = []

    def request_callback(request):
        import json
        body = json.loads(request.body)
        captured.append(body)
        return (200, {"content-type": "text/event-stream"}, SSE_STREAM)

    rsps_lib.add_callback(
        rsps_lib.POST,
        "https://api.openai.com/v1/chat/completions",
        callback=request_callback,
    )
    provider = AnthropicProvider({"openaiBaseUrl": "https://api.openai.com/v1"})
    req = CompletionRequest(messages=[_msg("user", "hello")], model="claude-3-haiku")
    provider.complete_text(req)
    assert "system" not in captured[0]


# ── Vision / image attachment ─────────────────────────────────────────────────

@rsps_lib.activate
def test_anthropic_provider_with_image_file():
    rsps_lib.add(
        rsps_lib.POST,
        "https://api.openai.com/v1/chat/completions",
        body=SSE_STREAM,
        status=200,
        content_type="text/event-stream",
    )
    provider = AnthropicProvider({"openaiBaseUrl": "https://api.openai.com/v1"})
    req = CompletionRequest(
        messages=[_msg("user", "describe this image")],
        model="claude-3-opus",
        files=[{
            "filename": "photo.jpg",
            "content_type": "image/jpeg",
            "data": b"\xff\xd8\xff",
        }],
    )
    result = provider.complete_text(req)
    assert result == "Hello world"


# ── content_as_array mode ─────────────────────────────────────────────────────

@rsps_lib.activate
def test_anthropic_provider_content_as_array():
    rsps_lib.add(
        rsps_lib.POST,
        "https://api.openai.com/v1/chat/completions",
        body=SSE_STREAM,
        status=200,
        content_type="text/event-stream",
    )
    provider = AnthropicProvider({
        "openaiBaseUrl": "https://api.openai.com/v1",
        "openaiContentArray": True,
    })
    req = CompletionRequest(messages=[_msg("user", "hello")], model="claude-3-sonnet")
    result = provider.complete_text(req)
    assert result == "Hello world"


# ── Error handling ────────────────────────────────────────────────────────────

@rsps_lib.activate
def test_anthropic_provider_http_401_raises():
    rsps_lib.add(
        rsps_lib.POST,
        "https://api.openai.com/v1/chat/completions",
        json={"error": "unauthorized"},
        status=401,
    )
    provider = AnthropicProvider({
        "openaiBaseUrl": "https://api.openai.com/v1",
        "openaiApiKey": "bad",
    })
    req = CompletionRequest(messages=[_msg("user", "hi")], model="claude-3-sonnet")
    with pytest.raises(Exception):
        provider.complete_text(req)


@rsps_lib.activate
def test_anthropic_provider_network_error_raises():
    rsps_lib.add(
        rsps_lib.POST,
        "https://api.openai.com/v1/chat/completions",
        body=requests.exceptions.ConnectionError("timeout"),
    )
    provider = AnthropicProvider({"openaiBaseUrl": "https://api.openai.com/v1"})
    req = CompletionRequest(messages=[_msg("user", "hi")], model="claude-3-sonnet")
    with pytest.raises(RuntimeError, match="timeout"):
        provider.complete_text(req)


@rsps_lib.activate
def test_anthropic_provider_empty_sse_raises():
    rsps_lib.add(
        rsps_lib.POST,
        "https://api.openai.com/v1/chat/completions",
        body="data: [DONE]\n",
        status=200,
        content_type="text/event-stream",
    )
    provider = AnthropicProvider({"openaiBaseUrl": "https://api.openai.com/v1"})
    req = CompletionRequest(messages=[_msg("user", "hi")], model="claude-3-sonnet")
    with pytest.raises(RuntimeError):
        provider.complete_text(req)


# ── Custom base URL ───────────────────────────────────────────────────────────

@rsps_lib.activate
def test_anthropic_provider_custom_base_url():
    body = (
        'data: {"choices": [{"delta": {"content": "custom endpoint"}}]}\n'
        "data: [DONE]\n"
    )
    rsps_lib.add(
        rsps_lib.POST,
        "http://my-proxy.internal/v1/chat/completions",
        body=body,
        status=200,
        content_type="text/event-stream",
    )
    provider = AnthropicProvider({"openaiBaseUrl": "http://my-proxy.internal/v1"})
    req = CompletionRequest(messages=[_msg("user", "hi")], model="claude-3-sonnet")
    result = provider.complete_text(req)
    assert result == "custom endpoint"


# ── SSE fallback for stream-like content-type ─────────────────────────────────

@rsps_lib.activate
def test_anthropic_provider_text_event_stream_content_type():
    """text/event-stream content-type triggers SSE path."""
    body = (
        'data: {"choices": [{"delta": {"content": "streamed"}}]}\n'
        "data: [DONE]\n"
    )
    rsps_lib.add(
        rsps_lib.POST,
        "https://api.openai.com/v1/chat/completions",
        body=body,
        status=200,
        content_type="text/event-stream; charset=utf-8",
    )
    provider = AnthropicProvider({"openaiBaseUrl": "https://api.openai.com/v1"})
    req = CompletionRequest(messages=[_msg("user", "hi")], model="claude-3-sonnet")
    result = provider.complete_text(req)
    assert result == "streamed"
