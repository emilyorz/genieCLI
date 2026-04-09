"""Tests for genie.core.model_profiles."""
import pytest
from genie.core.model_profiles import get_profile, estimate_tokens


def test_exact_match_gemini_flash():
    p = get_profile("gemini-2.5-flash")
    assert p.name == "gemini-2.5-flash"
    assert p.skill_tier == "core"
    assert p.context_window == 1_048_576


def test_exact_match_gpt4o():
    p = get_profile("gpt-4o")
    assert p.skill_tier == "full"
    assert p.supports_vision is True
    assert p.supports_tool_calls is True


def test_exact_match_ollama_qwen():
    p = get_profile("qwen3.5:4b")
    assert p.skill_tier == "core"
    assert p.context_window == 32_768


def test_pattern_fallback_gemini_variant():
    p = get_profile("gemini-2.0-flash-exp")
    assert p.skill_tier == "core"


def test_pattern_fallback_claude_variant():
    p = get_profile("claude-sonnet-4.5-20260801")
    assert p.skill_tier == "full"


def test_unknown_model_returns_default():
    p = get_profile("totally-unknown-model-xyz")
    assert p.skill_tier == "core"
    assert p.context_window == 8_192


def test_case_insensitive():
    p = get_profile("GPT-4o")
    assert p.skill_tier == "full"


def test_estimate_tokens_nonempty():
    tokens = estimate_tokens("Hello, this is a test string with some words in it.", "gpt-4o")
    assert tokens > 0
    assert tokens < 100


def test_estimate_tokens_empty():
    tokens = estimate_tokens("", "gpt-4o")
    assert tokens >= 1  # min 1
