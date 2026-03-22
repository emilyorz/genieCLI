from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Arg:
    name: str
    type: str
    description: str
    required: bool = True
    default: Any = None
    choices: list[str] | None = None


class BaseSkill:
    name: str = ""
    description: str = ""
    group: str = "generic"
    args: list[Arg] = []

    def run(self, **kwargs):
        raise NotImplementedError

    def validate(self, kwargs: dict) -> tuple[bool, str | None]:
        for arg in self.args:
            if arg.required and arg.name not in kwargs:
                return False, f"Missing required argument: '{arg.name}'"
            if arg.choices and arg.name in kwargs:
                if kwargs[arg.name] not in arg.choices:
                    return False, (
                        f"Invalid value for '{arg.name}': "
                        f"got '{kwargs[arg.name]}', must be one of {arg.choices}"
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
