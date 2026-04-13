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


# ── Browser core tier coherence ──────────────────────────────────────────────

class TestBrowserCoreTierCoherence:
    """The core tier must give Flash 2.5 a coherent snapshot-based workflow.

    browser_snapshot produces numbered element IDs, so the tools that consume
    those IDs (click_element, type_element) must also be core.  The raw CSS-
    selector tools (click, type) should NOT be core — they conflict with the
    snapshot paradigm and confuse weaker models.
    """

    @pytest.fixture(autouse=True)
    def _load_browser(self):
        """Load real browser skills for this test class."""
        from pathlib import Path
        SkillRegistry.clear()
        SkillRegistry.discover([Path("genie/skills")])
        yield
        SkillRegistry.clear()

    def _core_browser_names(self) -> set[str]:
        core = SkillRegistry.all(tier="core")
        return {s.name for s in core if s.name.startswith("browser_")}

    def test_snapshot_workflow_tools_are_core(self):
        """snapshot + click_element + type_element must all be core."""
        names = self._core_browser_names()
        assert "browser_snapshot" in names
        assert "browser_click_element" in names
        assert "browser_type_element" in names

    def test_raw_css_tools_are_not_core(self):
        """Raw CSS-selector click/type should be extended, not core."""
        names = self._core_browser_names()
        assert "browser_click" not in names, "browser_click should be extended"
        assert "browser_type" not in names, "browser_type should be extended"

    def test_core_browser_count(self):
        """Core browser tools should be exactly 10 — tight, coherent set."""
        names = self._core_browser_names()
        assert len(names) == 10, f"Expected 10 core browser tools, got {len(names)}: {names}"
