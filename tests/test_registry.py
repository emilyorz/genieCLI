"""Tests for SkillRegistry."""
from __future__ import annotations

import pytest
from genie.core.arg import Arg
from genie.core.context import SkillContext
from genie.core.registry import BaseSkill, SkillRegistry


class _AddSkill(BaseSkill):
    name = "_test_add"
    description = "Add two numbers"
    group = "test"
    args = [
        Arg(name="a", type="int", description="First number"),
        Arg(name="b", type="int", description="Second number"),
    ]

    def run(self, a=0, b=0) -> str:
        return str(int(a) + int(b))


class _EchoSkill(BaseSkill):
    name = "_test_echo"
    description = "Echo text"
    group = "test"
    args = [Arg(name="text", type="str", description="Text to echo")]

    def run(self, text="") -> str:
        return text


@pytest.fixture(autouse=True)
def clean_registry():
    """Restore registry state between tests."""
    original = dict(SkillRegistry._skills)
    yield
    SkillRegistry._skills.clear()
    SkillRegistry._skills.update(original)


def test_register_and_get():
    SkillRegistry.register(_AddSkill())
    skill = SkillRegistry.get("_test_add")
    assert skill is not None
    assert skill.name == "_test_add"


def test_all_returns_list():
    SkillRegistry.register(_AddSkill())
    SkillRegistry.register(_EchoSkill())
    all_skills = SkillRegistry.all()
    names = [s.name for s in all_skills]
    assert "_test_add" in names
    assert "_test_echo" in names


def test_run_tool_success(null_sink, fake_provider):
    SkillRegistry.register(_AddSkill())
    ctx = SkillContext(provider=fake_provider, output=null_sink, config={})
    result = SkillRegistry.run_tool("_test_add", {"a": 3, "b": 4}, ctx)
    assert result == "7"


def test_run_tool_unknown(null_sink, fake_provider):
    ctx = SkillContext(provider=fake_provider, output=null_sink, config={})
    result = SkillRegistry.run_tool("_nonexistent_xyz", {}, ctx)
    assert "Unknown" in result or "nonexistent" in result.lower()


def test_validate_missing_required():
    skill = _AddSkill()
    ok, err = skill.validate({})
    assert not ok
    assert "a" in err


def test_validate_ok():
    skill = _AddSkill()
    ok, err = skill.validate({"a": 1, "b": 2})
    assert ok
    assert err is None


# ── clear() hook tests ────────────────────────────────────────────────────────

def test_clear_invokes_registered_hook():
    """clear() must call all registered hooks."""
    called = []
    SkillRegistry.register_clear_hook(lambda: called.append(1))
    SkillRegistry.register(_AddSkill())
    SkillRegistry.clear()
    assert called == [1]
    assert SkillRegistry.get("_test_add") is None


def test_clear_hook_not_duplicated():
    """Registering the same hook twice must not call it twice."""
    called = []
    hook = lambda: called.append(1)
    SkillRegistry.register_clear_hook(hook)
    SkillRegistry.register_clear_hook(hook)
    SkillRegistry.clear()
    assert called == [1]


def test_cli_discovery_flag_reset_on_clear():
    """SkillRegistry.clear() must also reset cli._skills_discovered via the hook."""
    import genie.cli as cli_mod
    cli_mod._skills_discovered = True
    SkillRegistry.clear()
    assert cli_mod._skills_discovered is False


# ── SKILL.md support tests ───────────────────────────────────────────────────

def test_parse_skill_md_valid(tmp_path):
    """parse_skill_md extracts YAML frontmatter from a SKILL.md file."""
    from genie.core.registry import parse_skill_md
    md = tmp_path / "SKILL.md"
    md.write_text(
        "---\nname: test-skill\ndescription: A test skill\n"
        "version: 1.0.0\ngroup: test\ntier: core\n---\n\n# Test Skill\n"
    )
    data = parse_skill_md(md)
    assert data["name"] == "test-skill"
    assert data["tier"] == "core"
    assert data["group"] == "test"


def test_parse_skill_md_no_frontmatter(tmp_path):
    """parse_skill_md returns {} when no frontmatter is present."""
    from genie.core.registry import parse_skill_md
    md = tmp_path / "SKILL.md"
    md.write_text("# Just a heading\nNo frontmatter here.\n")
    assert parse_skill_md(md) == {}


def test_discover_accepts_skill_md(tmp_path, monkeypatch):
    """discover() should accept a dir with SKILL.md + __init__.py (no skill.toml)."""
    skill_dir = tmp_path / "fake_skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        "---\nname: fake\ndescription: fake\n---\n"
    )
    (skill_dir / "__init__.py").write_text(
        "from genie.core.registry import BaseSkill, SkillRegistry\n"
        "class FakeSkill(BaseSkill):\n"
        "    name = '_test_discover_md'\n"
        "    description = 'fake'\n"
        "    def run(self, **kw): return 'ok'\n"
        "def register(reg): reg.register(FakeSkill())\n"
    )
    # Make the fake skill importable
    monkeypatch.syspath_prepend(str(tmp_path))
    import sys
    sys.modules.pop("genie.skills.fake_skill", None)
    # Patch the package name construction
    monkeypatch.setattr(
        "genie.core.registry._load_skill_package",
        lambda d: __import__(d.name).register(SkillRegistry),
    )
    SkillRegistry.discover([tmp_path])
    assert SkillRegistry.get("_test_discover_md") is not None


def test_discover_legacy_absent_module_is_silent():
    """A completely missing legacy package must not warn (optional path)."""
    import warnings
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        SkillRegistry.discover_legacy("definitely_not_a_real_pkg_xyzzy")
    assert not any("discovery failed" in str(w.message) for w in caught)


def test_discover_legacy_broken_module_warns(tmp_path, monkeypatch):
    """A legacy package that exists but fails to import must warn so the
    user sees why their catalog is empty, rather than silently returning []."""
    import sys
    import warnings

    pkg = tmp_path / "broken_legacy_pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("raise ImportError('simulated breakage')\n")
    monkeypatch.syspath_prepend(str(tmp_path))
    sys.modules.pop("broken_legacy_pkg", None)

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        SkillRegistry.discover_legacy("broken_legacy_pkg")
    assert any("discovery failed" in str(w.message) for w in caught)
