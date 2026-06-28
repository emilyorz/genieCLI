"""Pre-flight safety checks for /trino-research.

Before any SQL is executed against the MCP server, these checks verify:
- The SQL is read-only (no DML/DDL)
- Estimated output size is within safety thresholds
- Returns a PreflightReport with actionable info for the caller.
"""
from __future__ import annotations

import enum
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


def _combine_cost(rows: Optional[int], bytes_: Optional[int]) -> Optional[int]:
    """Return a cost scalar usable for ranking EXPLAIN-based plan costs.

    Rules:
    - Both present: rows * bytes  (original intent, unchanged)
    - bytes only:   bytes         (use available dimension; no fake-zero from missing rows)
    - rows only:    rows          (use available dimension; no 1-byte sentinel distortion)
    - Neither:      None          (caller must guard this; do not rank against None)

    This replaces the collapsed ``(rows or 0) * (bytes or 1)`` expression, which
    incorrectly returns 0 for bytes-only partial EXPLAIN results (bytes-only
    candidate always sorts first, regardless of actual bytes value).
    """
    if rows is not None and bytes_ is not None:
        return rows * bytes_
    if bytes_ is not None:
        return bytes_
    if rows is not None:
        return rows
    return None


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
PER_CANDIDATE_TIMEOUT_FACTOR = 1.0
# Per-candidate kill-timeout headroom: a candidate is only killed when it runs
# substantially slower than baseline (2x), not at baseline speed. Kept separate
# from PER_CANDIDATE_TIMEOUT_FACTOR (which models the gate's worst-case total).
CANDIDATE_TIMEOUT_HEADROOM = 2.0


class CandidateTimeoutError(TimeoutError):
    """Raised when a candidate query exceeds the baseline wall-time limit."""

    def __init__(self, timeout_ms: float, label: str = "candidate") -> None:
        self.timeout_ms = timeout_ms
        self.label = label
        super().__init__(
            f"{label} exceeded baseline wall-time limit "
            f"({timeout_ms / 1000.0:.1f}s)"
        )


class LongQueryAbort(RuntimeError):
    """Raised by the iteration loop when the upfront cost gate rejects a run.

    Carries the user-facing message so the outer entry-point can surface it
    without re-deriving the prediction math.
    """

    def __init__(
        self,
        message: str,
        baseline_s: float,
        predicted_total_s: float,
        report_markdown: str | None = None,
    ):
        super().__init__(message)
        self.baseline_s = baseline_s
        self.predicted_total_s = predicted_total_s
        # When the gate trips we still emit a directed report instead of a bare
        # abort; the entry-point writes this to disk if present.
        self.report_markdown = report_markdown


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


class PreflightRoute(enum.Enum):
    """Routing decision returned by build_preflight_decision.

    Priority is enforced by the builder's if/elif chain (decision table §4),
    NOT by enum value order. One value per observable exit branch.
    """
    DIAGNOSE_ONLY    = "diagnose_only"     # --diagnose-only; no baseline ran
    NO_DATA          = "no_data"           # baseline 0 rows OR table/schema/catalog not found
    REAL_FAILURE     = "real_failure"      # baseline raised a non-no-data exception
    LONG_QUERY_ABORT = "long_query_abort"  # baseline ran; gate.ok == False
    PLAN_COST_LOOP   = "plan_cost_loop"    # gate passed; EXPLAIN yielded estimates
    STANDARD_LOOP    = "standard_loop"     # all above false; standard measure loop


@dataclass(frozen=True)
class PreflightDecision:
    """Immutable routing decision. Carries ONLY the evidence each adapter needs
    to build its own exit artifact. Builds no reports, calls no I/O, raises nothing.
    frozen=True: single-computation, no adapter mutation."""
    route: PreflightRoute
    no_data_reason: Optional[str] = None
    baseline_exc: Optional[BaseException] = None
    gate_result: Optional["LongQueryGateResult"] = None
    plan_cost_available: bool = False
    seen_no_estimates: bool = False


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
        predicted_total = baseline + (iter × baseline) + baseline + (fallbacks × baseline)
    i.e. one baseline sample + iter candidate measurements capped at baseline wall time
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
        f"Remove --no-long-query to proceed anyway."
    )
    return LongQueryGateResult(
        ok=False, message=message,
        baseline_s=baseline_s, predicted_total_s=predicted_total_s,
    )


# ---------------------------------------------------------------------------
# Shared plan-cost iteration core (S1 — v43)
# ---------------------------------------------------------------------------

