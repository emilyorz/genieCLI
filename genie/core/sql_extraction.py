"""Shared SQL extraction utilities used by research modules."""
from __future__ import annotations

import re
from typing import Optional


def extract_sql_from_reply(reply: str) -> Optional[str]:
    """Extract SQL from an AI reply.

    Tries in order:
    1. Fenced ```sql ... ``` block
    2. Fenced ``` ... ``` block that looks like SQL
    3. None (no SQL found)
    """
    sql_blocks = re.findall(r"```sql\s*\n(.*?)```", reply, re.DOTALL | re.IGNORECASE)
    if sql_blocks:
        return sql_blocks[-1].strip().rstrip(";")

    generic_blocks = re.findall(r"```\s*\n(.*?)```", reply, re.DOTALL)
    for block in reversed(generic_blocks):
        block = block.strip()
        if any(kw in block.upper() for kw in ["SELECT", "WITH", "INSERT", "UPDATE", "DELETE"]):
            return block.rstrip(";")

    return None


def extract_ctas_inner_select(sql: str) -> Optional[str]:
    """Return the inner SELECT/WITH body of a CREATE TABLE ... AS statement.

    The optimization value of a CTAS lives entirely in its inner query, so
    callers strip the ``CREATE TABLE ... AS`` wrapper, run the read-only
    optimization steps on the inner query, then re-wrap. Returns ``None`` when
    the SQL is not a CTAS or the inner query cannot be isolated (the caller then
    falls back to analyzing the whole statement — never crash).

    Pure: sqlglot parse only, no cluster, no network. Never raises.
    """
    if not sql or not sql.strip():
        return None
    try:
        import sqlglot
        import sqlglot.expressions as exp
    except ImportError:
        return None

    try:
        tree = sqlglot.parse_one(sql, read="trino")
    except Exception:
        return None

    if not isinstance(tree, exp.Create):
        return None
    # CTAS carries the query in `.expression` (a Select, or a With wrapping one).
    inner = tree.expression
    if not isinstance(inner, (exp.Select, exp.With, exp.Union, exp.Subquery)):
        return None
    try:
        rendered = inner.sql(dialect="trino")
    except Exception:
        return None
    return rendered.strip() or None


def rewrap_ctas_inner_select(original_ctas_sql: str, new_inner_sql: str) -> Optional[str]:
    """Return the original CTAS with its inner query replaced by ``new_inner_sql``.

    The inverse of :func:`extract_ctas_inner_select`: after the optimization steps
    rewrite the inner SELECT, re-wrap it into the original ``CREATE TABLE ... AS``
    shell (preserving the target table, OR REPLACE, column list, properties).

    Returns ``None`` (caller falls back to "no recomposed rewrite") when: the
    original is not a parseable CTAS; the original inner is itself a CREATE
    (nested CTAS); ``new_inner_sql`` does not parse, is not a query body, or is
    itself a CREATE; or any exception. Pure: sqlglot only, no cluster. Never raises.
    """
    if not original_ctas_sql or not new_inner_sql:
        return None
    try:
        import sqlglot
        import sqlglot.expressions as exp
    except ImportError:
        return None

    try:
        create_tree = sqlglot.parse_one(original_ctas_sql, read="trino")
    except Exception:
        return None
    if not isinstance(create_tree, exp.Create):
        return None
    # Reject nested CTAS on the original side.
    if isinstance(create_tree.expression, exp.Create):
        return None

    try:
        new_inner_tree = sqlglot.parse_one(new_inner_sql, read="trino")
    except Exception:
        return None
    if new_inner_tree is None or isinstance(new_inner_tree, exp.Create):
        return None
    if not isinstance(new_inner_tree, (exp.Select, exp.With, exp.Union, exp.Subquery)):
        return None

    try:
        create_tree.set("expression", new_inner_tree)
        rendered = create_tree.sql(dialect="trino")
    except Exception:
        return None
    return rendered.strip() or None


def query_output_columns(sql: Optional[str]) -> Optional[tuple]:
    """Return the output column-name tuple of a query, or ``None`` if indeterminable.

    Used to gate fragment rewrites: an optimization step must NOT change the set of
    output columns. Returns ``None`` when the projection cannot be determined
    statically (``SELECT *``, parse failure, no projecting SELECT) — callers treat
    ``None`` on either side as "cannot prove safe" and revert the rewrite.

    Pure: sqlglot only. Never raises.
    """
    if not sql:
        return None
    try:
        import sqlglot
        import sqlglot.expressions as exp
    except ImportError:
        return None
    try:
        tree = sqlglot.parse_one(sql, read="trino")
    except Exception:
        return None
    if tree is None:
        return None
    # Unwrap to the projecting SELECT (WITH/UNION expose it via `.this`).
    sel = tree
    if isinstance(sel, (exp.With, exp.Union, exp.Subquery)):
        sel = sel.this
    if not isinstance(sel, exp.Select):
        sel = sel.find(exp.Select) if sel is not None else None
    if not isinstance(sel, exp.Select):
        return None
    names = []
    for projection in sel.expressions:
        # Bare `*` or qualified `t.*` (a Column wrapping a Star) → indeterminable.
        if isinstance(projection, exp.Star) or projection.find(exp.Star) is not None:
            return None  # cannot prove the column set is preserved
        names.append(projection.alias_or_name)
    return tuple(names)


