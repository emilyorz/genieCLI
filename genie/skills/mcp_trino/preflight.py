"""Pre-flight safety checks for /trino-research.

Before any SQL is executed against the MCP server, these checks verify:
- The SQL is read-only (no DML/DDL)
- Estimated output size is within safety thresholds
- Returns a PreflightReport with actionable info for the caller.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Optional


READ_ONLY_KEYWORDS = {"SELECT", "WITH", "EXPLAIN", "SHOW", "DESCRIBE", "DESC"}
DML_DDL_BLOCKED = {
    "INSERT", "UPDATE", "DELETE", "MERGE",
    "CREATE", "DROP", "ALTER", "TRUNCATE", "RENAME",
    "GRANT", "REVOKE", "CALL", "COMMIT", "ROLLBACK",
}


# Budget caps (override via PreflightBudget)
DEFAULT_MAX_ROWS = 100_000
DEFAULT_MAX_BYTES = 100 * 1024 * 1024  # 100 MB
DEFAULT_MAX_CAPTURE_ROWS = 100_000


@dataclass
class PreflightBudget:
    max_estimated_rows: int = DEFAULT_MAX_ROWS
    max_estimated_bytes: int = DEFAULT_MAX_BYTES
    max_capture_rows: int = DEFAULT_MAX_CAPTURE_ROWS


@dataclass
class PreflightReport:
    ok: bool
    reason: str = ""
    estimated_rows: Optional[int] = None
    estimated_bytes: Optional[int] = None
    is_read_only: bool = True


def check_read_only(sql: str) -> tuple[bool, str]:
    """Verify the SQL is read-only. Returns (is_ok, reason)."""
    if not sql or not sql.strip():
        return False, "empty SQL"

    stripped = sql.strip()
    # Strip inline + block comments; keep first keyword detection simple.
    no_line_comments = re.sub(r"--[^\n]*", "", stripped)
    no_block_comments = re.sub(r"/\*.*?\*/", "", no_line_comments, flags=re.DOTALL)
    cleaned = no_block_comments.strip().upper()

    if not cleaned:
        return False, "SQL is only comments"

    # Check blocked keywords anywhere in statement (defensive)
    words = re.findall(r"\b([A-Z]+)\b", cleaned)
    for word in words:
        if word in DML_DDL_BLOCKED:
            return False, f"blocked keyword '{word}' — only read-only queries allowed"

    # Multi-statement (semicolons between statements) isn't allowed here either
    statements = [s for s in cleaned.split(";") if s.strip()]
    if len(statements) > 1:
        return False, f"multiple statements detected ({len(statements)}); submit a single query"

    first = statements[0].split()[0] if statements else ""
    if first not in READ_ONLY_KEYWORDS:
        return False, f"first keyword '{first}' is not a read-only statement"

    return True, "read-only OK"


def estimate_from_explain(explain_result: str) -> tuple[Optional[int], Optional[int]]:
    """Parse EXPLAIN (FORMAT JSON) output, return (est_rows, est_bytes).

    Trino's JSON EXPLAIN has a tree with `estimates` fields on each stage.
    We return the estimate of the root output stage (first one we find at top).
    Returns (None, None) if the format can't be parsed.
    """
    try:
        data = json.loads(explain_result) if isinstance(explain_result, str) else explain_result
    except (json.JSONDecodeError, TypeError):
        return None, None

    if not isinstance(data, dict):
        return None, None

    # Trino's JSON has "estimates" list at root or per plan node. Walk it.
    def first_estimate(node):
        if not isinstance(node, dict):
            return None
        est = node.get("estimates")
        if isinstance(est, list) and est:
            for e in est:
                if isinstance(e, dict) and (e.get("outputRowCount") or e.get("outputSizeInBytes")):
                    return e
        for child in node.get("children", []) or []:
            found = first_estimate(child)
            if found:
                return found
        return None

    est = first_estimate(data)
    if not est:
        return None, None
    rows = est.get("outputRowCount")
    bytes_ = est.get("outputSizeInBytes")
    try:
        rows = int(rows) if rows is not None else None
    except (TypeError, ValueError):
        rows = None
    try:
        bytes_ = int(bytes_) if bytes_ is not None else None
    except (TypeError, ValueError):
        bytes_ = None
    return rows, bytes_


def run_preflight(
    sql: str,
    explain_runner,
    budget: PreflightBudget | None = None,
) -> PreflightReport:
    """Run all pre-flight checks.

    Args:
        sql: the original SQL to evaluate.
        explain_runner: a callable `(sql: str) -> str | None`. If provided
            and not None, it runs EXPLAIN (FORMAT JSON) and returns the raw
            EXPLAIN output. Pass None to skip size estimation.
        budget: optional custom budget; defaults to module constants.
    """
    budget = budget or PreflightBudget()

    ok, reason = check_read_only(sql)
    if not ok:
        return PreflightReport(ok=False, reason=reason, is_read_only=False)

    est_rows: Optional[int] = None
    est_bytes: Optional[int] = None
    if explain_runner is not None:
        try:
            raw = explain_runner(sql)
            if raw:
                est_rows, est_bytes = estimate_from_explain(raw)
        except Exception:
            # EXPLAIN failed or unavailable — not a blocker, just proceed without estimate
            est_rows, est_bytes = None, None

    if est_rows is not None and est_rows > budget.max_estimated_rows:
        return PreflightReport(
            ok=False,
            reason=f"estimated output rows {est_rows:,} exceeds budget {budget.max_estimated_rows:,}. "
                   f"Add LIMIT, tighten filters, or use --safe-limit N.",
            estimated_rows=est_rows,
            estimated_bytes=est_bytes,
        )
    if est_bytes is not None and est_bytes > budget.max_estimated_bytes:
        return PreflightReport(
            ok=False,
            reason=f"estimated output size {est_bytes:,} bytes exceeds budget "
                   f"{budget.max_estimated_bytes:,}. Add LIMIT or tighten projection.",
            estimated_rows=est_rows,
            estimated_bytes=est_bytes,
        )

    return PreflightReport(
        ok=True,
        reason="preflight OK",
        estimated_rows=est_rows,
        estimated_bytes=est_bytes,
    )


def apply_safe_limit(sql: str, limit: int) -> str:
    """Wrap SQL in SELECT * FROM (<orig>) LIMIT N. Caller's responsibility."""
    if limit <= 0:
        return sql
    stripped = sql.strip().rstrip(";").strip()
    return f"SELECT * FROM ({stripped}) AS _safe_wrapped LIMIT {limit}"
