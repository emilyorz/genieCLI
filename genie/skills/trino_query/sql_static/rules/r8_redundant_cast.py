"""R8 redundant-cast-chain: CAST(CAST(x AS T1) AS T2) — at least one CAST is wasted."""
from __future__ import annotations

import re

from .. import Finding
from ..rule_ids import RULE_REDUNDANT_CAST_CHAIN


def apply(sql: str, statements: list) -> list[Finding]:
    try:
        import sqlglot.expressions as exp
    except ImportError:
        return []

    findings: list[Finding] = []
    seen_lines: set[int] = set()

    for stmt in statements:
        if stmt is None:
            continue
        for cast_node in stmt.find_all(exp.Cast):
            inner = cast_node.this
            if not isinstance(inner, exp.Cast):
                continue
            # Nested CAST
            outer_type = (cast_node.to.sql() if cast_node.to else "").upper()
            inner_type = (inner.to.sql() if inner.to else "").upper()
            line = 1
            m = re.search(r"\bCAST\s*\(\s*CAST\b", sql, re.IGNORECASE)
            if m:
                line = sql[: m.start()].count("\n") + 1
            key = (line, outer_type, inner_type)
            if key in seen_lines:
                continue
            seen_lines.add(key)
            same_type = outer_type == inner_type
            msg = (
                f"CAST(CAST(... AS {inner_type}) AS {outer_type}) — "
                + ("redundant: outer cast equals inner type" if same_type else "chained casts likely unnecessary")
            )
            findings.append(Finding(
                severity="low" if same_type else "low",
                rule_id=RULE_REDUNDANT_CAST_CHAIN,
                message=msg,
                suggestion="Cast the original expression directly to the final target type once",
                line=line,
            ))
    return findings
