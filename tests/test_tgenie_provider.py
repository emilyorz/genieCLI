"""Tests for TGenieProvider using responses library to mock HTTP."""
from __future__ import annotations

import pytest
import requests
import responses as rsps_lib

from genie.core.provider import CompletionRequest
from genie.providers.tgenie import TGenieProvider, set_debug


ENDPOINT = "https://tgenie.internal"


def _cfg(**kwargs) -> dict:
    base = {
        "endpoint": ENDPOINT,
        "authToken": "tok-abc123",
        "frontendUrl": "https://tgenie.internal",
        "cookies": [],
    }
    base.update(kwargs)
    return base


def _msg(role: str, text: str) -> dict:
    return {"role": role, "content": [{"type": "text", "text": text}]}


SSE_BODY = (
    'data: {"choices": [{"delta": {"content": "Hello"}}]}\n'
    'data: {"choices": [{"delta": {"content": " TGenie"}}]}\n'
    "data: [DONE]\n"
)


# ── set_debug ─────────────────────────────────────────────────────────────────

def test_set_debug_toggles():
    import genie.providers.tgenie as mod
    set_debug(True)
    assert mod._DEBUG is True
    set_debug(False)
    assert mod._DEBUG is False


# ── Basic properties ──────────────────────────────────────────────────────────

def test_tgenie_provider_name():
    provider = TGenieProvider(_cfg())
    assert provider.name == "tgenie"


def test_tgenie_provider_capabilities():
    provider = TGenieProvider(_cfg())
    caps = provider.capabilities()
    assert caps.streaming is True
    assert caps.vision is True
    assert caps.tool_calls is False


# ── SSE response ──────────────────────────────────────────────────────────────

@rsps_lib.activate
def test_tgenie_provider_sse_response():
    rsps_lib.add(
        rsps_lib.POST,
        f"{ENDPOINT}/api/main/oaicompatible",
        body=SSE_BODY,
        status=200,
        content_type="text/event-stream",
    )
    provider = TGenieProvider(_cfg())
    req = CompletionRequest(messages=[_msg("user", "hello")], model="gemini-2.5-flash")
    result = provider.complete_text(req)
    assert result == "Hello TGenie"


@rsps_lib.activate
def test_tgenie_provider_complete_iterator():
    rsps_lib.add(
        rsps_lib.POST,
        f"{ENDPOINT}/api/main/oaicompatible",
        body=SSE_BODY,
        status=200,
        content_type="text/event-stream",
    )
    provider = TGenieProvider(_cfg())
    req = CompletionRequest(messages=[_msg("user", "hi")], model="gemini-2.5-flash")
    deltas = list(provider.complete(req))
    assert len(deltas) == 1
    assert deltas[0].text == "Hello TGenie"
    assert deltas[0].finish_reason == "stop"


# ── JSON (non-SSE) response ───────────────────────────────────────────────────

@rsps_lib.activate
def test_tgenie_provider_json_response():
    """Non-SSE JSON response path."""
    payload = {
        "choices": [{"message": {"content": "json answer"}}]
    }
    # Use a non-stream content type so the SSE branch isn't triggered
    rsps_lib.add(
        rsps_lib.POST,
        f"{ENDPOINT}/api/main/oaicompatible",
        json=payload,
        status=200,
        content_type="application/json",
    )
    provider = TGenieProvider(_cfg())
    req = CompletionRequest(messages=[_msg("user", "hi")], model="gemini-2.5-flash")
    result = provider.complete_text(req)
    assert result == "json answer"


@rsps_lib.activate
def test_tgenie_provider_json_delta_fallback():
    """Choices with delta key instead of message."""
    payload = {
        "choices": [{"delta": {"content": "delta content"}}]
    }
    rsps_lib.add(
        rsps_lib.POST,
        f"{ENDPOINT}/api/main/oaicompatible",
        json=payload,
        status=200,
        content_type="application/json",
    )
    provider = TGenieProvider(_cfg())
    req = CompletionRequest(messages=[_msg("user", "hi")], model="gemini-2.5-flash")
    result = provider.complete_text(req)
    assert result == "delta content"


@rsps_lib.activate
def test_tgenie_provider_reasoning_content_in_json():
    payload = {
        "choices": [{"message": {"reasoning_content": "step by step", "content": ""}}]
    }
    rsps_lib.add(
        rsps_lib.POST,
        f"{ENDPOINT}/api/main/oaicompatible",
        json=payload,
        status=200,
        content_type="application/json",
    )
    provider = TGenieProvider(_cfg())
    req = CompletionRequest(messages=[_msg("user", "think")], model="o1")
    result = provider.complete_text(req)
    assert result == "step by step"


# ── Request body contains expected fields ─────────────────────────────────────

