"""Arg dataclass shared across all skills."""
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
