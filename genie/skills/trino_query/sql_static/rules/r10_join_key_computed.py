"""R10 join-key-computed: a function/cast/arithmetic wraps a JOIN ON key.

`JOIN t ON UPPER(a.x) = b.y` / `ON CAST(a.id AS varchar) = b.id` / `ON a.x + 1 = b.k`
computes a value on one side of a join-key equality. The planner cannot hash-join
on a computed key, so it falls back to a per-row recompute / nested-loop style join
— often the single most expensive thing in a slow query. R6/R9 cover WHERE-side
pushdown and filter timing; none of R1-R9 inspect the JOIN ON predicate itself.
"""
from __future__ import annotations

import re

from .. import Finding
from ..rule_ids import RULE_JOIN_KEY_COMPUTED


def _line_of(sql: str, needle: str) -> int:
    m = re.search(re.escape(needle), sql, re.IGNORECASE)
    return sql[: m.start()].count("\n") + 1 if m else 1


def apply(sql: str, statements: list) -> list[Finding]:
    try:
        import sqlglot.expressions as exp
    except ImportError:
        return []

    # A side is a "computed key" when it is NOT a bare column but still references
    # one — i.e. a function / cast / arithmetic wrapping a column. A bare column or
    # a literal is fine.
    def _is_bare_column(node) -> bool:
        return isinstance(node, exp.Column)

    def _is_computed_on_column(node) -> bool:
        if isinstance(node, (exp.Column, exp.Literal, exp.Null)):
            return False
        if isinstance(node, (exp.Func, exp.Cast, exp.Binary, exp.Paren)):
            return node.find(exp.Column) is not None
        return False

    findings: list[Finding] = []
    seen: set[tuple[int, str]] = set()

    for stmt in statements:
        if stmt is None:
            continue
        for join in stmt.find_all(exp.Join):
            on = join.args.get("on")
            if on is None:
                continue
            for eq in on.find_all(exp.EQ):
                left, right = eq.left, eq.right
                left_computed = _is_computed_on_column(left)
                right_computed = _is_computed_on_column(right)
                if not (left_computed or right_computed):
                    continue

                both_computed = left_computed and right_computed
                # Symmetric wrap (e.g. LOWER(a.x) = LOWER(b.x)) is often an
                # intentional normalized join — still worth flagging (it blocks a
                # raw hash key) but at lower severity than a one-sided compute.
                symmetric = (
                    both_computed
                    and type(left).__name__ == type(right).__name__
                )
                # One side computed, the other must be a bare column (a real join
                # key comparison) — not two unrelated computed expressions.
                if not both_computed:
                    other = right if left_computed else left
                    if not _is_bare_column(other):
                        continue

                expr_sql = eq.sql()
                line = _line_of(sql, expr_sql.split("=")[0].strip())
                key = (line, expr_sql)
                if key in seen:
                    continue
                seen.add(key)

                if symmetric:
                    findings.append(Finding(
                        severity="low",
                        rule_id=RULE_JOIN_KEY_COMPUTED,
                        message=(
                            f"both join keys are computed in ON ({expr_sql}) — the planner "
                            "cannot hash-join on a computed key; a symmetric normalization "
                            "still forces a recompute on every row of both inputs"
                        ),
                        suggestion=(
                            "If the normalization is intentional, materialize the normalized "
                            "value as a stored column upstream so the join key is raw; "
                            "otherwise join on the raw columns"
                        ),
                        line=line,
                    ))
                else:
                    findings.append(Finding(
                        severity="medium",
                        rule_id=RULE_JOIN_KEY_COMPUTED,
                        message=(
                            f"a function/cast/arithmetic wraps a join key in ON ({expr_sql}) — "
                            "the planner cannot hash-join on a computed key, forcing a per-row "
                            "recompute / nested-loop style join"
                        ),
                        suggestion=(
                            "Join on the raw column; if the value must be normalized/cast, "
                            "store the normalized value as a column upstream so the join key is raw"
                        ),
                        line=line,
                    ))

    return findings
