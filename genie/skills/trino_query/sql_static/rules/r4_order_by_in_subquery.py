"""R4 unnecessary-order-by-in-subquery: ORDER BY inside a subquery / CTE without LIMIT."""
from __future__ import annotations

import re

from .. import Finding
from ..rule_ids import RULE_UNNECESSARY_ORDER_BY_IN_SUBQUERY


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
        # Walk every Select that is NOT the top-level / outermost result Select
        for select in stmt.find_all(exp.Select):
            order = select.args.get("order")
            if order is None:
                continue
            # A LIMIT/OFFSET makes the ORDER BY meaningful (top-N inside the subquery)
            if select.args.get("limit") is not None or select.args.get("offset") is not None:
                continue
            # Top-level ordering is fine
            parent = select.parent
            in_subquery = False
            while parent is not None:
                if isinstance(parent, (exp.Subquery, exp.CTE)):
                    in_subquery = True
                    break
                parent = parent.parent
            if not in_subquery:
                continue

            order_sql = order.sql()
            line = 1
            m = re.search(r"\bORDER\s+BY\b", sql, re.IGNORECASE)
            if m:
                line = sql[: m.start()].count("\n") + 1

            key = (line, order_sql)
            if key in seen_lines:
                continue
            seen_lines.add(key)
            findings.append(Finding(
                severity="low",
                rule_id=RULE_UNNECESSARY_ORDER_BY_IN_SUBQUERY,
                message="ORDER BY inside a subquery/CTE without LIMIT — Trino is free to discard the ordering",
                suggestion="Move ORDER BY to the outermost SELECT, or pair the inner ORDER BY with a LIMIT",
                line=line,
            ))
    return findings
