"""R7 null-unsafe-equals: ``col = NULL`` / ``col != NULL`` instead of IS [NOT] NULL."""
from __future__ import annotations

import re

from .. import Finding
from ..rule_ids import RULE_NULL_UNSAFE_EQUALS


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
        for cmp_node in stmt.find_all(exp.EQ, exp.NEQ):
            left, right = cmp_node.left, cmp_node.right
            if isinstance(left, exp.Null) or isinstance(right, exp.Null):
                expr_sql = cmp_node.sql()
                # Locate in original SQL
                m = re.search(re.escape(expr_sql.split("=")[0].strip()) + r"\s*(=|<>|!=)\s*NULL\b", sql, re.IGNORECASE)
                line = sql[: m.start()].count("\n") + 1 if m else 1
                key = (line, expr_sql)
                if key in seen_lines:
                    continue
                seen_lines.add(key)
                op = "=" if isinstance(cmp_node, exp.EQ) else "<>"
                replacement = "IS NULL" if isinstance(cmp_node, exp.EQ) else "IS NOT NULL"
                findings.append(Finding(
                    severity="high",
                    rule_id=RULE_NULL_UNSAFE_EQUALS,
                    message=f"`{op} NULL` always evaluates UNKNOWN — the predicate is silently false",
                    suggestion=f"Use `{replacement}` for null checks",
                    line=line,
                ))
    return findings