class _SafeOutput:
    """Adapter-boundary output normalizer.

    Both adapters wrap their ``output`` (Optional on MCP, required on direct)
    before calling the core; the core body calls self.print/progress/error
    unconditionally without guarding for None.
    """

    def __init__(self, output):  # output may be None (MCP) or a real sink (direct)
        self._out = output

    def print(self, *a, **kw):
        if self._out:
            self._out.print(*a, **kw)

    def progress(self, *a, **kw):
        if self._out:
            self._out.progress(*a, **kw)

    def error(self, *a, **kw):
        if self._out:
            self._out.error(*a, **kw)


from typing import NamedTuple as _NamedTuple


class _PlanCostCoreResult(_NamedTuple):
    winner_sql: Optional[str]
    winner_measure: object              # raw measure result (MCP: MeasureResult; direct: dict)
    winner_ranked: Optional[dict]       # ranked candidate dict; adapter reads ["plan_cost"]
    history: list                       # 4-key per-entry: {iteration, status, candidate_sql, plan_cost}
    verify_log: list                    # per-attempt log entries
    candidates_evaluated: int
    fallbacks_used: int
    baseline_cost: Optional[float]
    surviving_better_was_empty: bool    # True => Case A (no verify_log in direct return)


def _plan_cost_loop_core(
    *,
    # LLM plumbing
    provider,
    model: str,
    reasoning: str,
    sys_prompt: str,                    # adapter builds full prompt (incl. directions_block); core opaque
    # SQL + budget
    original_sql: str,
    metric_key: str,
    max_iterations: int,
    max_fallbacks: int,
    # Scoring anchors (pass-through to core body; computed by adapter before call)
    baseline_cost: Optional[float],
    baseline_sig,
    baseline_plan,
    baseline_rows_est: Optional[int],
    baseline_bytes_est: Optional[int],
    # Injected callables
    explain_runner,                     # (sql) -> str | None
    measure_fn,                         # (sql, label) -> measure result; raises CandidateTimeoutError
    metric_fn,                          # (measured) -> float; MCP: lambda m: m.median_metric; direct: lambda m: m["median"]
    row_equiv_fn,                       # (measured,) -> (bool, str); 1-arg closure captures baseline rows
    static_report,
    output: "_SafeOutput",              # always a _SafeOutput; core calls methods unconditionally
    candidate_timeout_ms: Optional[float] = None,
    empty_message: Optional[str] = None,  # overrides core empty-branch progress line; None uses MCP default
) -> "_PlanCostCoreResult":
    """Shared iteration core for both MCP and direct plan-cost loops.

    Implements the full iteration phase (LLM + EXPLAIN) and verification phase
    (K-retry on row-equivalence) identically for both call paths. Path-specific
    behaviour (baseline extraction, measure calls, output wrapping) is injected
    as callables by the adapter.

    Returns a _PlanCostCoreResult NamedTuple; the adapter reconstructs the
    final return dict from the fields (path-specific Case A/B/C logic lives
    in the adapter, not here).
    """
    # Lazy imports — all 8 required symbols; preserves import graph symmetry.
    from genie.core.provider import CompletionRequest
    from genie.session.manager import new_msg, new_session
    from genie.skills.trino_query.plan_signature import plan_signature, structural_equivalent
    from genie.skills.trino_query.research import _format_static_findings, _lint_sql
    from genie.core.sql_extraction import extract_sql_from_reply
    # plan_cost and _combine_cost are already module-level in this file; reference directly.

    session = new_session(sys_prompt)
    candidates: list = []
    history: list = []

    for iteration in range(1, max_iterations + 1):
        output.print("")
        output.progress(f"  ── Iteration {iteration}/{max_iterations} (plan-cost mode) ──")

        static_block = ""
        if iteration == 1 and static_report and static_report.findings:
            static_block = (
                "Static analysis findings (sqlglot AST rules — apply these in priority order):\n"
                f"{_format_static_findings(static_report)}\n\n"
            )

        # Lean history: keep system messages + last 4 non-system messages.
        sys_msgs = [m for m in session["history"] if m["role"] == "system"]
        non_sys = [m for m in session["history"] if m["role"] != "system"]
        session["history"] = sys_msgs + non_sys[-4:]

        context = (
            f"[Long-query plan-cost iteration {iteration}]\n"
            f"Baseline rows estimate: {baseline_rows_est}\n"
            f"Baseline bytes estimate: {baseline_bytes_est}\n"
            f"{static_block}"
            f"Current SQL:\n```sql\n{original_sql}\n```\n\n"
            f"Return the COMPLETE optimized SQL in a ```sql block. ONE change only. "
            f"Do NOT include a trailing semicolon."
        )
        session["history"].append(new_msg("user", context))

        output.progress("  AI thinking...")
        req = CompletionRequest(messages=session["history"], model=model, reasoning=reasoning)
        try:
            reply = provider.complete_text(req)
        except Exception as exc:
            output.error(f"  Model/provider failed — stopping iteration phase: {exc}")
            history.append({
                "iteration": iteration,
                "status": "model_failed",
                "candidate_sql": None,
                "plan_cost": None,
            })
            break
        if not reply:
            output.error("  Empty AI response — stopping iteration phase.")
            break
        session["history"].append(new_msg("assistant", reply))

        candidate_sql = extract_sql_from_reply(reply)
        if not candidate_sql:
            output.progress("  [SKIP] No SQL extracted")
            history.append({
                "iteration": iteration, "status": "no_sql",
                "candidate_sql": None, "plan_cost": None,
            })
            continue

        lint_ok, lint_msg = _lint_sql(candidate_sql)
        if not lint_ok:
            output.progress(f"  [SKIP] Lint failed: {lint_msg}")
            history.append({
                "iteration": iteration, "status": "lint_failed",
                "candidate_sql": candidate_sql, "plan_cost": None,
            })
            session["history"].append(new_msg("user", f"SQL failed lint: {lint_msg}. Try a different change."))
            continue

        try:
            cand_rows_est, cand_bytes_est, cand_plan = plan_cost(candidate_sql, explain_runner)
        except Exception as exc:
            output.progress(f"  [SKIP] EXPLAIN failed: {exc}")
            history.append({
                "iteration": iteration, "status": "explain_failed",
                "candidate_sql": candidate_sql, "plan_cost": None,
            })
            continue

        if cand_rows_est is None and cand_bytes_est is None:
            output.progress("  [SKIP] EXPLAIN returned no estimates")
            history.append({
                "iteration": iteration, "status": "explain_failed",
                "candidate_sql": candidate_sql, "plan_cost": None,
            })
            continue

        cand_cost = _combine_cost(cand_rows_est, cand_bytes_est)
        cand_sig = plan_signature(cand_plan) if cand_plan is not None else None

        # L1 structural guard
        if baseline_sig is not None and cand_sig is not None:
            if not structural_equivalent(baseline_plan, cand_plan):
                output.progress(
                    "  [REJECT] Structural divergence (L1) — candidate plan shape differs from baseline"
                )
                history.append({
                    "iteration": iteration, "status": "structural_reject",
                    "candidate_sql": candidate_sql, "plan_cost": cand_cost,
                })
                session["history"].append(new_msg(
                    "user",
                    "Candidate plan shape differs from baseline (L1 reject) — likely lost a column / "
                    "filter / aggregation. Try a different change that preserves the plan structure."
                ))
                continue

        # If baseline EXPLAIN yielded no estimates at all, plan-cost comparison is
        # impossible (would raise TypeError on None < int). Skip ranking and treat
        # every candidate as unranked — do not falsely promote them.
        if baseline_cost is None:
            output.progress("  [SKIP] Baseline has no plan-cost estimates; skipping cost comparison")
            history.append({
                "iteration": iteration, "status": "explain_failed",
                "candidate_sql": candidate_sql, "plan_cost": cand_cost,
            })
            continue

        verdict = "plan_cost_better" if cand_cost < baseline_cost else "plan_cost_worse"
        output.progress(
            f"  [{'+' if verdict == 'plan_cost_better' else '-'}] {verdict} "
            f"(cand_cost={cand_cost:.2e}, baseline_cost={baseline_cost:.2e})"
        )

        candidates.append({
            "iteration": iteration,
            "sql": candidate_sql,
            "plan_cost": cand_cost,
            "rows_est": cand_rows_est,
            "bytes_est": cand_bytes_est,
            "verdict": verdict,
        })
        history.append({
            "iteration": iteration, "status": verdict,
            "candidate_sql": candidate_sql, "plan_cost": cand_cost,
        })
        session["history"].append(new_msg(
            "user",
            f"Candidate accepted into ranking pool with plan cost {cand_cost:.2e} "
            f"(baseline {baseline_cost:.2e}). Suggest another rewrite for the next iteration."
        ))

    # ── Verification phase ──
    output.print("")
    output.progress(f"  [verify] {len(candidates)} candidate(s) survived L1; ranking by plan cost")

    # D10: surviving_better_was_empty is computed before the K-retry loop so the
    # core NEVER early-returns (adapter uses the flag for Case A detection).
    surviving_better = sorted(
        [c for c in candidates if baseline_cost is not None and c["plan_cost"] < baseline_cost],
        key=lambda c: c["plan_cost"],
    )
    surviving_better_was_empty = not surviving_better

    fallbacks_used = 0
    winner_sql: Optional[str] = None
    winner_measure = None
    winner_ranked: Optional[dict] = None
    verify_log: list = []

    for ranked in surviving_better:
        if fallbacks_used > max_fallbacks:
            output.progress(f"  [verify] Exhausted K={max_fallbacks} fallbacks")
            break
        output.progress(
            f"  [verify] Trying iter#{ranked['iteration']} "
            f"(plan_cost={ranked['plan_cost']:.2e})"
        )
        try:
            measured = measure_fn(ranked["sql"], f"verify iter {ranked['iteration']}")
        except CandidateTimeoutError as exc:
            output.progress(f"  [verify] timeout_worse: {exc}")
            verify_log.append({"iter": ranked["iteration"], "result": "timeout_worse", "reason": str(exc)})
            fallbacks_used += 1
            continue
        except Exception as exc:
            output.progress(f"  [verify] _measure failed: {exc}")
            verify_log.append({"iter": ranked["iteration"], "result": "exec_failed", "reason": str(exc)})
            fallbacks_used += 1
            continue

        equiv, reason = row_equiv_fn(measured)
        if not equiv:
            output.progress(f"  [verify] L3 row-equiv FAIL — {reason}")
            verify_log.append({"iter": ranked["iteration"], "result": "row_equiv_fail", "reason": reason})
            fallbacks_used += 1
            continue

        # WINNER
        winner_sql = ranked["sql"]
        winner_measure = measured
        winner_ranked = ranked
        # Item 14a: metric key MUST be present in verify_log (behavioral, not cosmetic).
        verify_log.append({"iter": ranked["iteration"], "result": "verified", "metric": metric_fn(measured)})
        break

    # Item 14b: single emission via empty_message param; no separate adapter emit.
    if surviving_better_was_empty:
        output.progress(
            empty_message
            or "  [verify] No candidate beats baseline plan cost — original SQL unchanged"
        )

    return _PlanCostCoreResult(
        winner_sql=winner_sql,
        winner_measure=winner_measure,
        winner_ranked=winner_ranked,
        history=history,
        verify_log=verify_log,
        candidates_evaluated=len(candidates),
        fallbacks_used=fallbacks_used,
        baseline_cost=baseline_cost,
        surviving_better_was_empty=surviving_better_was_empty,
    )