@rsps_lib.activate
def test_tgenie_provider_sends_model_and_reasoning():
    captured = []

    def callback(request):
        body_bytes = request.body if isinstance(request.body, bytes) else request.body.encode()
        captured.append(body_bytes.decode("utf-8", errors="replace"))
        return (200, {"content-type": "text/event-stream"}, SSE_BODY)

    rsps_lib.add_callback(
        rsps_lib.POST,
        f"{ENDPOINT}/api/main/oaicompatible",
        callback=callback,
    )
    provider = TGenieProvider(_cfg())
    req = CompletionRequest(
        messages=[_msg("user", "test")],
        model="gemini-2.5-flash",
        reasoning="low",
    )
    provider.complete_text(req)
    assert len(captured) == 1
    body = captured[0]
    assert "gemini-2.5-flash" in body
    assert "low" in body  # reasoning effort


# ── Cookies ───────────────────────────────────────────────────────────────────

@rsps_lib.activate
def test_tgenie_provider_sets_cookies():
    rsps_lib.add(
        rsps_lib.POST,
        f"{ENDPOINT}/api/main/oaicompatible",
        body=SSE_BODY,
        status=200,
        content_type="text/event-stream",
    )
    cfg = _cfg(cookies=[
        {"name": "session", "value": "abc", "domain": "tgenie.internal"},
    ])
    provider = TGenieProvider(cfg)
    req = CompletionRequest(messages=[_msg("user", "hi")], model="gemini-2.5-flash")
    result = provider.complete_text(req)
    assert result == "Hello TGenie"


# ── Custom header ─────────────────────────────────────────────────────────────

@rsps_lib.activate
def test_tgenie_provider_custom_header():
    captured_headers = []

    def callback(request):
        captured_headers.append(dict(request.headers))
        return (200, {"content-type": "text/event-stream"}, SSE_BODY)

    rsps_lib.add_callback(
        rsps_lib.POST,
        f"{ENDPOINT}/api/main/oaicompatible",
        callback=callback,
    )
    cfg = _cfg(customHeader="my-secret-value")
    provider = TGenieProvider(cfg)
    req = CompletionRequest(messages=[_msg("user", "hi")], model="gemini-2.5-flash")
    provider.complete_text(req)
    assert captured_headers[0].get("x-custom-header") == "my-secret-value"


# ── Error handling ────────────────────────────────────────────────────────────

@rsps_lib.activate
def test_tgenie_provider_http_500_raises():
    rsps_lib.add(
        rsps_lib.POST,
        f"{ENDPOINT}/api/main/oaicompatible",
        json={"error": "internal server error"},
        status=500,
    )
    provider = TGenieProvider(_cfg())
    req = CompletionRequest(messages=[_msg("user", "hi")], model="gemini-2.5-flash")
    with pytest.raises(Exception):
        provider.complete_text(req)


@rsps_lib.activate
def test_tgenie_provider_network_error_raises():
    rsps_lib.add(
        rsps_lib.POST,
        f"{ENDPOINT}/api/main/oaicompatible",
        body=requests.exceptions.ConnectionError("connection refused"),
    )
    provider = TGenieProvider(_cfg())
    req = CompletionRequest(messages=[_msg("user", "hi")], model="gemini-2.5-flash")
    with pytest.raises(RuntimeError, match="connection refused"):
        provider.complete_text(req)


# ── File attachment ───────────────────────────────────────────────────────────

@rsps_lib.activate
def test_tgenie_provider_with_file():
    rsps_lib.add(
        rsps_lib.POST,
        f"{ENDPOINT}/api/main/oaicompatible",
        body=SSE_BODY,
        status=200,
        content_type="text/event-stream",
    )
    provider = TGenieProvider(_cfg())
    req = CompletionRequest(
        messages=[_msg("user", "see this file")],
        model="gemini-2.5-flash",
        files=[{
            "filename": "doc.txt",
            "content_type": "text/plain",
            "data": b"file content here",
        }],
    )
    result = provider.complete_text(req)
    assert result == "Hello TGenie"


# ── 401 token refresh (mocked to fail) ───────────────────────────────────────

@rsps_lib.activate
def test_tgenie_provider_401_refresh_fails(monkeypatch):
    """On 401, provider tries to refresh token. If refresh fails, raises RuntimeError."""
    rsps_lib.add(
        rsps_lib.POST,
        f"{ENDPOINT}/api/main/oaicompatible",
        json={"error": "unauthorized"},
        status=401,
    )
    # Mock _refresh_token to return False (refresh fails)
    monkeypatch.setattr(
        TGenieProvider,
        "_refresh_token",
        lambda self: False,
    )
    provider = TGenieProvider(_cfg())
    req = CompletionRequest(messages=[_msg("user", "hi")], model="gemini-2.5-flash")
    with pytest.raises(RuntimeError, match="Token refresh failed"):
        provider.complete_text(req)
