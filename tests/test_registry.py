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
