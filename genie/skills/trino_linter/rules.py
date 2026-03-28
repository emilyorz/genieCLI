"""Trino SQL lint rules — 11 checks covering Oracle residuals and Trino anti-patterns."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


@dataclass
class Finding:
    severity: str   # "high" | "medium" | "low"
    line: int
    rule: str
    message: str
    suggestion: str


# ── helpers ────────────────────────────────────────────────────────────────────

def _line_of(sql: str, pattern: str, flags: int = re.IGNORECASE) -> int:
    m = re.search(pattern, sql, flags)
    return sql[: m.start()].count("\n") + 1 if m else 1


def _all_lines_of(sql: str, pattern: str, flags: int = re.IGNORECASE) -> list[int]:
    return [sql[: m.start()].count("\n") + 1 for m in re.finditer(pattern, sql, flags)]


def _strip_comments_and_strings(sql: str) -> str:
    """Replace comment/string content with spaces, preserving newlines and character positions."""
    chars = list(sql)
    i = 0
    n = len(sql)
    while i < n:
        if sql[i:i+2] == '--':
            # Line comment: replace up to (but not including) newline
            while i < n and sql[i] != '\n':
                chars[i] = ' '
                i += 1
        elif sql[i:i+2] == '/*':
            # Block comment: replace until */
            chars[i] = ' '
            chars[i + 1] = ' '
            i += 2
            while i < n:
                if sql[i:i+2] == '*/':
                    chars[i] = ' '
                    chars[i + 1] = ' '
                    i += 2
                    break
                if sql[i] != '\n':
                    chars[i] = ' '
                i += 1
        elif sql[i] == "'":
            # String literal: replace content (keep newlines)
            chars[i] = ' '
            i += 1
            while i < n:
                if sql[i] == "'":
                    chars[i] = ' '
                    i += 1
                    if i < n and sql[i] == "'":
                        # Escaped quote ''
                        chars[i] = ' '
                        i += 1
                        continue
                    break
                if sql[i] != '\n':
                    chars[i] = ' '
                i += 1
        else:
            i += 1
    return ''.join(chars)


# ── Oracle residuals (rules 1-5) ───────────────────────────────────────────────

def check_nvl(sql: str, statements: list) -> list[Finding]:
    """oracle-residual-nvl: NVL() → COALESCE()"""
    stripped = _strip_comments_and_strings(sql)
    return [
        Finding(
            severity="high",
            line=line,
            rule="oracle-residual-nvl",
            message="NVL() is an Oracle function not supported in Trino",
            suggestion="Replace NVL(a, b) with COALESCE(a, b)",
        )
        for line in _all_lines_of(stripped, r"\bNVL\s*\(")
    ]


def check_decode(sql: str, statements: list) -> list[Finding]:
    """oracle-residual-decode: DECODE() → CASE WHEN"""
    stripped = _strip_comments_and_strings(sql)
    return [
        Finding(
            severity="high",
            line=line,
            rule="oracle-residual-decode",
            message="DECODE() is an Oracle function not supported in Trino",
            suggestion="Replace DECODE(expr, s1, r1, ..., def) with CASE WHEN expr=s1 THEN r1 ... ELSE def END",
        )
        for line in _all_lines_of(stripped, r"\bDECODE\s*\(")
    ]


def check_plus_join(sql: str, statements: list) -> list[Finding]:
    """oracle-residual-plus-join: (+) outer join syntax"""
    stripped = _strip_comments_and_strings(sql)
    return [
        Finding(
            severity="high",
            line=line,
            rule="oracle-residual-plus-join",
            message="Oracle outer join syntax (+) is not supported in Trino",
            suggestion="Rewrite as ANSI LEFT JOIN or RIGHT JOIN",
        )
        for line in _all_lines_of(stripped, r"\(\s*\+\s*\)")
    ]


def check_rownum(sql: str, statements: list) -> list[Finding]:
    """oracle-residual-rownum: ROWNUM → LIMIT / ROW_NUMBER()"""
    stripped = _strip_comments_and_strings(sql)
    return [
        Finding(
            severity="high",
            line=line,
            rule="oracle-residual-rownum",
            message="ROWNUM is an Oracle pseudo-column not supported in Trino",
            suggestion="Replace with ROW_NUMBER() OVER (...) in a subquery, or use FETCH FIRST n ROWS ONLY",
        )
        for line in _all_lines_of(stripped, r"\bROWNUM\b")
    ]


def check_sysdate(sql: str, statements: list) -> list[Finding]:
    """oracle-residual-sysdate: SYSDATE → CURRENT_TIMESTAMP"""
    stripped = _strip_comments_and_strings(sql)
    return [
        Finding(
            severity="high",
            line=line,
            rule="oracle-residual-sysdate",
            message="SYSDATE is an Oracle keyword not supported in Trino",
            suggestion="Replace SYSDATE with CURRENT_TIMESTAMP or NOW()",
        )
        for line in _all_lines_of(stripped, r"\bSYSDATE\b")
    ]


# ── Trino anti-patterns (rules 6-11) ──────────────────────────────────────────

def check_select_star(sql: str, statements: list) -> list[Finding]:
    """select-star: SELECT * but not COUNT(*)"""
    try:
        import sqlglot.expressions as exp
    except ImportError:
        return []

    findings: list[Finding] = []
    search_pos = 0  # advance per statement to get correct line numbers for each

    for stmt in statements:
        if stmt is None:
            continue

        has_non_count_star = False
        for star in stmt.find_all(exp.Star):
            # Walk up to check if this star is inside a Count function
            in_count = False
            parent = star.parent
            while parent is not None:
                if isinstance(parent, exp.Count):
                    in_count = True
                    break
                if isinstance(parent, (exp.Select, exp.Subquery)):
                    break
                parent = parent.parent
            if not in_count:
                has_non_count_star = True
                break

        if not has_non_count_star:
            continue

        # Find line number: first * from search_pos not immediately after COUNT(
        line = 1
        for m in re.finditer(r"\*", sql[search_pos:]):
            pos = m.start() + search_pos
            before = sql[max(0, pos - 50): pos]
            if re.search(r"\bCOUNT\s*\(\s*$", before, re.IGNORECASE):
                continue
            line = sql[:pos].count("\n") + 1
            search_pos = pos + 1
            break

        findings.append(Finding(
            severity="medium",
            line=line,
            rule="select-star",
            message="SELECT * may retrieve unnecessary columns and hinder query optimization",
            suggestion="Specify only the columns you need instead of SELECT *",
        ))

    return findings


def check_implicit_cross_join(sql: str, statements: list) -> list[Finding]:
    """implicit-cross-join: FROM a, b without explicit JOIN keyword"""
    findings: list[Finding] = []
    # Pattern: FROM followed by comma-separated table refs (with optional alias)
    pattern = r"\bFROM\b\s+[\w.\"]+(?:\s+(?:AS\s+)?\w+)?\s*,\s*[\w.\"]+"
    seen_lines: set[int] = set()
    for m in re.finditer(pattern, sql, re.IGNORECASE):
        line = sql[: m.start()].count("\n") + 1
        if line in seen_lines:
            continue
        seen_lines.add(line)
        findings.append(Finding(
            severity="medium",
            line=line,
            rule="implicit-cross-join",
            message="Implicit cross join — comma-separated tables without explicit JOIN keyword",
            suggestion="Use explicit JOIN ... ON ... syntax to make join intent clear and prevent accidental cartesian products",
        ))
    return findings


def check_leading_wildcard_like(sql: str, statements: list) -> list[Finding]:
    """leading-wildcard-like: LIKE '%xxx' prevents index usage"""
    findings: list[Finding] = []
    for m in re.finditer(r"""\bLIKE\s+(['\"])([%_][^'\"]*)\1""", sql, re.IGNORECASE):
        line = sql[: m.start()].count("\n") + 1
        findings.append(Finding(
            severity="low",
            line=line,
            rule="leading-wildcard-like",
            message=f"Leading wildcard LIKE '{m.group(2)}' forces a full scan and cannot use indexes",
            suggestion="Avoid leading wildcards when possible; consider full-text search or reverse the string",
        ))
    return findings


def check_count_distinct(sql: str, statements: list) -> list[Finding]:
    """count-distinct-high-risk: COUNT(DISTINCT ...) on large cardinality"""
    return [
        Finding(
            severity="medium",
            line=line,
            rule="count-distinct-high-risk",
            message="COUNT(DISTINCT ...) can be expensive on high-cardinality columns at scale",
            suggestion="Consider APPROX_DISTINCT() for large datasets where an approximate count is acceptable",
        )
        for line in _all_lines_of(sql, r"\bCOUNT\s*\(\s*DISTINCT\b")
    ]


def check_correlated_subquery(sql: str, statements: list) -> list[Finding]:
    """correlated-subquery: subquery in WHERE that references outer-scope columns."""
    try:
        import sqlglot
        import sqlglot.errors as sge
        import sqlglot.expressions as exp
    except ImportError:
        return []

    stmts = statements if statements else sqlglot.parse(
        sql, read="trino", error_level=sge.ErrorLevel.IGNORE
    )

    findings: list[Finding] = []
    seen_lines: set[int] = set()

    for stmt in stmts:
        if stmt is None or not isinstance(stmt, exp.Select):
            continue

        # Collect outer table names and aliases
        outer_tables: set[str] = set()
        from_ = stmt.args.get("from_")
        if from_:
            tbl = from_.this
            if isinstance(tbl, exp.Table):
                if tbl.name:
                    outer_tables.add(tbl.name.lower())
                if tbl.alias:
                    outer_tables.add(tbl.alias.lower())
        for join in stmt.args.get("joins") or []:
            tbl = join.this
            if isinstance(tbl, exp.Table):
                if tbl.name:
                    outer_tables.add(tbl.name.lower())
                if tbl.alias:
                    outer_tables.add(tbl.alias.lower())

        where = stmt.args.get("where")
        if where is None or not outer_tables:
            continue

        for subquery in where.find_all(exp.Subquery):
            is_correlated = any(
                bool(col.table) and col.table.lower() in outer_tables
                for col in subquery.find_all(exp.Column)
            )
            if not is_correlated:
                continue

            line = _line_of(sql, r"\bWHERE\b")
            if line in seen_lines:
                continue
            seen_lines.add(line)
            findings.append(Finding(
                severity="medium",
                line=line,
                rule="correlated-subquery",
                message="Subquery in WHERE clause — may be a correlated subquery causing repeated execution per outer row",
                suggestion="Rewrite as a JOIN or move to a CTE to avoid per-row re-execution",
            ))

    return findings


def check_missing_partition_filter(sql: str, statements: list) -> list[Finding]:
    """missing-partition-filter: top-level SELECT on a table with no WHERE clause."""
    try:
        import sqlglot.expressions as exp
    except ImportError:
        return []

    findings: list[Finding] = []
    seen_tables: set[str] = set()

    for stmt in statements:
        if stmt is None:
            continue
        # Only inspect the top-level SELECT — do not recurse into subqueries/CTEs
        if isinstance(stmt, exp.Select):
            top_select = stmt
        elif hasattr(stmt, "this") and isinstance(stmt.this, exp.Select):
            top_select = stmt.this
        else:
            continue

        if top_select.args.get("where") is not None:
            continue
        from_ = top_select.args.get("from_")
        if from_ is None:
            continue
        table = from_.this
        if not isinstance(table, exp.Table):
            continue
        table_name = table.name or ""
        if not table_name or table_name in seen_tables:
            continue
        seen_tables.add(table_name)

        line = _line_of(sql, r"\bFROM\b\s+" + re.escape(table_name), re.IGNORECASE)
        findings.append(Finding(
            severity="low",
            line=line,
            rule="missing-partition-filter",
            message=f"Query on '{table_name}' has no WHERE clause — may cause a full table scan",
            suggestion="Add a partition filter (e.g., a date partition column) in the WHERE clause",
        ))

    return findings


# ── rule registry ──────────────────────────────────────────────────────────────

ALL_RULES = [
    check_nvl,
    check_decode,
    check_plus_join,
    check_rownum,
    check_sysdate,
    check_select_star,
    check_implicit_cross_join,
    check_leading_wildcard_like,
    check_count_distinct,
    check_correlated_subquery,
    check_missing_partition_filter,
]
