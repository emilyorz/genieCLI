"""R5 subquery-in-select-pushable-to-join: scalar subquery in projection list."""
from __future__ import annotations

import re

from .. import Finding


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
        for select in stmt.find_all(exp.Select):
            for proj in select.expressions or []:
                target = proj.unalias() if hasattr(proj, "unalias") else proj
                # Direct scalar subquery in a projection slot
                sub = target if isinstance(target, exp.Subquery) else None
                if sub is None:
                    # Walk one level down for things like (SELECT ...) + 1
                    for child in target.walk():
                        if isinstance(child, exp.Subquery):
                            sub = child
                            break
                if sub is None:
                    continue

                line = 1
                snippet = sub.sql()
                m = re.search(r"\bSELECT\b", snippet, re.IGNORECASE)
                if m:
                    # Locate the subquery by its first SELECT inside the original SQL
                    inner_sel = sub.find(exp.Select)
                    if inner_sel is not None:
                        # Best-effort: find the first SELECT after the projection list start
                        first = re.search(r"\(\s*SELECT\b", sql, re.IGNORECASE)
                        if first:
                            line = sql[: first.start()].count("\n") + 1

                if line in seen_lines:
                    continue
                seen_lines.add(line)
                findings.append(Finding(
                    severity="medium",
                    rule_id="subquery-in-select-pushable-to-join",
                    message="Scalar subquery in SELECT projection — Trino re-evaluates it per outer row when correlated",
                    suggestion="Rewrite as a LEFT JOIN against an aggregated subquery so the inner scan runs once",
                    line=line,
                ))
    return findings
