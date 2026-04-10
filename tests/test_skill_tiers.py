"""Tests for skill tier filtering in SkillRegistry."""
import pytest
from genie.core.registry import BaseSkill, SkillRegistry


class _CoreSkill(BaseSkill):
    name = "_test_core"
    description = "test core"
    tier = "core"
    def run(self): return "ok"


class _ExtendedSkill(BaseSkill):
    name = "_test_extended"
    description = "test extended"
    tier = "extended"
    def run(self): return "ok"


class _FullSkill(BaseSkill):
    name = "_test_full"
    description = "test full"
    tier = "full"
    def run(self): return "ok"


@pytest.fixture(autouse=True)
def _clean_registry():
    SkillRegistry.clear()
    SkillRegistry.register(_CoreSkill())
    SkillRegistry.register(_ExtendedSkill())
    SkillRegistry.register(_FullSkill())
    yield
    SkillRegistry.clear()


def test_all_returns_everything_by_default():
    skills = SkillRegistry.all()
    assert len(skills) == 3


def test_all_full_returns_everything():
    skills = SkillRegistry.all(tier="full")
    assert len(skills) == 3


def test_core_tier_filters_correctly():
    skills = SkillRegistry.all(tier="core")
    names = {s.name for s in skills}
    assert names == {"_test_core"}


def test_extended_tier_includes_core():
    skills = SkillRegistry.all(tier="extended")
    names = {s.name for s in skills}
    assert names == {"_test_core", "_test_extended"}


def test_none_tier_returns_all():
    skills = SkillRegistry.all(tier=None)
    assert len(skills) == 3