def make_query_max_run_time_sql(baseline_wall_ms: float) -> str:
    """Build a `SET SESSION query_max_run_time = '<N>ms'` statement.

    N comes from make_candidate_timeout_ms(): ceil(baseline_wall_ms ×
    CANDIDATE_TIMEOUT_HEADROOM), currently 2x, clamped to at least 2000ms so
    tiny baselines don't produce absurdly short timeouts during smoke runs.
    """
    n_ms = make_candidate_timeout_ms(baseline_wall_ms)
    return f"SET SESSION query_max_run_time = '{n_ms}ms'"


def make_candidate_timeout_ms(baseline_wall_ms: float) -> int:
    """Return the per-candidate kill-timeout derived from baseline wall time.

    Uses ``CANDIDATE_TIMEOUT_HEADROOM`` (> 1) so a candidate that runs at roughly
    baseline speed is NOT killed — only candidates substantially slower than
    baseline are. The 2s floor protects fast baselines (a sub-second baseline
    must not produce a sub-second timeout that the candidate's own EXPLAIN /
    measurement overhead would blow through). The long-query gate's worst-case
    prediction keeps using ``PER_CANDIDATE_TIMEOUT_FACTOR`` separately.
    """
    import math
    return max(2000, int(math.ceil(baseline_wall_ms * CANDIDATE_TIMEOUT_HEADROOM)))


