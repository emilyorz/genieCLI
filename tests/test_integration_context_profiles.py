"""Integration tests for context manager, model profiles, and skill tiers."""
from __future__ import annotations

import pytest

from genie.core.context_manager import ContextManager
from genie.core.model_profiles import get_profile
from genie.core.registry import BaseSkill, SkillRegistry


class _CoreSkill(BaseSkill):
    name = "_test_core"
    description = "test core"
    tier = "core"

    def run(self, **kwargs):
        return "ok"


class _ExtendedSkill(BaseSkill):
    name = "_test_extended"
    description = "test extended"
    tier = "extended"

    def run(self, **kwargs):
        return "ok"


class _FullSkill(BaseSkill):
    name = "_test_full"
    description = "test full"
    tier = "full"

    def run(self, **kwargs):
        return "ok"


@pytest.fixture(autouse=True)
def _seed_registry():
    """Keep registry state deterministic for this module."""
    SkillRegistry.clear()
    SkillRegistry.register(_CoreSkill())
    SkillRegistry.register(_ExtendedSkill())
    SkillRegistry.register(_FullSkill())
    yield
    SkillRegistry.clear()


def _make_msg(role: str, text: str) -> dict:
    return {
        "role": role,
        "content": [{"type": "text", "text": text, "reasonText": None}],
    }


@pytest.mark.parametrize(
    "model_name",
    ["gpt-4o", "gemini-2.5-flash", "qwen3.5:4b"],
)
def test_context_manager_respects_model_profile(model_name: str):
    """ContextManager should use the exact model profile context window."""
    profile = get_profile(model_name)
    cm = ContextManager(model_name=model_name)
    assert cm.context_window == profile.context_window


def test_weak_model_has_different_context_limits_than_strong():
    """Weak models have smaller context windows than strong models."""
    cm_weak = ContextManager(model_name="qwen3.5:4b")
    cm_strong = ContextManager(model_name="gemini-2.5-flash")

    # Weak model should have less available context
    assert cm_weak.context_window < cm_strong.context_window
    assert cm_weak.available_for_history < cm_strong.available_for_history


def test_model_profile_skill_tier_mapping():
    """Model profiles should have correct skill tier mappings."""
    profiles = {
        "gemini-2.5-flash": "core",
        "gpt-4o": "full",
        "claude-opus-4": "full",
        "qwen3.5:4b": "core",
    }

    for model_name, expected_tier in profiles.items():
        profile = get_profile(model_name)
        assert profile.skill_tier == expected_tier, (
            f"{model_name} should have tier {expected_tier}, got {profile.skill_tier}"
        )


def test_skill_filtering_by_model_tier():
    """Skill registry filtering by tier should actually narrow the registry."""
    all_skills = {skill.name for skill in SkillRegistry.all(tier=None)}
    core_skills = {skill.name for skill in SkillRegistry.all(tier="core")}
    extended_skills = {skill.name for skill in SkillRegistry.all(tier="extended")}
    full_skills = {skill.name for skill in SkillRegistry.all(tier="full")}

    assert all_skills == {"_test_core", "_test_extended", "_test_full"}
    assert core_skills == {"_test_core"}
    assert extended_skills == {"_test_core", "_test_extended"}
    assert full_skills == all_skills
    assert core_skills < all_skills
    assert extended_skills < all_skills


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
