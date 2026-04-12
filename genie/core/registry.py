"""SkillRegistry — plugin discovery and dispatch."""
from __future__ import annotations

import importlib
import importlib.util
import sys
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from genie.core.context import SkillContext


class BaseSkill:
    """Base class for all skills (bundled and external).

    tier controls when this skill is loaded based on model capability:
      - "core":     always loaded — essential tools for any model
      - "extended": loaded for mid-tier+ models (e.g. GPT-4o-mini, Gemini Pro)
      - "full":     loaded only for top-tier models (e.g. GPT-4o, Claude)
    """

    name: str = ""
    description: str = ""
    group: str = "generic"
    tier: str = "core"  # "core" | "extended" | "full"
    args: list = []

    def run(self, **kwargs) -> str:
        raise NotImplementedError

    def validate(self, kwargs: dict) -> tuple[bool, str | None]:
        for arg in self.args:
            if arg.required and arg.name not in kwargs:
                return False, f"Missing required argument: '{arg.name}'"
            if arg.choices and arg.name in kwargs:
                if kwargs[arg.name] not in arg.choices:
                    return (
                        False,
                        f"Invalid value for '{arg.name}': "
                        f"got '{kwargs[arg.name]}', must be one of {arg.choices}",
                    )
        return True, None

    def spec(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "group": self.group,
            "args": [
                {
                    "name": arg.name,
                    "type": arg.type,
                    "description": arg.description,
                    "required": arg.required,
                    "default": arg.default,
                    "choices": arg.choices,
                }
                for arg in self.args
            ],
        }

    def tools(self) -> list[dict]:
        return [self.spec()]

    def run_tool(self, name: str, args: dict, ctx: "SkillContext") -> str:
        if name == self.name:
            ok, err = self.validate(args)
            if not ok:
                return f"Validation error for '{name}': {err}"
            try:
                return self.run(**args)
            except TypeError as exc:
                import inspect
                sig = inspect.signature(self.run)
                return f"Wrong args for {name}: {exc}. Expected: {sig}"
            except Exception as exc:
                return f"Tool error ({name}): {exc}"
        return f"Unknown tool: '{name}'"

    def contribute_commands(self, app) -> None:
        """Hook for skills to register Typer subcommands. Default: no-op."""


class SkillRegistry:
    """Singleton skill registry — all skills live in a shared class-level dict."""

    _skills: dict[str, BaseSkill] = {}
    _clear_hooks: list = []  # callbacks invoked by clear() for external state reset

    @classmethod
    def register(cls, skill: BaseSkill) -> None:
        cls._skills[skill.name] = skill

    @classmethod
    def register_clear_hook(cls, hook) -> None:
        """Register a callable to be invoked when clear() is called.

        Use this to reset external state (e.g. discovery flags in cli.py) that
        must stay in sync with the registry contents.  Each hook is called with
        no arguments immediately after _skills is cleared.
        """
        if hook not in cls._clear_hooks:
            cls._clear_hooks.append(hook)

    @classmethod
    def clear(cls) -> None:
        """Remove all registered skills and invoke registered clear hooks.

        Primarily for test isolation.  Callers that maintain discovery flags or
        other state coupled to the registry should register a hook via
        register_clear_hook() so clear() resets that state too.
        """
        cls._skills.clear()
        for hook in cls._clear_hooks:
            hook()

    @classmethod
    def get(cls, name: str) -> BaseSkill | None:
        return cls._skills.get(name)

    @classmethod
    def all(cls, tier: str | None = None) -> list[BaseSkill]:
        """Return all skills, optionally filtered by tier ceiling.

        tier="core"     → only core skills
        tier="extended" → core + extended
        tier="full"     → everything (default)
        tier=None       → everything
        """
        if tier is None or tier == "full":
            return list(cls._skills.values())

        tier_order = {"core": 0, "extended": 1, "full": 2}
        max_tier = tier_order.get(tier, 2)
        return [
            s for s in cls._skills.values()
            if tier_order.get(getattr(s, "tier", "core"), 0) <= max_tier
        ]

    @classmethod
    def all_tools(cls) -> list[dict]:
        tools = []
        for skill in cls._skills.values():
            tools.extend(skill.tools())
        return tools

    @classmethod
    def run_tool(cls, name: str, args: dict, ctx: "SkillContext") -> str:
        skill = cls._skills.get(name)
        if not skill:
            available = ", ".join(cls._skills.keys())
            return f"Unknown tool: '{name}'. Available: {available}"
        return skill.run_tool(name, args, ctx)

    @classmethod
    def discover(cls, paths: list[Path]) -> None:
        """Scan directories for SKILL.md + __init__.py skill packages.

        Each subdirectory with both a SKILL.md (Anthropic-style metadata)
        and an __init__.py (Python execution layer) is loaded as a skill.
        Legacy skill.toml is also accepted as a fallback marker.
        """
        for base in paths:
            base = Path(base)
            if not base.is_dir():
                continue
            for skill_dir in sorted(base.iterdir()):
                if not skill_dir.is_dir():
                    continue
                init_file = skill_dir / "__init__.py"
                skill_md = skill_dir / "SKILL.md"
                toml_file = skill_dir / "skill.toml"
                if not init_file.exists():
                    continue
                if not skill_md.exists() and not toml_file.exists():
                    continue
                _load_skill_package(skill_dir)

    @classmethod
    def discover_legacy(cls, module_name: str) -> None:
        """Import a legacy module that registers skills via SkillRegistry.register().

        A missing top-level module is tolerated (the legacy package is
        optional), but an ImportError *inside* the module — e.g. a broken
        skill — is surfaced via warnings so callers can see why their
        catalog is unexpectedly empty.
        """
        try:
            importlib.import_module(module_name)
        except ModuleNotFoundError as exc:
            if exc.name == module_name:
                return  # legacy package genuinely absent — fine
            import warnings
            warnings.warn(
                f"Legacy skill discovery failed inside '{module_name}': {exc}",
                stacklevel=2,
            )
        except ImportError as exc:
            import warnings
            warnings.warn(
                f"Legacy skill discovery failed for '{module_name}': {exc}",
                stacklevel=2,
            )


def _load_skill_package(skill_dir: Path) -> None:
    """Import a skill package and call its register() function if present."""
    pkg_name = f"genie.skills.{skill_dir.name}"

    # Do NOT add genie/ to sys.path — it would shadow the legacy `skills` package.
    # genie is already importable because its root is in sys.path at startup.
    try:
        mod = importlib.import_module(pkg_name)
        if hasattr(mod, "register"):
            mod.register(SkillRegistry)
    except Exception as exc:
        import warnings
        warnings.warn(f"Failed to load skill '{skill_dir.name}': {exc}", stacklevel=2)


def parse_skill_md(path: Path) -> dict:
    """Parse SKILL.md YAML frontmatter into a dict.

    Returns an empty dict if the file has no valid frontmatter.
    """
    import yaml

    text = path.read_text(encoding="utf-8")
    if not text.startswith("---"):
        return {}
    end = text.find("---", 3)
    if end == -1:
        return {}
    frontmatter = text[3:end]
    try:
        data = yaml.safe_load(frontmatter)
        return data if isinstance(data, dict) else {}
    except yaml.YAMLError:
        return {}
