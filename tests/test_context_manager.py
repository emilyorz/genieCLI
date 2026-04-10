"""Tests for genie.core.context_manager."""
import pytest
from genie.core.context_manager import ContextManager


def _make_msg(role: str, text: str) -> dict:
    return {
        "role": role,
        "content": [{"type": "text", "text": text, "reasonText": None}],
    }


def test_init_sets_profile():
    cm = ContextManager(model_name="gemini-2.5-flash")
    assert cm.profile.name == "gemini-2.5-flash"
    assert cm.context_window == 1_048_576


def test_should_prune_short_history():
    cm = ContextManager(model_name="gemini-2.5-flash")
    history = [_make_msg("system", "You are helpful."), _make_msg("user", "hi")]
    assert cm.should_prune(history) is False


def test_should_prune_large_history():
    cm = ContextManager(model_name="qwen3.5:4b")  # 32K context
    # Fill with enough messages to exceed 70% of available
    history = [_make_msg("system", "System prompt" * 100)]
    for i in range(200):
        history.append(_make_msg("user", f"Message number {i} " * 50))
        history.append(_make_msg("assistant", f"Response number {i} " * 50))
    assert cm.should_prune(history) is True


def test_prune_keeps_system_and_recent():
    cm = ContextManager(model_name="qwen3.5:4b")
    history = [_make_msg("system", "System prompt" * 100)]
    for i in range(200):
        history.append(_make_msg("user", f"Message {i} " * 50))
        history.append(_make_msg("assistant", f"Response {i} " * 50))

    pruned = cm.prune_history(history)
    # Should have: system + summary + last 4
    assert pruned[0]["role"] == "system"
    assert len(pruned) <= 10  # system + summary + 4 recent
    assert pruned[1]["role"] == "system"  # summary uses system role
    assert "[Context summary" in pruned[1]["content"][0]["text"]


def test_prune_noop_when_under_limit():
    cm = ContextManager(model_name="gemini-2.5-flash")  # 1M context
    history = [_make_msg("system", "hi"), _make_msg("user", "hello")]
    pruned = cm.prune_history(history)
    assert len(pruned) == len(history)


def test_truncate_tool_result_short():
    cm = ContextManager(model_name="gpt-4o")
    result = "Short result"
    assert cm.truncate_tool_result(result) == result


def test_truncate_tool_result_long():
    cm = ContextManager(model_name="gpt-4o")
    result = "x" * 10000
    truncated = cm.truncate_tool_result(result)
    assert len(truncated) < len(result)
    assert "truncated" in truncated


def test_context_status():
    cm = ContextManager(model_name="gemini-2.5-flash")
    history = [_make_msg("system", "hi"), _make_msg("user", "hello")]
    status = cm.context_status(history)
    assert status["model"] == "gemini-2.5-flash"
    assert status["tokens_used"] > 0
    assert status["usage_pct"] < 1.0
    assert status["should_prune"] is False