def apply_safe_limit(sql: str, limit: int) -> str:
    """Wrap SQL in SELECT * FROM (<orig>) LIMIT N. Caller's responsibility."""
    if limit <= 0:
        return sql
    stripped = sql.strip().rstrip(";").strip()
    return f"SELECT * FROM ({stripped}) AS _safe_wrapped LIMIT {limit}"


# ── No-data detection (v28 T9) ────────────────────────────────────────────────

# Tier 1: Trino structured errorName tokens (uppercase, appear in exception text).
_TABLE_NOT_FOUND_PLAIN: frozenset = frozenset({
    "TABLE_NOT_FOUND",
    "SCHEMA_NOT_FOUND",
    "CATALOG_NOT_FOUND",
})

# Tier 2: anchored free-text fallback.
# CONTAINER subjects only: Table, View, Schema, Catalog.
# Case-SENSITIVE on the subject word: Trino capitalizes the leading subject noun
# ("Table 'x' does not exist") but NOT "Materialized view" (lowercase "view").
# A case-sensitive `View` therefore matches "View 'a.v' does not exist" but NOT
# "Materialized view 'm' does not exist" — the latter is a real failure.
# The `\S+` matches any quoted/back-ticked/unquoted object name token.
_TABLE_NOT_FOUND_REGEX: tuple = (
    re.compile(r"\b(?:Table|View|Schema|Catalog)\s+\S+\s+[Dd]oes not exist"),
    re.compile(r"\bTable\b.*\bnot found\b", re.IGNORECASE),  # old "Table.*not found" preserved
)


