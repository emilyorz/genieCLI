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


# Default-deny sentinel: every key in this set is explicitly compared by the oracle.
# Any key present in the parsed Select but NOT in this set triggers None (indeterminate).
# This makes "unmodeled construct → revert" true by construction, not by trusting the
# enumeration above is complete.
# Keys: from_, expressions, where, joins, group, distinct, having, order, limit, offset, windows.
# Deliberately excluded: qualify (Trino parse raises → already None), sample (folded into from_).
_MODELLED_KEYS: frozenset = frozenset({
    "from_", "expressions", "where", "joins", "group",
    "distinct", "having", "order", "limit", "offset", "windows",
})


def queries_structurally_equivalent(sql1: Optional[str], sql2: Optional[str]) -> Optional[bool]:
    """Return True if two queries have identical row-level structural invariants.

    Compares: FROM source (Fix 3), projections (Fix 7), WHERE predicates (commutative),
    JOIN graph — count + type + ON predicate + USING columns (Fix 2, Fix 5),
    GROUP BY (set), DISTINCT presence, HAVING, named WINDOW definitions (Fix 8),
    ORDER BY (sequence-sensitive when LIMIT present — Fix 6), LIMIT, OFFSET (Fix 4),
    and set-op type — UNION / INTERSECT / EXCEPT (Fix 1).

    CTE-bearing queries return None (indeterminable) because CTE body changes
    cannot be reliably compared at the outer-SELECT level.

    Default-deny sentinel: any key on either parsed Select not in _MODELLED_KEYS
    that is non-falsy → return None (indeterminate, gate reverts).

    Pure sqlglot AST; never raises; never executes.

    Returns True (provably equivalent), False (provably different),
    or None (indeterminable — parse failure, CTE, unmodeled construct, or comparison
    uncertainty).
    """
    if not sql1 or not sql2:
        return None
    try:
        import sqlglot
        import sqlglot.expressions as exp
    except ImportError:
        return None

    # Resolve _SET_OP_TYPES now that exp is imported.
    _set_op_types = (exp.Union, exp.Intersect, exp.Except)

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

        # Fix 1 (CLASS E): handle Union, Intersect, Except uniformly.
        def _unwrap_select(tree):
            sel = tree
            if isinstance(sel, exp.Subquery):
                sel = sel.this
            # Return set-op nodes intact; do NOT call .find(exp.Select) on them
            # because that would silently discard the right branch and the op type.
            if isinstance(sel, _set_op_types):
                return sel
            if not isinstance(sel, exp.Select):
                sel = sel.find(exp.Select) if sel is not None else None
            return sel

        s1 = _unwrap_select(t1)
        s2 = _unwrap_select(t2)
        if s1 is None or s2 is None:
            return None

        # Fix 1 (CLASS E): set-op block covers Union, Intersect, Except.
        # Full-text compare after type check — captures right-branch body differences.
        if isinstance(s1, _set_op_types) or isinstance(s2, _set_op_types):
            if type(s1) != type(s2):
                return False
            return s1.sql(dialect="trino").strip() == s2.sql(dialect="trino").strip()

        # Fix 2 (CLASS G): unwrap .this ONLY for Where/Having nodes (which wrap their
        # predicate under .this). For bare predicate nodes (e.g. EQ, Or, And passed
        # directly as a JOIN ON value), use the node whole — otherwise .this returns
        # only the left child, silently dropping OR/XOR right branches.
        def _predicate_set(node):
            if node is None:
                return frozenset()
            pred = node.this if isinstance(node, (exp.Where, exp.Having)) else node
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
                        # per-item .sql() exception falls through to outer except -> None
                        parts.append(cur.sql(dialect="trino").strip().lower())
            else:
                parts.append(pred.sql(dialect="trino").strip().lower())
            return frozenset(parts)

        # Fix 3 (CLASS A): compare full FROM source (table, schema/catalog qual, TABLESAMPLE,
        # UNNEST, VALUES, derived subquery body) via canonical From.sql() rendering.
        from1 = s1.args.get("from_")
        from2 = s2.args.get("from_")
        from1_sql = from1.sql(dialect="trino").strip().lower() if from1 else ""
        from2_sql = from2.sql(dialect="trino").strip().lower() if from2 else ""
        if from1_sql != from2_sql:
            return False

        # Fix 7 (CLASS C): compare projection expressions positionally.
        # Ordering is a fix-independent early-exit guard; placement here (after FROM,
        # before JOIN/WHERE) is not semantically load-bearing — each comparison is an
        # independent early-exit.
        expr1 = s1.args.get("expressions") or []
        expr2 = s2.args.get("expressions") or []
        if len(expr1) != len(expr2):
            return False
        for e1, e2 in zip(expr1, expr2):
            # per-item .sql() exception falls through to outer except -> None
            if e1.sql(dialect="trino").strip().lower() != e2.sql(dialect="trino").strip().lower():
                return False

        # WHERE predicate comparison (existing; _predicate_set now sound per Fix 2).
        w1 = _predicate_set(s1.args.get("where"))
        w2 = _predicate_set(s2.args.get("where"))
        if w1 != w2:
            return False

        # JOIN graph: count, kind/side, ON predicate (Fix 2), table, USING columns (Fix 5).
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
            # Fix 5 (CLASS F): compare USING column set per join.
            # ON joins have empty using on both sides (no-op here).
            using_a = frozenset(
                col.sql(dialect="trino").strip().lower()
                for col in (ja.args.get("using") or [])
            )
            using_b = frozenset(
                col.sql(dialect="trino").strip().lower()
                for col in (jb.args.get("using") or [])
            )
            if using_a != using_b:
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

        # Fix 8 (CLASS D): compare named WINDOW definitions as a frozenset (unordered;
        # windows are referenced by name so definition order does not matter).
        win1 = s1.args.get("windows") or []
        win2 = s2.args.get("windows") or []
        if len(win1) != len(win2):
            return False
        win1_set = frozenset(w.sql(dialect="trino").strip().lower() for w in win1)
        win2_set = frozenset(w.sql(dialect="trino").strip().lower() for w in win2)
        if win1_set != win2_set:
            return False

        # Fix 6 (CLASS H): ORDER BY comparison.
        # When LIMIT is present on either side, key sequence matters (top-N result differs).
        # Without LIMIT, ORDER BY resequencing is presentation-only (bag semantics) → frozenset.
        # Note: R-ORDERBY-LIMIT-ADD/REMOVE (same ORDER BY, different LIMIT) are caught by the
        # LIMIT string compare below; has_limit causes list compare which is sound (equal lists).
        ord1 = s1.args.get("order")
        ord2 = s2.args.get("order")
        ord_list1 = [
            e.sql(dialect="trino").strip().lower()
            for e in (ord1.expressions if ord1 else [])
        ]
        ord_list2 = [
            e.sql(dialect="trino").strip().lower()
            for e in (ord2.expressions if ord2 else [])
        ]
        has_limit = (s1.args.get("limit") is not None) or (s2.args.get("limit") is not None)
        if has_limit:
            if ord_list1 != ord_list2:   # sequence-sensitive when LIMIT present
                return False
        else:
            if frozenset(ord_list1) != frozenset(ord_list2):   # presentation-only without LIMIT
                return False

        lim1 = s1.args.get("limit")
        lim2 = s2.args.get("limit")
        lim1_sql = lim1.sql(dialect="trino").strip() if lim1 else None
        lim2_sql = lim2.sql(dialect="trino").strip() if lim2 else None
        if lim1_sql != lim2_sql:
            return False

        # Fix 4 (CLASS B): compare OFFSET.
        off1 = s1.args.get("offset")
        off2 = s2.args.get("offset")
        off1_sql = off1.sql(dialect="trino").strip() if off1 else ""
        off2_sql = off2.sql(dialect="trino").strip() if off2 else ""
        if off1_sql != off2_sql:
            return False

        # Default-deny sentinel (Fix 9 / Sentinel):
        # Any key present on either parsed Select that is NOT in _MODELLED_KEYS and has a
        # non-falsy value → indeterminate (return None, gate reverts).
        # This makes the oracle fail-closed for any construct not explicitly modeled above,
        # including future sqlglot additions (e.g. qualify if ever parsed under Trino,
        # prewhere, pivots, laterals, etc.).
        _all_keys = set(s1.args) | set(s2.args)
        for _key in _all_keys - _MODELLED_KEYS:
            if bool(s1.args.get(_key)) or bool(s2.args.get(_key)):
                return None

        return True
    except Exception:
        return None
