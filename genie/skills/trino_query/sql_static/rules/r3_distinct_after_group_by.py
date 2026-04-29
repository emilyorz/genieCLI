"""R3 redundant-distinct-after-group-by: SELECT DISTINCT … GROUP BY same keys."""
from __future__ import annotations

import re

from .. import Finding


def _line_of_distinct(sql: str) -> int:
    m = re.search(r"\bSELECT\s+DISTINCT\b", sql, re.IGNORECASE)
    return sql[: m.start()].count("\n") + 1 if m else 1


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
            distinct = select.args.get("distinct")
            group = select.args.get("group")
            if distinct is None or group is None:
                continue
            # GROUP BY already produces distinct rows over the grouped keys.
            # If every projection is either a grouped column or an aggregate,
            # DISTINCT is redundant.
            group_keys = {e.sql() for e in (group.expressions or [])}
            projections = select.expressions or []
            redundant = True
            for proj in projections:
                target = proj.unalias() if hasattr(proj, "unalias") else proj
                # Aggregates collapse rows — already unique per group
                if any(isinstance(node, exp.AggFunc) for node in target.walk()):
                    continue
                if target.sql() in group_keys:
                    continue
                redundant = False
                break
            if not redundant:
                continue

            line = _line_of_distinct(sql)
            if line in seen_lines:
                continue
            seen_lines.add(line)
            findings.append(Finding(
                severity="medium",
                rule_id="redundant-distinct-after-group-by",
                message="SELECT DISTINCT is redundant when GROUP BY already covers all projected non-aggregate columns",
                suggestion="Drop DISTINCT — GROUP BY guarantees distinct rows over its keys",
                line=line,
            ))
    return findings
