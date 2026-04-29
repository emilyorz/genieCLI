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


def plan_cost(
    sql: str,
    explain_runner,
) -> tuple[Optional[int], Optional[int], Optional[object]]:
    """Return (rows_est, bytes_est, raw_plan_json) from EXPLAIN (FORMAT JSON).

    Args:
        sql: SQL to plan-cost (caller is responsible for read-only check).
        explain_runner: callable `(sql) -> str | None` that returns raw EXPLAIN
            JSON text. If it returns falsy or raises, result is all-None.

    The tuple members are independent — bytes_est can be None while rows_est is set.
    raw_plan_json is the parsed plan (dict or list) for downstream callers that
    want to walk it for structural signatures (T3); it is None when the raw
    response isn't valid JSON.
    """
    if explain_runner is None:
        return None, None, None
    try:
        raw = explain_runner(sql)
    except Exception:
        return None, None, None
    if not raw:
        return None, None, None

    rows, bytes_ = estimate_from_explain(raw)

    try:
        plan_json = json.loads(raw) if isinstance(raw, str) else raw
    except (json.JSONDecodeError, TypeError):
        plan_json = None
    if not isinstance(plan_json, (dict, list)):
        plan_json = None

    return rows, bytes_, plan_json


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


DEFAULT_LONG_QUERY_THRESHOLD_S = 60
DEFAULT_MAX_FALLBACKS = 3
PER_CANDIDATE_TIMEOUT_FACTOR = 1.2


class LongQueryAbort(RuntimeError):
    """Raised by the iteration loop when the upfront cost gate rejects a run.

    Carries the user-facing message so the outer entry-point can surface it
    without re-deriving the prediction math.
    """

    def __init__(self, message: str, baseline_s: float, predicted_total_s: float):
        super().__init__(message)
        self.baseline_s = baseline_s
        self.predicted_total_s = predicted_total_s


class NoDataDetected(RuntimeError):
    """Raised when baseline returns 0 rows or hits TABLE_NOT_FOUND.

    Short-circuits the iteration loop so the entry-point can write the
    static-analysis no-data report instead. Carries the result dict from
    `_run_no_data_path` so the caller doesn't re-derive it.
    """

    def __init__(self, reason: str, result: dict):
        super().__init__(f"no-data dispatch: {reason}")
        self.reason = reason
        self.result = result


@dataclass
class LongQueryGateResult:
    """Verdict from the upfront cost gate.

    - `ok=True`: caller may proceed with iteration.
    - `ok=False`: caller should abort and surface `message` to the user.
    """
    ok: bool
    message: str = ""
    baseline_s: float = 0.0
    predicted_total_s: float = 0.0


def check_long_query_gate(
    baseline_wall_ms: float,
    max_iterations: int,
    *,
    long_query_opt_in: bool,
    threshold_s: int = DEFAULT_LONG_QUERY_THRESHOLD_S,
    max_fallbacks: int = DEFAULT_MAX_FALLBACKS,
) -> LongQueryGateResult:
    """Decide whether a run should be aborted because the baseline is too slow.

    Worst-case wall-time model matches v28 PLAN:
        predicted_total = baseline + (iter × 1.2 × baseline) + baseline + (fallbacks × baseline)
    i.e. one baseline sample + iter candidate measurements capped at 1.2× baseline
    + one final L3 verify + up to `max_fallbacks` L3 fallbacks.
    """
    baseline_s = baseline_wall_ms / 1000.0
    predicted_total_s = baseline_s * (
        1  # baseline
        + max_iterations * PER_CANDIDATE_TIMEOUT_FACTOR
        + 1  # final L3 verify on winner
        + max_fallbacks  # K-retry L3 fallbacks worst-case
    )

    if baseline_s <= threshold_s:
        return LongQueryGateResult(ok=True, baseline_s=baseline_s,
                                   predicted_total_s=predicted_total_s)

    if long_query_opt_in:
        return LongQueryGateResult(ok=True, baseline_s=baseline_s,
                                   predicted_total_s=predicted_total_s)

    message = (
        f"baseline wall-time {baseline_s:.1f}s exceeds --long-query-threshold "
        f"{threshold_s}s; predicted worst-case total "
        f"{predicted_total_s / 60:.1f} min "
        f"(iter={max_iterations}, fallbacks={max_fallbacks}). "
        f"Pass --long-query to proceed anyway."
    )
    return LongQueryGateResult(
        ok=False, message=message,
        baseline_s=baseline_s, predicted_total_s=predicted_total_s,
    )


def make_query_max_run_time_sql(baseline_wall_ms: float) -> str:
    """Build a `SET SESSION query_max_run_time = '<N>ms'` statement.

    N = ceil(1.2 × baseline_wall_ms), clamped to at least 1000ms so that tiny
    baselines don't produce absurdly short timeouts during smoke runs.
    """
    import math
    n_ms = max(1000, int(math.ceil(baseline_wall_ms * PER_CANDIDATE_TIMEOUT_FACTOR)))
    return f"SET SESSION query_max_run_time = '{n_ms}ms'"


def apply_safe_limit(sql: str, limit: int) -> str:
    """Wrap SQL in SELECT * FROM (<orig>) LIMIT N. Caller's responsibility."""
    if limit <= 0:
        return sql
    stripped = sql.strip().rstrip(";").strip()
    return f"SELECT * FROM ({stripped}) AS _safe_wrapped LIMIT {limit}"


# ── No-data detection (v28 T9) ────────────────────────────────────────────────

# Substrings the Trino client surfaces when a referenced table/schema/catalog
# doesn't exist. Matched case-insensitively against str(exception).
_TABLE_NOT_FOUND_MARKERS = (
    "TABLE_NOT_FOUND",
    "SCHEMA_NOT_FOUND",
    "CATALOG_NOT_FOUND",
    "does not exist",
    "Table.*not found",
    "Schema.*does not exist",
)


def detect_no_data_reason(
    *,
    baseline_row_count: Optional[int] = None,
    baseline_exc: Optional[BaseException] = None,
) -> Optional[str]:
    """Classify why a baseline run has no usable data.

    Returns one of:
        - "table_not_found": exception text matches a known not-found marker
        - "empty_result": baseline succeeded but row count == 0
        - None: there is data, no fallback needed

    The caller decides what to do — typically: switch from the iteration loop
    to a single-call static-analysis report.
    """
    if baseline_exc is not None:
        msg = str(baseline_exc)
        upper = msg.upper()
        for marker in _TABLE_NOT_FOUND_MARKERS:
            if marker.upper() in upper:
                return "table_not_found"
            if re.search(marker, msg, re.IGNORECASE):
                return "table_not_found"
        # Any other exception → not a no-data case (let caller surface error)
        return None
    if baseline_row_count is not None and baseline_row_count == 0:
        return "empty_result"
    return None
