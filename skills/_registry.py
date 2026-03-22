from __future__ import annotations

import importlib
import inspect
import pkgutil

from .base import BaseSkill


class SkillRegistry:
    _skills: dict[str, BaseSkill] = {}

    @classmethod
    def register(cls, skill: BaseSkill) -> None:
        if skill.name in cls._skills:
            raise ValueError(f"Duplicate skill name: '{skill.name}'")
        cls._skills[skill.name] = skill

    @classmethod
    def get(cls, name: str) -> BaseSkill | None:
        return cls._skills.get(name)

    @classmethod
    def by_group(cls, group: str) -> list[BaseSkill]:
        return [s for s in cls._skills.values() if s.group == group]

    @classmethod
    def all(cls) -> list[BaseSkill]:
        return list(cls._skills.values())

    @classmethod
    def groups(cls) -> list[str]:
        return list({s.group for s in cls._skills.values()})

    @classmethod
    def discover(cls, package: str = "skills") -> None:
        import sys

        pkg = sys.modules.get(package) or importlib.import_module(package)

        for _finder, module_name, _is_pkg in pkgutil.iter_modules(pkg.__path__):
            if module_name.startswith("_"):
                continue
            full_name = f"{package}.{module_name}"
            module = importlib.import_module(full_name)
            for _obj_name, obj in inspect.getmembers(module, inspect.isclass):
                if (
                    issubclass(obj, BaseSkill)
                    and obj is not BaseSkill
                    and obj.name
                    and obj.name not in cls._skills
                ):
                    cls.register(obj())