def queries_structurally_equivalent(sql1: Optional[str], sql2: Optional[str]) -> Optional[bool]:
    """Return True if two queries have identical row-level structural invariants.

    Compares WHERE predicates (commutative), JOIN graph (count + type + ON),
    GROUP BY (set), DISTINCT presence, HAVING, ORDER BY, LIMIT, and set-op type
    (UNION vs UNION ALL). CTE-bearing queries return None (indeterminable) because
    CTE body changes cannot be reliably compared at the outer-SELECT level.

    Pure sqlglot AST; never raises; never executes.

    Returns True (provably equivalent), False (provably different),
    or None (indeterminable — parse failure, CTE presence, or comparison uncertainty).
    """
    if not sql1 or not sql2:
        return None
    try:
        import sqlglot
        import sqlglot.expressions as exp
    except ImportError:
        return None

    try:
        t1 = sqlglot.parse_one(sql1, read="trino")
        t2 = sqlglot.parse_one(sql2, read="trino")
        if t1 is None or t2 is None:
            return None

        # CTE-bearing queries: body changes are invisible at outer-SELECT level → indeterminable.
        if isinstance(t1, exp.With) or isinstance(t2, exp.With):
            return None
        if (hasattr(t1, "find") and t1.find(exp.With)) or (hasattr(t2, "find") and t2.find(exp.With)):
            return None

        def _unwrap_select(tree):
            sel = tree
            if isinstance(sel, exp.Subquery):
                sel = sel.this
            if isinstance(sel, exp.Union):
                return sel
            if not isinstance(sel, exp.Select):
                sel = sel.find(exp.Select) if sel is not None else None
            return sel

        s1 = _unwrap_select(t1)
        s2 = _unwrap_select(t2)
        if s1 is None or s2 is None:
            return None

        if isinstance(s1, exp.Union) or isinstance(s2, exp.Union):
            if type(s1) != type(s2):
                return False
            return s1.sql(dialect="trino").strip() == s2.sql(dialect="trino").strip()

        def _predicate_set(node):
            if node is None:
                return frozenset()
            pred = node.this if hasattr(node, "this") else node
            if pred is None:
                return frozenset()
            parts = []
            if isinstance(pred, exp.And):
                stack = [pred]
                while stack:
                    cur = stack.pop()
                    if isinstance(cur, exp.And):
                        stack.append(cur.left)
                        stack.append(cur.right)
                    else:
                        parts.append(cur.sql(dialect="trino").strip().lower())
            else:
                parts.append(pred.sql(dialect="trino").strip().lower())
            return frozenset(parts)

        w1 = _predicate_set(s1.args.get("where"))
        w2 = _predicate_set(s2.args.get("where"))
        if w1 != w2:
            return False

        j1 = s1.args.get("joins") or []
        j2 = s2.args.get("joins") or []
        if len(j1) != len(j2):
            return False
        for ja, jb in zip(j1, j2):
            if (ja.args.get("kind"), ja.args.get("side")) != (jb.args.get("kind"), jb.args.get("side")):
                return False
            on_a = ja.args.get("on")
            on_b = jb.args.get("on")
            if _predicate_set(on_a) != _predicate_set(on_b):
                return False
            tbl_a = ja.this.sql(dialect="trino").strip().lower() if ja.this else ""
            tbl_b = jb.this.sql(dialect="trino").strip().lower() if jb.this else ""
            if tbl_a != tbl_b:
                return False

        def _expr_set(node):
            if node is None:
                return frozenset()
            exprs = node.expressions if hasattr(node, "expressions") else []
            return frozenset(e.sql(dialect="trino").strip().lower() for e in exprs)

        if _expr_set(s1.args.get("group")) != _expr_set(s2.args.get("group")):
            return False

        d1 = s1.args.get("distinct") is not None
        d2 = s2.args.get("distinct") is not None
        if d1 != d2:
            return False

        h1 = _predicate_set(s1.args.get("having"))
        h2 = _predicate_set(s2.args.get("having"))
        if h1 != h2:
            return False

        if _expr_set(s1.args.get("order")) != _expr_set(s2.args.get("order")):
            return False

        lim1 = s1.args.get("limit")
        lim2 = s2.args.get("limit")
        lim1_sql = lim1.sql(dialect="trino").strip() if lim1 else None
        lim2_sql = lim2.sql(dialect="trino").strip() if lim2 else None
        if lim1_sql != lim2_sql:
            return False

        return True
    except Exception:
        return None
