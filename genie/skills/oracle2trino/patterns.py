"""Shared Oracle construct catalog — used by both oracle2trino converter and trino_linter.

Each entry describes one Oracle SQL or PL/SQL construct that Trino does not support
or requires rewriting. The `pattern` field is a case-insensitive regex applied against
the SQL text. The `linter_rule` field links to the corresponding trino_linter rule ID
where one exists.
"""
from __future__ import annotations

# ── catalog ────────────────────────────────────────────────────────────────────
# Fields:
#   pattern       — regex used to detect the construct (re.IGNORECASE applied)
#   construct     — canonical name shown in ConversionResult.unsupported
#   severity      — "high" | "medium" | "low"
#   message       — one-line problem description
#   suggestion    — recommended fix
#   linter_rule   — matching trino_linter rule ID (str) or None
#   plsql         — True for PL/SQL-only constructs that always need manual rewriting

ORACLE_CONSTRUCTS: list[dict] = [
    # ── Oracle SQL residuals (also flagged by trino_linter) ────────────────────
    {
        "pattern": r"\bROWNUM\b",
        "construct": "ROWNUM",
        "severity": "high",
        "message": "ROWNUM is an Oracle pseudo-column not supported in Trino",
        "suggestion": "Replace with ROW_NUMBER() OVER (...) in a subquery, or use FETCH FIRST n ROWS ONLY",
        "linter_rule": "oracle-residual-rownum",
        "plsql": False,
    },
    {
        "pattern": r"\bNVL\s*\(",
        "construct": "NVL",
        "severity": "high",
        "message": "NVL() is an Oracle function not supported in Trino",
        "suggestion": "Replace NVL(a, b) with COALESCE(a, b)",
        "linter_rule": "oracle-residual-nvl",
        "plsql": False,
    },
    {
        "pattern": r"\bDECODE\s*\(",
        "construct": "DECODE",
        "severity": "high",
        "message": "DECODE() is an Oracle function not supported in Trino",
        "suggestion": "Replace DECODE(expr, s1, r1, ..., def) with CASE WHEN expr=s1 THEN r1 ... ELSE def END",
        "linter_rule": "oracle-residual-decode",
        "plsql": False,
    },
    {
        "pattern": r"\(\s*\+\s*\)",
        "construct": "(+)",
        "severity": "high",
        "message": "Oracle outer join syntax (+) is not supported in Trino",
        "suggestion": "Rewrite as ANSI LEFT JOIN or RIGHT JOIN",
        "linter_rule": "oracle-residual-plus-join",
        "plsql": False,
    },
    {
        "pattern": r"\bSYSDATE\b",
        "construct": "SYSDATE",
        "severity": "high",
        "message": "SYSDATE is an Oracle keyword not supported in Trino",
        "suggestion": "Replace SYSDATE with CURRENT_TIMESTAMP or NOW()",
        "linter_rule": "oracle-residual-sysdate",
        "plsql": False,
    },
    # ── Oracle SQL conversion gaps (no linter rule) ────────────────────────────
    {
        "pattern": r"\bLISTAGG\b",
        "construct": "LISTAGG",
        "severity": "medium",
        "message": "LISTAGG is not supported in Trino",
        "suggestion": "Rewrite as ARRAY_JOIN(ARRAY_AGG(...), ',')",
        "linter_rule": None,
        "plsql": False,
    },
    {
        "pattern": r"\bMONTHS_BETWEEN\b",
        "construct": "MONTHS_BETWEEN",
        "severity": "low",
        "message": "MONTHS_BETWEEN is an Oracle function not available in Trino",
        "suggestion": "Rewrite as DATE_DIFF('month', d2, d1)",
        "linter_rule": None,
        "plsql": False,
    },
    {
        "pattern": r"\bWM_CONCAT\b",
        "construct": "WM_CONCAT",
        "severity": "medium",
        "message": "WM_CONCAT is an undocumented Oracle function not available in Trino",
        "suggestion": "Rewrite as ARRAY_JOIN(ARRAY_AGG(...), ',')",
        "linter_rule": None,
        "plsql": False,
    },
    {
        "pattern": r"\bCONNECT\s+BY\b",
        "construct": "CONNECT BY",
        "severity": "medium",
        "message": "CONNECT BY hierarchical query may not be fully converted by sqlglot",
        "suggestion": "Rewrite as a WITH RECURSIVE CTE",
        "linter_rule": None,
        "plsql": False,
    },
    {
        "pattern": r"\bPIVOT\b",
        "construct": "PIVOT",
        "severity": "medium",
        "message": "PIVOT may be dropped or incorrectly converted",
        "suggestion": "Rewrite as CASE-WHEN aggregation",
        "linter_rule": None,
        "plsql": False,
    },
    {
        "pattern": r"\bUNPIVOT\b",
        "construct": "UNPIVOT",
        "severity": "medium",
        "message": "UNPIVOT is not supported in Trino",
        "suggestion": "Rewrite as CROSS JOIN UNNEST",
        "linter_rule": None,
        "plsql": False,
    },
    # ── PL/SQL constructs (always require manual rewriting) ────────────────────
    {
        "pattern": r"\bEXECUTE\s+IMMEDIATE\b",
        "construct": "EXECUTE IMMEDIATE",
        "severity": "high",
        "message": "EXECUTE IMMEDIATE dynamic SQL is not supported in Trino",
        "suggestion": "Move dynamic SQL generation to the Python orchestration layer",
        "linter_rule": None,
        "plsql": True,
    },
    {
        "pattern": r"(?:^|\s)BEGIN\b",
        "construct": "BEGIN",
        "severity": "high",
        "message": "PL/SQL BEGIN block is not supported in Trino",
        "suggestion": "Wrap the logic in Python and submit individual Trino queries",
        "linter_rule": None,
        "plsql": True,
    },
    {
        "pattern": r"(?:^|\s)DECLARE\b",
        "construct": "DECLARE",
        "severity": "high",
        "message": "PL/SQL DECLARE section is not supported in Trino",
        "suggestion": "Replace variable declarations with CTEs or Python variables",
        "linter_rule": None,
        "plsql": True,
    },
    {
        "pattern": r"\bCURSOR\b",
        "construct": "CURSOR",
        "severity": "high",
        "message": "PL/SQL CURSOR is not supported in Trino",
        "suggestion": "Rewrite as a Python loop fetching rows from multiple Trino queries",
        "linter_rule": None,
        "plsql": True,
    },
    {
        "pattern": r"(?:^|\s)LOOP\b",
        "construct": "LOOP",
        "severity": "high",
        "message": "PL/SQL LOOP is not supported in Trino",
        "suggestion": "Move loop logic to Python orchestration",
        "linter_rule": None,
        "plsql": True,
    },
    {
        "pattern": r"\bEXCEPTION\b",
        "construct": "EXCEPTION",
        "severity": "high",
        "message": "PL/SQL EXCEPTION block is not supported in Trino",
        "suggestion": "Move exception handling to Python try/except",
        "linter_rule": None,
        "plsql": True,
    },
    {
        "pattern": r"\bDBMS_\w+",
        "construct": "DBMS_*",
        "severity": "high",
        "message": "Oracle DBMS_* packages have no Trino equivalent",
        "suggestion": "Replace with Python stdlib or Trino-native functions as appropriate",
        "linter_rule": None,
        "plsql": True,
    },
    {
        "pattern": r"\bFOR\s+\w+\s+IN\b",
        "construct": "FOR...IN",
        "severity": "high",
        "message": "PL/SQL FOR loop is not supported in Trino",
        "suggestion": "Move loop logic to Python orchestration",
        "linter_rule": None,
        "plsql": True,
    },
    {
        "pattern": r"\bMERGE\s+INTO\b",
        "construct": "MERGE INTO",
        "severity": "medium",
        "message": "MERGE INTO support depends on the Trino connector",
        "suggestion": "Verify connector supports MERGE; Iceberg supports limited MERGE (Trino 420+), Hive does not",
        "linter_rule": None,
        "plsql": False,
    },
]


# ── helpers ────────────────────────────────────────────────────────────────────

def get_construct_meta(construct: str) -> dict | None:
    """Return catalog entry for a construct name, or None if not found."""
    return next((c for c in ORACLE_CONSTRUCTS if c["construct"] == construct), None)


def get_construct_pattern(construct: str) -> str | None:
    """Return the regex pattern for a construct, or None if not found."""
    meta = get_construct_meta(construct)
    return meta["pattern"] if meta else None


def compute_confidence(unsupported: list) -> float:
    """Calculate conversion confidence (1.0 → 0.0) from unsupported constructs.

    Each unsupported construct deducts:
      high   → -0.30
      medium → -0.15
      low    → -0.05
    """
    conf = 1.0
    for item in unsupported:
        sev = item.severity if hasattr(item, "severity") else item.get("severity", "")
        if sev == "high":
            conf -= 0.30
        elif sev == "medium":
            conf -= 0.15
        elif sev == "low":
            conf -= 0.05
    return max(0.0, conf)
