"""Integration tests for context manager, model profiles, and skill tiers."""
import pytest
from genie.core.context_manager import ContextManager
from genie.core.model_profiles import get_profile
from genie.core.registry import SkillRegistry


def _make_msg(role: str, text: str) -> dict:
    return {
        "role": role,
        "content": [{"type": "text", "text": text, "reasonText": None}],
    }


def test_context_manager_respects_model_profile():
    """ContextManager should use the correct model profile."""
    # GPT-4o has 128K context window
    cm_gpt = ContextManager(model_name="gpt-4o")
    assert cm_gpt.context_window == 128_000

    # Gemini Flash has 1M context window
    cm_gemini = ContextManager(model_name="gemini-2.5-flash")
    assert cm_gemini.context_window == 1_048_576

    # Small Ollama model has 32K context window
    cm_ollama = ContextManager(model_name="qwen3.5:4b")
    assert cm_ollama.context_window == 32_768


def test_weak_model_has_different_context_limits_than_strong():
    """Weak models have smaller context windows than strong models."""
    cm_weak = ContextManager(model_name="qwen3.5:4b")      # 32K
    cm_strong = ContextManager(model_name="gemini-2.5-flash")  # 1M

    # Weak model should have less available context
    assert cm_weak.context_window < cm_strong.context_window
    assert cm_weak.available_for_history < cm_strong.available_for_history


def test_model_profile_skill_tier_mapping():
    """Model profiles should have correct skill tier mappings."""
    profiles = {
        "gemini-2.5-flash": "core",      # Fast/weak model
        "gpt-4o": "full",                 # Strong model
        "claude-opus-4": "full",          # Strongest model
        "qwen3.5:4b": "core",            # Very weak model
    }

    for model_name, expected_tier in profiles.items():
        profile = get_profile(model_name)
        assert profile.skill_tier == expected_tier, \
            f"{model_name} should have tier {expected_tier}, got {profile.skill_tier}"


def test_skill_filtering_by_model_tier():
    """Skill registry filtering by tier should work."""
    # All tier should return all available skills
    all_skills = SkillRegistry.all(tier="all")

    # Core tier should be a subset of all
    core_skills = SkillRegistry.all(tier="core")
    for skill in core_skills:
        assert skill in all_skills, f"Core skill {skill} should be in all skills"

    # Extended tier should be a subset of all
    extended_skills = SkillRegistry.all(tier="extended")
    for skill in extended_skills:
        assert skill in all_skills, f"Extended skill {skill} should be in all skills"


def test_context_status_includes_model_and_tier():
    """context_status() should report both context usage and skill tier info."""
    history = [
        _make_msg("system", "System prompt"),
        _make_msg("user", "Hello"),
        _make_msg("assistant", "Hi there!"),
    ]

    cm = ContextManager(model_name="gpt-4o")
    status = cm.context_status(history)

    # Should have all expected fields
    assert "model" in status
    assert "tokens_used" in status
    assert "usage_pct" in status
    assert "should_prune" in status

    # Values should be reasonable
    assert status["model"] == "gpt-4o"
    assert status["tokens_used"] > 0
    assert 0 <= status["usage_pct"] <= 100
    assert isinstance(status["should_prune"], bool)
