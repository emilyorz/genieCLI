"""Verify skill discovery works after browser/deepwiki removal."""
from pathlib import Path

import pytest

from genie.core.registry import SkillRegistry


@pytest.fixture(autouse=True)
def _clean():
    SkillRegistry.clear()
    yield
    SkillRegistry.clear()


def test_discover_finds_remaining_skills():
    """After cutting browser/deepwiki, discovery should still find all remaining skills."""
    bundled = Path(__file__).parent.parent / "genie" / "skills"
    SkillRegistry.discover([bundled])
    names = {s.name for s in SkillRegistry.all()}

    # Core skills that must exist (lint_trino_sql merged into oracle2trino)
    assert "lint_trino_sql" in names
    # oracle2trino tools
    assert "transpile_sql" in names

    # Removed skills must NOT appear
    for name in names:
        assert not name.startswith("browser_"), f"browser skill leaked: {name}"
        assert "deepwiki" not in name, f"deepwiki skill leaked: {name}"
        assert name != "trino_linter", f"trino_linter should be merged into oracle2trino"


def test_no_browser_directory():
    """Browser skill directory should be gone."""
    browser_dir = Path(__file__).parent.parent / "genie" / "skills" / "browser"
    assert not browser_dir.exists(), "browser/ directory should have been deleted"


def test_no_deepwiki_directory():
    """Deepwiki skill directory should be gone."""
    deepwiki_dir = Path(__file__).parent.parent / "genie" / "skills" / "deepwiki"
    assert not deepwiki_dir.exists(), "deepwiki/ directory should have been deleted"
