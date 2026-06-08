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
        if isinstance(projection, exp.Star):
            return None  # indeterminable — cannot prove the column set is preserved
        names.append(projection.alias_or_name)
    return tuple(names)
