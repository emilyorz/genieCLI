"""R1 cartesian-join: explicit CROSS JOIN or comma-join without ON predicate."""
from __future__ import annotations

import re

from .. import Finding


def _line_of(sql: str, pattern: str) -> int:
    m = re.search(pattern, sql, re.IGNORECASE)
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
            for join in select.args.get("joins") or []:
                kind = (join.args.get("kind") or "").upper()
                side = (join.args.get("side") or "").upper()
                on = join.args.get("on")
                using = join.args.get("using")
                # Explicit CROSS JOIN
                if kind == "CROSS":
                    line = _line_of(sql, r"\bCROSS\s+JOIN\b")
                    if line not in seen_lines:
                        seen_lines.add(line)
                        findings.append(Finding(
                            severity="high",
                            rule_id="cartesian-join",
                            message="CROSS JOIN produces a cartesian product — every row × every row",
                            suggestion="Replace with INNER/LEFT JOIN ... ON <predicate>; if cartesian is intended, document why",
                            line=line,
                        ))
                    continue
                # Implicit cartesian: no kind/side, no ON, no USING
                if not kind and not side and on is None and not using:
                    line = _line_of(sql, r"\bJOIN\b")
                    if line not in seen_lines:
                        seen_lines.add(line)
                        findings.append(Finding(
                            severity="high",
                            rule_id="cartesian-join",
                            message="JOIN without ON/USING clause yields a cartesian product",
                            suggestion="Add an ON <predicate> clause to specify the join condition",
                            line=line,
                        ))

    # Comma-separated FROM (FROM a, b) — implicit cross join
    pattern = r"\bFROM\b\s+[\w.\"]+(?:\s+(?:AS\s+)?\w+)?\s*,\s*[\w.\"]+"
    for m in re.finditer(pattern, sql, re.IGNORECASE):
        line = sql[: m.start()].count("\n") + 1
        if line in seen_lines:
            continue
        seen_lines.add(line)
        findings.append(Finding(
            severity="high",
            rule_id="cartesian-join",
            message="Comma-separated tables in FROM (implicit cross join) — easy to miss missing predicates",
            suggestion="Use explicit JOIN ... ON syntax to make the join condition explicit",
            line=line,
        ))

    return findings
