"""R6 predicate-not-pushed-to-cte: pushdown_predicates() yields a different SQL."""
from __future__ import annotations

import copy
import re

from .. import Finding
from ..rule_ids import RULE_PREDICATE_NOT_PUSHED_TO_CTE


def apply(sql: str, statements: list) -> list[Finding]:
    try:
        import sqlglot
        import sqlglot.expressions as exp
        from sqlglot.optimizer.pushdown_predicates import pushdown_predicates
    except ImportError:
        return []

    findings: list[Finding] = []
    seen_lines: set[int] = set()

    for stmt in statements:
        if stmt is None:
            continue
        if not isinstance(stmt, (exp.Select, exp.With)):
            continue
        # Only meaningful when there's a CTE / subquery boundary that can absorb the predicate
        has_cte = stmt.find(exp.CTE) is not None
        has_subq_from = False
        for select in stmt.find_all(exp.Select):
            from_ = select.args.get("from_")
            if from_ is not None and isinstance(from_.this, exp.Subquery):
                has_subq_from = True
                break
        if not has_cte and not has_subq_from:
            continue

        try:
            before = stmt.sql(dialect="trino")
            optimized = pushdown_predicates(copy.deepcopy(stmt))
            after = optimized.sql(dialect="trino")
        except Exception:
            continue

        if before == after:
            continue

        line = 1
        m = re.search(r"\bWHERE\b", sql, re.IGNORECASE)
        if m:
            line = sql[: m.start()].count("\n") + 1
        if line in seen_lines:
            continue
        seen_lines.add(line)
        findings.append(Finding(
            severity="medium",
            rule_id=RULE_PREDICATE_NOT_PUSHED_TO_CTE,
            message="Outer WHERE predicate could be pushed into the CTE / subquery to scan less data",
            suggestion="Move the predicate inside the CTE/subquery so the inner scan filters early",
            line=line,
        ))
    return findings