def detect_no_data_reason(
    *,
    baseline_row_count: Optional[int] = None,
    baseline_exc: Optional[BaseException] = None,
) -> Optional[str]:
    """Classify why a baseline run has no usable data.

    Returns one of:
        - "table_not_found": exception matches a known TABLE/SCHEMA/CATALOG not-found pattern
        - "empty_result": baseline succeeded but row count == 0
        - None: there is data (or it is a different error)

    Direction-of-error rule: when unsure, return None to SURFACE the real error.
    A false-negative (real not-found slips through to None) is safer than a
    false-positive (a genuine failure swallowed into a no-data advisory report).
    """
    if baseline_exc is not None:
        msg = str(baseline_exc)
        upper = msg.upper()
        for marker in _TABLE_NOT_FOUND_PLAIN:          # tier 1 (exact token)
            if marker in upper:
                return "table_not_found"
        for pattern in _TABLE_NOT_FOUND_REGEX:         # tier 2 (anchored regex)
            if pattern.search(msg):
                return "table_not_found"
        return None                                    # everything else → real failure
    if baseline_row_count is not None and baseline_row_count == 0:
        return "empty_result"
    return None


def build_preflight_decision(
    *,
    diagnose_only: bool,
    baseline_row_count: Optional[int],
    baseline_exc: Optional[BaseException],
    gate: Optional["LongQueryGateResult"],
    long_query_opt_in: bool,
    plan_cost_available: bool,
    seen_no_estimates: bool,
    max_iterations: int,
) -> "PreflightDecision":
    """Pure routing decision. No I/O, no network, no LLM. Inputs are
    pre-computed facts (NOT live callables). detect_no_data_reason is called
    INSIDE so both paths classify identically (single source of routing truth).

    Decision tree (first match wins — exhaustive over all routes):
      1. diagnose_only            -> DIAGNOSE_ONLY
      2. no-data (rows/exc)       -> NO_DATA
      3. other baseline exc       -> REAL_FAILURE
      4. gate.ok is False         -> LONG_QUERY_ABORT
      5. opt-in & plan estimates & max_iter>0 -> PLAN_COST_LOOP
      6. otherwise                -> STANDARD_LOOP
    """
    if diagnose_only:
        return PreflightDecision(route=PreflightRoute.DIAGNOSE_ONLY)

    no_data = detect_no_data_reason(
        baseline_row_count=baseline_row_count,
        baseline_exc=baseline_exc,
    )
    if no_data is not None:
        return PreflightDecision(
            route=PreflightRoute.NO_DATA,
            no_data_reason=no_data,
            baseline_exc=baseline_exc,
        )
    if baseline_exc is not None:
        return PreflightDecision(
            route=PreflightRoute.REAL_FAILURE,
            baseline_exc=baseline_exc,
        )

    assert gate is not None, (
        "build_preflight_decision: gate required when baseline succeeded "
        "(baseline_exc is None and no_data is None)"
    )
    if not gate.ok:
        return PreflightDecision(
            route=PreflightRoute.LONG_QUERY_ABORT,
            gate_result=gate,
        )

    if long_query_opt_in and plan_cost_available and max_iterations > 0:
        return PreflightDecision(
            route=PreflightRoute.PLAN_COST_LOOP,
            plan_cost_available=True,
            seen_no_estimates=seen_no_estimates,
        )

    return PreflightDecision(
        route=PreflightRoute.STANDARD_LOOP,
        plan_cost_available=False,
        seen_no_estimates=seen_no_estimates,
    )
