"""Trino SQL static linter skill."""
from __future__ import annotations

import json

from genie.core.arg import Arg
from genie.core.registry import BaseSkill

from .analyzer import analyze


class TrinoLinter(BaseSkill):
    name = "trino_linter"
    description = (
        "Static SQL linter for Trino queries. "
        "Detects Oracle residuals (NVL, DECODE, ROWNUM, SYSDATE, (+) joins), "
        "SELECT *, implicit cross joins, leading wildcards, COUNT(DISTINCT), "
        "correlated subqueries, and missing partition filters. "
        "Returns structured findings with severity, rule, and fix suggestions."
    )
    group = "trino_linter"
    args = [
        Arg(
            name="sql",
            type="str",
            description="Trino SQL statement(s) to lint (multiple statements separated by ;)",
            required=True,
        )
    ]

    def run(self, sql: str = "") -> str:
        result = analyze(sql)
        return json.dumps(result.to_dict(), ensure_ascii=False, indent=2)


def register(registry) -> None:
    registry.register(TrinoLinter())
