"""tests/test_llm_adapters.py — Unit tests for genie.core.llm_adapters (T9, v48)."""
from __future__ import annotations

from unittest.mock import MagicMock, call

import pytest

from genie.core.llm_adapters import _make_advisory_llm_fn


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

class _FakeProvider:
    """Minimal provider stub that records calls and returns scripted responses."""

    def __init__(self, responses: list[str]):
        self._responses = list(responses)
        self.calls: list = []

    def complete_text(self, request):
        self.calls.append(request)
        if self._responses:
            return self._responses.pop(0)
        raise RuntimeError("No more scripted responses")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestMakeAdvisoryLlmFn:
    def test_returns_callable(self):
        provider = _FakeProvider(["answer"])
        fn = _make_advisory_llm_fn(provider, "claude-3", "")
        assert callable(fn)

    def test_calls_provider_complete_text(self):
        provider = _FakeProvider(["the result"])
        fn = _make_advisory_llm_fn(provider, "claude-3", "extended")
        result = fn("SELECT 1")
        assert result == "the result"
        assert len(provider.calls) == 1

    def test_system_message_is_advisory(self):
        provider = _FakeProvider(["ok"])
        fn = _make_advisory_llm_fn(provider, "claude-3", "")
        fn("SELECT 1")
        req = provider.calls[0]
        # First message should be the system advisory prompt
        # content is a list of content blocks: [{"type": "text", "text": ...}]
        sys_msg = req.messages[0]
        content_blocks = sys_msg["content"]
        full_text = " ".join(
            block["text"] for block in content_blocks if isinstance(block, dict) and "text" in block
        )
        assert "advisory" in full_text.lower()

    def test_user_message_is_prompt(self):
        provider = _FakeProvider(["ok"])
        fn = _make_advisory_llm_fn(provider, "claude-3", "")
        fn("SELECT 1 FROM t")
        req = provider.calls[0]
        user_msg = req.messages[1]
        content_blocks = user_msg["content"]
        full_text = " ".join(
            block["text"] for block in content_blocks if isinstance(block, dict) and "text" in block
        )
        assert "SELECT 1 FROM t" in full_text

    def test_model_passed_to_request(self):
        provider = _FakeProvider(["ok"])
        fn = _make_advisory_llm_fn(provider, "my-special-model", "")
        fn("SELECT 1")
        req = provider.calls[0]
        assert req.model == "my-special-model"

    def test_reasoning_passed_to_request(self):
        provider = _FakeProvider(["ok"])
        fn = _make_advisory_llm_fn(provider, "m", "extended_thinking")
        fn("SELECT 1")
        req = provider.calls[0]
        assert req.reasoning == "extended_thinking"

    def test_none_response_coerced_to_empty_string(self):
        provider = _FakeProvider([None])  # type: ignore[list-item]
        fn = _make_advisory_llm_fn(provider, "m", "")
        result = fn("SELECT 1")
        assert result == ""

    def test_empty_string_response_returned_as_is(self):
        provider = _FakeProvider([""])
        fn = _make_advisory_llm_fn(provider, "m", "")
        result = fn("SELECT 1")
        assert result == ""

    def test_provider_exception_propagates(self):
        provider = _FakeProvider([])  # will raise on call
        fn = _make_advisory_llm_fn(provider, "m", "")
        with pytest.raises(RuntimeError, match="No more scripted responses"):
            fn("SELECT 1")

    def test_independent_closures_per_call(self):
        """Two calls to _make_advisory_llm_fn should produce independent callables."""
        p1 = _FakeProvider(["a"])
        p2 = _FakeProvider(["b"])
        fn1 = _make_advisory_llm_fn(p1, "m1", "")
        fn2 = _make_advisory_llm_fn(p2, "m2", "")
        assert fn1("prompt") == "a"
        assert fn2("prompt") == "b"
        assert len(p1.calls) == 1
        assert len(p2.calls) == 1
