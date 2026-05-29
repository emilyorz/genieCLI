"""R2 select-star: SELECT * outside COUNT(*) — wide reads + brittle to schema drift."""
from __future__ import annotations

import re

from .. import Finding
from ..rule_ids import RULE_SELECT_STAR


def apply(sql: str, statements: list) -> list[Finding]:
    try:
        import sqlglot.expressions as exp
    except ImportError:
        return []

    findings: list[Finding] = []
    seen_lines: set[int] = set()
    search_pos = 0

    for stmt in statements:
        if stmt is None:
            continue
        for select in stmt.find_all(exp.Select):
            has_bare_star = False
            for star in select.find_all(exp.Star):
                parent = star.parent
                in_count = False
                while parent is not None and parent is not select:
                    if isinstance(parent, exp.Count):
                        in_count = True
                        break
                    parent = parent.parent
                if not in_count:
                    has_bare_star = True
                    break
            if not has_bare_star:
                continue

            line = 1
            for m in re.finditer(r"\*", sql[search_pos:]):
                pos = m.start() + search_pos
                before = sql[max(0, pos - 50): pos]
                if re.search(r"\bCOUNT\s*\(\s*$", before, re.IGNORECASE):
                    continue
                line = sql[:pos].count("\n") + 1
                search_pos = pos + 1
                break

            if line in seen_lines:
                continue
            seen_lines.add(line)
            findings.append(Finding(
                severity="medium",
                rule_id=RULE_SELECT_STAR,
                message="SELECT * reads every column — wider IO and brittle to schema drift",
                suggestion="List columns explicitly so the planner can prune projections and the query survives schema changes",
                line=line,
            ))
    return findings
