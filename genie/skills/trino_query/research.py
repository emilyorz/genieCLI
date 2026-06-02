"""trino-research — Autoresearch mode for SQL query optimization.

Architecture (v2 — 2026-04-03):
- AI returns COMPLETE SQL each iteration (no file_patch dependency)
- Verify measures median of N runs to reduce cache noise
- Row-count guard rejects semantically-wrong optimizations
- Supports both interactive and non-interactive (parameterized) entry

Usage in CLI:
  Interactive:
    /trino-research
  Non-interactive:
    /trino-research --file query.sql --metric cpu_time_ms --iterations 5 --runs 3
"""
from __future__ import annotations

import re
import statistics
import threading
import time
from pathlib import Path
from typing import Callable, Optional

from genie.core.sql_extraction import extract_sql_from_reply
from genie.skills.mcp_trino.preflight import CandidateTimeoutError, make_candidate_timeout_ms
from genie.skills.trino_query.connection import get_active_profile
from genie.skills.trino_query import QueryMetrics, _extract_metrics


# ---------------------------------------------------------------------------
# Measurement helpers (run in-process, no subprocess/verify.py needed)
# ---------------------------------------------------------------------------

def _execute_sql_sync(sql: str, capture_rows: bool = False) -> tuple[int, QueryMetrics, list]:
    """Execute SQL on Trino, return (row_count, metrics, rows).

    When capture_rows=True, actual row data is returned for equivalence checks.
    """
    cfg = get_active_profile()
    conn = cfg.connect()
    cur = conn.cursor()
    cur.execute(sql)
    try:
        rows = cur.fetchall()
        row_count = len(rows)
    except Exception:
        rows = []
        row_count = 0
    stats = getattr(cur, "stats", {}) or {}
    metrics = _extract_metrics(stats)
    conn.close()
    return row_count, metrics, rows if capture_rows else []


def _execute_sql(
    sql: str,
    capture_rows: bool = False,
    timeout_ms: Optional[float] = None,
    label: str = "candidate",
) -> tuple[int, QueryMetrics, list]:
    """Execute SQL with an optional wall-clock timeout.

    The Trino Python cursor exposes ``cancel()``, so candidate timeouts can
    stop the server-side query instead of waiting for the full driver request.
    """
    if timeout_ms is None or timeout_ms <= 0:
        return _execute_sql_sync(sql, capture_rows=capture_rows)

    result: dict[str, tuple[int, QueryMetrics, list]] = {}
    error: dict[str, BaseException] = {}
    state: dict[str, object] = {}

    def runner() -> None:
        conn = None
        cur = None
        try:
            cfg = get_active_profile()
            conn = cfg.connect()
            state["conn"] = conn
            cur = conn.cursor()
            state["cur"] = cur
            cur.execute(sql)
            try:
                rows = cur.fetchall()
                row_count = len(rows)
            except Exception:
                rows = []
                row_count = 0
            stats = getattr(cur, "stats", {}) or {}
            metrics = _extract_metrics(stats)
            result["value"] = (row_count, metrics, rows if capture_rows else [])
        except BaseException as exc:
            error["exc"] = exc
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass

    thread = threading.Thread(target=runner, daemon=True)
    thread.start()
    thread.join(timeout_ms / 1000.0)
    if thread.is_alive():
        cur = state.get("cur")
        if cur is not None and hasattr(cur, "cancel"):
            try:
                cur.cancel()
            except Exception:
                pass
        conn = state.get("conn")
        if conn is not None and hasattr(conn, "close"):
            try:
                conn.close()
            except Exception:
                pass
        thread.join(timeout=0.2)
        raise CandidateTimeoutError(timeout_ms, label)

    if "exc" in error:
        raise error["exc"]
    if "value" not in result:
        raise RuntimeError("Trino query finished without returning a result")
    return result["value"]


def _measure(
    sql: str,
    metric_key: str,
    runs: int,
    capture_rows: bool = False,
    output=None,
    label: str = "query",
    timeout_ms: Optional[float] = None,
) -> dict:
    """Run SQL `runs` times, return median metric + row_count + all samples.

    When capture_rows=True, the rows from the LAST run are included for
    equivalence checking.
    """
    samples = []
    all_metrics = []
    row_count = 0
    last_rows = []

    for i in range(runs):
        # Capture rows only on last run to avoid memory waste
        capture = capture_rows and (i == runs - 1)
        run_label = f"{label}: run {i + 1}/{runs}"
        if timeout_ms is not None:
            run_label = f"{run_label} limit={timeout_ms / 1000.0:.1f}s"
        if output and hasattr(output, "status"):
            with output.status(run_label):
                rc, m, rows = _execute_sql(
                    sql, capture_rows=capture,
                    timeout_ms=timeout_ms, label=label,
                )
        else:
            rc, m, rows = _execute_sql(
                sql, capture_rows=capture,
                timeout_ms=timeout_ms, label=label,
            )
        row_count = rc
        if capture:
            last_rows = rows
        value = float(getattr(m, metric_key, 0) or 0)
        samples.append(value)
        all_metrics.append(m)

    median_val = statistics.median(samples)
    # Pick the run closest to median for full metrics display
    median_idx = min(range(len(samples)), key=lambda i: abs(samples[i] - median_val))

    return {
        "median": median_val,
        "samples": samples,
        "row_count": row_count,
        "rows": last_rows,
        "metrics": all_metrics[median_idx],
    }


def _baseline_wall_ms(metrics) -> float:
    """Best available wall-clock duration from Trino metrics.

    Takes the LARGEST available numeric measure so the per-candidate kill-timeout
    basis never under-estimates baseline (the EXPLAIN-stage wall_time is often
    0/tiny). Non-numeric attribute values are treated as 0.
    """
    def _num(v) -> float:
        return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else 0.0

    return float(
        max(
            _num(getattr(metrics, "query_time_ms", 0)),
            _num(getattr(metrics, "wall_time_ms", 0)),
            _num(getattr(metrics, "elapsed_time_ms", 0)),
        )
    )


def _normalize_row(row: tuple) -> tuple:
    """Normalize a row for comparison (handle float precision, None, etc)."""
    result = []
    for val in row:
        if isinstance(val, float):
            result.append(round(val, 6))
        else:
            result.append(val)
    return tuple(result)


def _results_equivalent(rows_a: list, rows_b: list) -> tuple[bool, str]:
    """Check if two result sets are equivalent (same rows, same order).

    Returns (equivalent, reason).
    """
    if len(rows_a) != len(rows_b):
        return False, f"row count differs: {len(rows_a)} vs {len(rows_b)}"

    if not rows_a:
        return True, "both empty"

    # Compare column count
    if len(rows_a[0]) != len(rows_b[0]):
        return False, f"column count differs: {len(rows_a[0])} vs {len(rows_b[0])}"

    # Normalize and compare row by row
    mismatches = 0
    first_mismatch = None
    for i, (a, b) in enumerate(zip(rows_a, rows_b)):
        na = _normalize_row(a)
        nb = _normalize_row(b)
        if na != nb:
            mismatches += 1
            if first_mismatch is None:
                first_mismatch = f"row {i}: {na} vs {nb}"

    if mismatches == 0:
        return True, "exact match"

    return False, f"{mismatches} row(s) differ; first: {first_mismatch}"


def _lint_sql(sql: str) -> tuple[bool, str]:
    """Lint SQL, return (passed, message). F with parse error = fail."""
    try:
        from genie.core.lint_analyzer import analyze
        result = analyze(sql)
        if result.score == "F" and result.parse_error:
            return False, f"parse error: {result.parse_error}"
        return True, f"lint score={result.score}"
    except Exception as e:
        return False, f"lint error: {e}"


def _format_static_findings(report) -> str:
    """Render a sql_static report as a compact bullet list for prompt injection."""
    if report is None or not report.findings:
        return ""
    lines = []
    for f in report.findings:
        lines.append(f"  - [{f.severity}] {f.rule_id} (line {f.line}): {f.message}")
        lines.append(f"      → {f.suggestion}")
    return "\n".join(lines)


def _no_data_report(
    *,
    sql: str,
    reason: str,
    static_report,
    llm_finishing: Optional[str],
    model: str,
) -> str:
    """Render the no-data path report (sticky warning + L1 findings + L3 finishing)."""
    from datetime import datetime

    reason_human = {
        "table_not_found": "referenced table/schema/catalog does not exist",
        "empty_result": "query ran but returned 0 rows",
    }.get(reason, reason)

    lines = [
        "# Trino Query Static Analysis Report (no-data path)",
        "",
        f"**Date:** {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        f"**Model:** {model}",
        f"**Mode:** static — iteration loop skipped because **{reason_human}**",
        "",
        "## Why this report instead of an iteration run",
        "",
        f"- {reason_human}",
        "- Without measurable rows there is nothing to optimize empirically.",
        "- Static analysis still surfaces query-shape issues that hold regardless of data.",
        "",
        "## Original SQL",
        "",
        "```sql",
        sql.rstrip(),
        "```",
        "",
        "## Static analysis findings",
        "",
    ]

    if static_report is None or static_report.parse_error:
        err = (static_report.parse_error if static_report else "analyzer unavailable")
        lines += [f"_Parse failed: {err}_", ""]
    elif not static_report.findings:
        lines += [
            "_No structural issues detected._",
            "",
            "If the query is correct, the next step is to confirm the table name / "
            "catalog / schema, or re-check the partition filter.",
            "",
        ]
    else:
        lines += [f"**Summary:** {static_report.summary}", ""]
        lines += ["| # | Severity | Rule | Line | Message | Suggestion |",
                  "|---|---|---|---|---|---|"]
        for i, f in enumerate(static_report.findings, 1):
            msg = f.message.replace("|", "\\|")
            sug = f.suggestion.replace("|", "\\|")
            lines.append(f"| {i} | {f.severity} | {f.rule_id} | {f.line} | {msg} | {sug} |")
        lines.append("")

    if llm_finishing:
        lines += ["## LLM finishing pass", "", llm_finishing.rstrip(), ""]

    lines += ["## Next steps", "",
              "1. Verify the referenced tables exist and contain data.",
              "2. Apply the highest-severity findings above.",
              "3. Re-run `/trino-research` against the corrected query.", ""]
    return "\n".join(lines)




# ---------------------------------------------------------------------------
# No-data path (v28 T9 + T10)
# ---------------------------------------------------------------------------

def _run_no_data_path(
    *,
    provider,
    model: str,
    reasoning: str,
    original_sql: str,
    no_data_reason: str,
    static_report,
    baseline_exc: Optional[BaseException],
    output,
) -> dict:
    """Single-call static analysis + optional LLM finishing — no iteration loop.

    Triggered when the baseline either raised a table-not-found-shaped error
    or returned 0 rows. Cost: ≤1 LLM call vs N for the iteration path.
    """
    reason_human = {
        "table_not_found": "table/schema/catalog not found",
        "empty_result": "baseline returned 0 rows",
    }.get(no_data_reason, no_data_reason)

    output.print("")
    output.error(f"  [no-data] {reason_human} — switching to static analysis mode")
    if baseline_exc is not None:
        output.print(f"  [dim]baseline error: {baseline_exc}[/dim]")

    if static_report is None:
        output.error("  Static analyzer unavailable — emitting bare report")
    elif static_report.parse_error:
        output.error(f"  Static parse failed: {static_report.parse_error}")
    else:
        output.progress(f"  Static analysis: {static_report.summary}")
        for f in static_report.findings:
            output.print(f"    [{f.severity[0].upper()}] {f.rule_id}: {f.message}")

    # Optional finishing pass: ask the model to synthesise findings into a
    # rewrite recommendation. Single call — no iteration, no measurement.
    llm_finishing: Optional[str] = None
    has_findings = bool(static_report and static_report.findings)
    if provider is not None and has_findings:
        try:
            from genie.core.provider import CompletionRequest
            from genie.session.manager import new_msg

            findings_text = _format_static_findings(static_report)
            sys_prompt = (
                "You are a Trino SQL reviewer. The user's query could not be benchmarked "
                "(table missing or empty), but a static analyzer found structural issues. "
                "Combine the findings below with the SQL and write a concise rewrite "
                "recommendation. Return: (1) a one-paragraph diagnosis, (2) a single "
                "rewritten SQL block, (3) a short list of any further checks the user "
                "should perform before re-running."
            )
            user_prompt = (
                f"Original SQL:\n```sql\n{original_sql.rstrip()}\n```\n\n"
                f"Static findings:\n{findings_text}\n\n"
                f"Reason for no-data path: {reason_human}."
            )
            req = CompletionRequest(
                messages=[new_msg("system", sys_prompt), new_msg("user", user_prompt)],
                model=model,
                reasoning=reasoning,
            )
            output.progress("  Calling LLM for finishing pass...")
            llm_finishing = provider.complete_text(req)
        except Exception as exc:
            output.progress(f"  [warn] LLM finishing pass failed: {exc}")
            llm_finishing = None

    report_md = _no_data_report(
        sql=original_sql,
        reason=no_data_reason,
        static_report=static_report,
        llm_finishing=llm_finishing,
        model=model,
    )

    return {
        "status": "no_data",
        "reason": no_data_reason,
        "baseline_error": str(baseline_exc) if baseline_exc else None,
        "original_sql": original_sql,
        "best_sql": original_sql,
        "static_findings": (
            [
                {
                    "severity": f.severity,
                    "rule_id": f.rule_id,
                    "message": f.message,
                    "suggestion": f.suggestion,
                    "line": f.line,
                }
                for f in static_report.findings
            ]
            if static_report else []
        ),
        "llm_finishing": llm_finishing,
        "report_markdown": report_md,
    }


# ---------------------------------------------------------------------------
# Long-query plan-cost loop (v28 T4)
# ---------------------------------------------------------------------------

def _run_plan_cost_loop(
    *,
    provider,
    model: str,
    reasoning: str,
    original_sql: str,
    metric_key: str,
    max_iterations: int,
    verify_runs: int,
    output,
    build_prompt: Callable[..., str],
    baseline: dict,
    baseline_data: list,
    static_report,
    explain_runner: Callable[[str], Optional[str]],
    max_fallbacks: int,
    candidate_timeout_ms: Optional[float] = None,
) -> dict:
    """Plan-cost ranking + L1 structural guard + K-retry on row-equivalence.

    Iteration phase: generate candidates via LLM, score each by EXPLAIN plan
    cost (rows × bytes estimate). No execution, no measurement — cheap.
    Reject candidates whose plan signature diverges from baseline (L1).

    Verification phase: rank surviving candidates by plan cost ascending; for
    each in turn run real _measure + _results_equivalent. First L3 PASS wins.
    If all top candidates fail L3 within max_fallbacks attempts, emit
    `no_verifiable_improvement`.
    """
    from genie.core.provider import CompletionRequest
    from genie.session.manager import new_msg, new_session
    from genie.skills.mcp_trino.pre_execution_diagnosis import pre_execution_diagnosis
    from genie.skills.mcp_trino.preflight import plan_cost, _combine_cost
    from genie.skills.mcp_trino.rule_gate import (
        build_rule_gate_summary,
        format_rule_gate_for_prompt,
        render_rule_gate_summary,
    )
    from genie.skills.trino_query.plan_signature import (
        plan_signature,
        structural_equivalent,
    )

    baseline_metric = baseline["median"]
    baseline_rows = baseline["row_count"]
    if candidate_timeout_ms is None:
        baseline_wall_ms = _baseline_wall_ms(baseline["metrics"])
        candidate_timeout_ms = make_candidate_timeout_ms(baseline_wall_ms) if baseline_wall_ms > 0 else None

    # Baseline plan cost + signature
    baseline_rows_est, baseline_bytes_est, baseline_plan = plan_cost(
        original_sql, explain_runner
    )
    baseline_sig = plan_signature(baseline_plan) if baseline_plan is not None else None
    baseline_cost = _combine_cost(baseline_rows_est, baseline_bytes_est)
    directions = pre_execution_diagnosis(
        original_sql,
        static_report=static_report,
        explain_cost=(baseline_rows_est, baseline_bytes_est, baseline_plan),
        table_metadata=None,
        peak_memory_bytes=getattr(baseline.get("metrics"), "peak_memory_bytes", 0) or None,
    )
    rule_gate = build_rule_gate_summary(static_report, directions)
    rule_gate_block = format_rule_gate_for_prompt(rule_gate)

    output.print("")
    timeout_text = (
        f", candidate_timeout={candidate_timeout_ms / 1000.0:.1f}s"
        if candidate_timeout_ms is not None else ""
    )
    output.progress(
        f"  [long-query] Plan-cost loop active "
        f"(baseline rows~{baseline_rows_est}, bytes~{baseline_bytes_est}, "
        f"max_fallbacks={max_fallbacks}{timeout_text})"
    )
    render_rule_gate_summary(output, rule_gate)

    # Session setup — same prompt structure as the legacy loop
    skill_prompt = build_prompt(True, model)
    sys_prompt = (
        f"You are optimizing a Trino SQL query for performance.\n"
        f"Target metric: {metric_key} (lower is better).\n\n"
        f"Rules:\n"
        f"- Return the COMPLETE optimized SQL in a ```sql code block\n"
        f"- Do NOT use file_patch or any tool calls\n"
        f"- Keep the EXACT same result set — same columns, same rows, same values\n"
        f"- Make ONE focused change per iteration\n"
        f"- Trino best practices: partition filters, named columns, predicate pushdown, "
        f"projection pruning, APPROX_DISTINCT over COUNT(DISTINCT), COALESCE instead of NVL\n"
        f"- Treat CTE step materialization as advisory only; keep this loop read-only\n\n"
        f"{(rule_gate_block + chr(10) + chr(10)) if rule_gate_block else ''}"
        f"{skill_prompt}"
    )
    session = new_session(sys_prompt)

    candidates: list[dict] = []  # ranked entries with plan_cost + sig + sql
    history: list[dict] = []

    for iteration in range(1, max_iterations + 1):
        output.print("")
        output.progress(f"  ── Iteration {iteration}/{max_iterations} (plan-cost mode) ──")

        static_block = ""
        if iteration == 1 and static_report and static_report.findings:
            static_block = (
                "Static analysis findings (sqlglot AST rules — apply these in priority order):\n"
                f"{_format_static_findings(static_report)}\n\n"
            )

        # Lean history
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
        reply = provider.complete_text(req)
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

        # Lint guard (cheap)
        lint_ok, lint_msg = _lint_sql(candidate_sql)
        if not lint_ok:
            output.progress(f"  [SKIP] Lint failed: {lint_msg}")
            history.append({
                "iteration": iteration, "status": "lint_failed",
                "candidate_sql": candidate_sql, "plan_cost": None,
            })
            session["history"].append(new_msg("user", f"SQL failed lint: {lint_msg}. Try a different change."))
            continue

        # Plan cost (cheap, no execution)
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
                    f"  [REJECT] Structural divergence (L1) — candidate plan shape differs from baseline"
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
        # impossible (would raise TypeError on None < int).  Skip ranking and treat
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

    # ── Verification phase: rank by plan cost, K-retry on L3 fail ──
    output.print("")
    output.progress(f"  [verify] {len(candidates)} candidate(s) survived L1; ranking by plan cost")

    surviving_better = sorted(
        [c for c in candidates if c["plan_cost"] < baseline_cost],
        key=lambda c: c["plan_cost"],
    )

    if not surviving_better:
        output.progress("  [verify] No candidate beats baseline plan cost — emitting no_verifiable_improvement")
        return {
            "status": "no_verifiable_improvement",
            "baseline_metric": baseline_metric,
            "best_metric": baseline_metric,
            "total_improvement": 0.0,
            "improvement_pct": 0.0,
            "iterations": len(history),
            "kept": 0,
            "baseline_rows": baseline_rows,
            "baseline_plan_cost": baseline_cost,
            "original_sql": original_sql,
            "best_sql": original_sql,
            "history": history,
            "candidates_evaluated": len(candidates),
        }

    fallbacks_used = 0
    winner: Optional[dict] = None
    verify_log: list[dict] = []
    for ranked in surviving_better:
        if fallbacks_used > max_fallbacks:
            output.progress(f"  [verify] Exhausted K={max_fallbacks} fallbacks")
            break
        output.progress(
            f"  [verify] Trying iter#{ranked['iteration']} "
            f"(plan_cost={ranked['plan_cost']:.2e})"
        )
        try:
            measured = _measure(
                ranked["sql"], metric_key, verify_runs, capture_rows=True,
                output=output, label=f"verify iter {ranked['iteration']}",
                timeout_ms=candidate_timeout_ms,
            )
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

        equiv, reason = _results_equivalent(baseline_data, measured["rows"])
        if not equiv:
            output.progress(f"  [verify] L3 row-equiv FAIL — {reason}")
            verify_log.append({"iter": ranked["iteration"], "result": "row_equiv_fail", "reason": reason})
            fallbacks_used += 1
            continue

        # WINNER
        winner = {
            **ranked,
            "measured_metric": measured["median"],
            "measured_rows": measured["row_count"],
            "samples": measured["samples"],
            "metrics": measured["metrics"],
        }
        verify_log.append({"iter": ranked["iteration"], "result": "verified", "metric": measured["median"]})
        break

    if winner is None:
        return {
            "status": "no_verifiable_improvement",
            "baseline_metric": baseline_metric,
            "best_metric": baseline_metric,
            "total_improvement": 0.0,
            "improvement_pct": 0.0,
            "iterations": len(history),
            "kept": 0,
            "baseline_rows": baseline_rows,
            "baseline_plan_cost": baseline_cost,
            "original_sql": original_sql,
            "best_sql": original_sql,
            "history": history,
            "verify_log": verify_log,
            "candidates_evaluated": len(candidates),
        }

    # Convert winner into legacy-shape result so report rendering works as-is
    winner_history = [{
        "iteration": h["iteration"],
        "status": "improved" if (h["candidate_sql"] == winner["sql"]) else h["status"],
        "metric": winner["measured_metric"] if (h["candidate_sql"] == winner["sql"]) else baseline_metric,
        "delta": winner["measured_metric"] - baseline_metric if (h["candidate_sql"] == winner["sql"]) else 0.0,
        "hypothesis": "(plan-cost-loop)",
        "base_sql": original_sql,
        "candidate_sql": h.get("candidate_sql"),
    } for h in history]

    return {
        "status": "completed",
        "mode": "plan_cost",
        "baseline_metric": baseline_metric,
        "best_metric": winner["measured_metric"],
        "total_improvement": winner["measured_metric"] - baseline_metric,
        "improvement_pct": (
            (winner["measured_metric"] - baseline_metric) / baseline_metric * 100
            if baseline_metric else 0
        ),
        "iterations": len(history),
        "kept": 1,
        "baseline_rows": baseline_rows,
        "baseline_plan_cost": baseline_cost,
        "winner_plan_cost": winner["plan_cost"],
        "original_sql": original_sql,
        "best_sql": winner["sql"],
        "history": winner_history,
        "verify_log": verify_log,
        "candidates_evaluated": len(candidates),
        "fallbacks_used": fallbacks_used,
    }


# ---------------------------------------------------------------------------
# Core iteration loop (no RunManager / file_patch / git dependency)
# ---------------------------------------------------------------------------

def _assemble_direct_directions(
    original_sql: str,
    static_report,
    explain_runner: Optional[Callable[[str], Optional[str]]],
    *,
    peak_memory_bytes: Optional[int] = None,
):
    """Assemble ranked optimization directions for the --direct path.

    Mirrors the MCP path's `_assemble_mcp_directions` but with no table-metadata
    fetcher (the direct path has none). Diagnosis is driven by static findings +
    EXPLAIN (FORMAT JSON) plan cost (zero query) + the baseline's real peak
    memory when available. Never raises; a failing EXPLAIN runner yields no
    plan-cost contribution.
    """
    from genie.skills.mcp_trino.pre_execution_diagnosis import pre_execution_diagnosis
    from genie.skills.mcp_trino.preflight import plan_cost as _plan_cost

    explain_cost = None
    if explain_runner is not None:
        try:
            explain_cost = _plan_cost(original_sql, explain_runner)
        except Exception:
            explain_cost = None

    return pre_execution_diagnosis(
        original_sql,
        static_report=static_report,
        explain_cost=explain_cost,
        table_metadata=None,
        peak_memory_bytes=peak_memory_bytes,
    )


def _run_optimization_loop(
    provider,
    model: str,
    reasoning: str,
    original_sql: str,
    metric_key: str,
    max_iterations: int,
    verify_runs: int,
    output,
    build_prompt: Callable[..., str],
    *,
    long_query_opt_in: bool = True,
    long_query_threshold_s: Optional[int] = None,
    max_fallbacks: Optional[int] = None,
    explain_runner: Optional[Callable[[str], Optional[str]]] = None,
    diagnose_only: bool = False,
) -> dict:
    """Run the optimization loop. Returns summary dict.

    v28 dispatch:
        - baseline raises with table-not-found-shaped error → no-data path
        - baseline returns 0 rows                          → no-data path
        - else                                              → has-data iteration
          (with sql_static findings injected into the per-iteration context)
    """
    from genie.core.provider import CompletionRequest
    from genie.session.manager import new_msg, new_session
    from genie.skills.mcp_trino.preflight import detect_no_data_reason
    from genie.skills.trino_query.sql_static import analyze as static_analyze
    from genie.skills.trino_query.sql_static import summary_line as _static_summary_line

    # ── Static analysis (cheap; runs in both paths) ──
    try:
        static_report = static_analyze(original_sql)
    except Exception as exc:
        output.progress(f"  [warn] static analysis skipped: {exc}")
        static_report = None
    if static_report is not None:
        output.progress(f"  Static analysis: {_static_summary_line(static_report)}")

    # ── --diagnose-only short-circuit (v29 T3): zero query cost ──
    # No baseline, no iteration loop, no EXPLAIN ANALYZE. EXPLAIN (FORMAT JSON)
    # plans the query without running it; static analysis is cheap. Emit a
    # directed report and stop. peak_memory_bytes is None (no run happened).
    if diagnose_only:
        from genie.skills.mcp_trino.pre_execution_diagnosis import format_directions_report
        output.progress("  --diagnose-only: EXPLAIN-cost + static, no query execution")
        directions = _assemble_direct_directions(
            original_sql, static_report, explain_runner, peak_memory_bytes=None
        )
        report_md = format_directions_report(
            directions, sql=original_sql,
            reason="--diagnose-only requested (no baseline, no iteration)",
            model=model,
        )
        return {"status": "diagnosed", "report_markdown": report_md}

    # ── Baseline ──
    output.progress("  Measuring baseline...")
    baseline = None
    baseline_exc: Optional[BaseException] = None
    try:
        baseline = _measure(
            original_sql, metric_key, verify_runs, capture_rows=True,
            output=output, label="baseline",
        )
    except Exception as e:
        baseline_exc = e

    no_data = detect_no_data_reason(
        baseline_row_count=baseline["row_count"] if baseline else None,
        baseline_exc=baseline_exc,
    )

    if no_data is not None:
        return _run_no_data_path(
            provider=provider,
            model=model,
            reasoning=reasoning,
            original_sql=original_sql,
            no_data_reason=no_data,
            static_report=static_report,
            baseline_exc=baseline_exc,
            output=output,
        )

    if baseline_exc is not None:
        # Real failure — not a no-data case
        output.error(f"  Baseline measurement failed: {baseline_exc}")
        return {"status": "failed", "error": str(baseline_exc)}

    baseline_metric = baseline["median"]
    baseline_rows = baseline["row_count"]
    baseline_data = baseline["rows"]
    baseline_wall_ms = _baseline_wall_ms(baseline["metrics"])
    candidate_timeout_ms = make_candidate_timeout_ms(baseline_wall_ms) if baseline_wall_ms > 0 else None

    output.progress(f"  Baseline {metric_key}: {baseline_metric} (median of {verify_runs} runs)")
    output.progress(f"  Baseline row count: {baseline_rows}")
    _print_metrics(output, baseline["metrics"])
    if candidate_timeout_ms is not None:
        output.progress(
            f"  Candidate timeout: {candidate_timeout_ms / 1000.0:.1f}s "
            f"(baseline wall-time)"
        )

    if static_report and static_report.findings:
        output.progress(
            f"  Static analysis: {static_report.summary} "
            f"({len(static_report.findings)} finding(s) — feeding into prompt)"
        )

    # ── Upfront cost gate (v28) ──
    from genie.skills.mcp_trino.preflight import (
        DEFAULT_LONG_QUERY_THRESHOLD_S,
        DEFAULT_MAX_FALLBACKS,
        check_long_query_gate,
    )
    threshold_s = long_query_threshold_s if long_query_threshold_s is not None else DEFAULT_LONG_QUERY_THRESHOLD_S
    fallbacks = max_fallbacks if max_fallbacks is not None else DEFAULT_MAX_FALLBACKS
    gate = check_long_query_gate(
        baseline_wall_ms=baseline_wall_ms,
        max_iterations=max_iterations,
        long_query_opt_in=long_query_opt_in,
        threshold_s=threshold_s,
        max_fallbacks=fallbacks,
    )
    if not gate.ok:
        # v29 T3: instead of a bare abort, emit a directed report.
        # The baseline already ran (one query) so its real peak memory feeds the
        # diagnosis; EXPLAIN (FORMAT JSON) + static add the rest. No further
        # query / no EXPLAIN ANALYZE / no iteration loop.
        from genie.skills.mcp_trino.pre_execution_diagnosis import format_directions_report
        output.progress(f"  Long-query gate: {gate.message}")
        output.progress("  Writing directed report and skipping further query executions")
        directions = _assemble_direct_directions(
            original_sql, static_report, explain_runner,
            peak_memory_bytes=getattr(baseline["metrics"], "peak_memory_bytes", 0) or None,
        )
        report_md = format_directions_report(
            directions, sql=original_sql, reason=gate.message, model=model,
            baseline_already_ran=True,
        )
        return {
            "status": "diagnosed",
            "reason": "long_query_gate",
            "message": gate.message,
            "report_markdown": report_md,
        }

    # ── v28 T4 dispatch: long-query plan-cost loop ──
    # When the user opts into long-query mode AND EXPLAIN yields real cost
    # ESTIMATES, skip per-iteration execution; rank candidates by plan cost;
    # verify only the top-K via real _measure with row-equivalence check.
    # A cluster without table statistics returns a plan but no estimates — then
    # plan-cost ranking is impossible (every candidate skips), so fall back to
    # the standard measure loop.
    plan_cost_available = False
    plan_seen_no_estimates = False
    if long_query_opt_in and explain_runner is not None:
        from genie.skills.mcp_trino.preflight import plan_cost as _plan_cost_probe
        try:
            _pr, _pb, _pp = _plan_cost_probe(original_sql, explain_runner)
            plan_cost_available = _pr is not None or _pb is not None
            plan_seen_no_estimates = (not plan_cost_available) and _pp is not None
        except Exception:
            plan_cost_available = False
    if plan_seen_no_estimates:
        output.progress(
            "  [info] Plan-cost mode unavailable: EXPLAIN returned a plan but no cost "
            "estimates (table statistics missing — run ANALYZE). Using standard iteration loop."
        )
    if plan_cost_available:
        return _run_plan_cost_loop(
            provider=provider,
            model=model,
            reasoning=reasoning,
            original_sql=original_sql,
            metric_key=metric_key,
            max_iterations=max_iterations,
            verify_runs=verify_runs,
            output=output,
            build_prompt=build_prompt,
            baseline=baseline,
            baseline_data=baseline_data,
            static_report=static_report,
            explain_runner=explain_runner,
            max_fallbacks=fallbacks,
            candidate_timeout_ms=candidate_timeout_ms,
        )

    # ── Pre-execution diagnosis (v29 T2 — dual-path parity with MCP path) ──
    # The --direct path has no table-metadata fetcher, so diagnosis is driven by
    # static findings + plan-cost estimates + the baseline's actual peak memory.
    from genie.skills.mcp_trino.pre_execution_diagnosis import format_directions_for_prompt
    from genie.skills.mcp_trino.rule_gate import (
        build_rule_gate_summary,
        format_rule_gate_for_prompt,
        render_rule_gate_summary,
    )

    directions = _assemble_direct_directions(
        original_sql, static_report, explain_runner,
        peak_memory_bytes=getattr(baseline["metrics"], "peak_memory_bytes", 0) or None,
    )
    rule_gate = build_rule_gate_summary(static_report, directions)
    rule_gate_block = format_rule_gate_for_prompt(rule_gate)
    directions_block = format_directions_for_prompt(directions)
    render_rule_gate_summary(output, rule_gate)
    if directions:
        output.progress(f"  Pre-execution diagnosis: {len(directions)} ranked direction(s) → prompt")

    # ── Session setup ──
    skill_prompt = build_prompt(True, model)
    sys_prompt = (
        f"You are optimizing a Trino SQL query for performance.\n"
        f"Target metric: {metric_key} (lower is better).\n\n"
        f"Rules:\n"
        f"- Return the COMPLETE optimized SQL in a ```sql code block\n"
        f"- Do NOT use file_patch or any tool calls\n"
        f"- Keep the EXACT same result set — same columns, same rows, same values\n"
        f"- Make ONE focused change per iteration\n"
        f"- Trino best practices: partition filters, named columns, predicate pushdown, "
        f"projection pruning, APPROX_DISTINCT over COUNT(DISTINCT), COALESCE instead of NVL\n"
        f"- Treat CTE step materialization as advisory only; keep this loop read-only\n\n"
        f"{(rule_gate_block + chr(10) + chr(10)) if rule_gate_block else ''}"
        f"{(directions_block + chr(10) + chr(10)) if directions_block else ''}"
        f"{skill_prompt}"
    )
    session = new_session(sys_prompt)

    best_sql = original_sql
    best_metric = baseline_metric
    best_metrics_obj = baseline["metrics"]  # v32 T2: track best candidate's full metrics
    history = []
    # v32 T1: per-iteration re-diagnosis cache keyed by SQL (mirrors the MCP
    # path). Seeded with the original block (already in the system prompt) so a
    # stable best_sql is never re-diagnosed.
    rediag_cache: dict[str, str] = {original_sql: directions_block}

    # ── Iteration loop ──
    for iteration in range(1, max_iterations + 1):
        output.print("")
        output.progress(f"  ── Iteration {iteration}/{max_iterations} ──")
        iter_base_sql = best_sql  # snapshot for per-iteration diff in report

        # Build context for AI
        last_str = "N/A (first iteration)"
        if history:
            last = history[-1]
            last_str = f"{last['status']} (metric={last['metric']:.1f}, delta={last['delta']:+.1f})"

        static_block = ""
        if iteration == 1 and static_report and static_report.findings:
            static_block = (
                "Static analysis findings (sqlglot AST rules — apply these in priority order):\n"
                f"{_format_static_findings(static_report)}\n\n"
            )

        # v32 T1: re-diagnose the current best_sql once it diverges from the
        # original (the system-prompt directions describe original_sql). Zero
        # query cost (static + EXPLAIN FORMAT JSON); cached by SQL so a stable
        # best_sql is not re-diagnosed.
        fresh_block = rediag_cache.get(best_sql)
        if fresh_block is None:
            try:
                fresh_block = format_directions_for_prompt(
                    _assemble_direct_directions(
                        best_sql, static_analyze(best_sql), explain_runner,
                        peak_memory_bytes=None,
                    )
                )
            except Exception:
                fresh_block = ""
            rediag_cache[best_sql] = fresh_block
        diag_line = f"{fresh_block}\n\n" if (fresh_block and best_sql != original_sql) else ""

        context = (
            f"[Trino Query Optimization — Iteration {iteration}]\n"
            f"Target metric: {metric_key} (lower is better)\n"
            f"Baseline: {baseline_metric}\n"
            f"Current best: {best_metric}\n"
            f"Last iteration: {last_str}\n\n"
            f"{static_block}"
            f"Current SQL:\n```sql\n{best_sql}\n```\n\n"
            f"{diag_line}"
            f"Return the COMPLETE optimized SQL in a ```sql block. ONE change only. "
            f"Do NOT include a trailing semicolon."
        )

        # Keep history lean: only system + last 4 messages (2 user/assistant pairs)
        # to avoid context bloat with local models
        sys_msgs = [m for m in session["history"] if m["role"] == "system"]
        non_sys = [m for m in session["history"] if m["role"] != "system"]
        session["history"] = sys_msgs + non_sys[-4:]

        session["history"].append(new_msg("user", context))

        # Get AI response
        output.progress("  AI thinking...")
        req = CompletionRequest(messages=session["history"], model=model, reasoning=reasoning)
        reply = provider.complete_text(req)

        if not reply:
            output.error("  Empty AI response — stopping.")
            break

        session["history"].append(new_msg("assistant", reply))

        # Extract SQL
        candidate_sql = extract_sql_from_reply(reply)
        if not candidate_sql:
            output.progress("  [SKIP] No SQL found in AI response.")
            session["history"].append(new_msg("user", "I couldn't find a SQL block in your response. Return the COMPLETE SQL in a ```sql block."))
            # One retry
            req2 = CompletionRequest(messages=session["history"], model=model, reasoning=reasoning)
            reply2 = provider.complete_text(req2)
            if reply2:
                session["history"].append(new_msg("assistant", reply2))
                candidate_sql = extract_sql_from_reply(reply2)
            if not candidate_sql:
                output.progress("  [SKIP] Still no SQL — skipping iteration.")
                history.append({
                    "iteration": iteration, "status": "no_sql",
                    "metric": best_metric, "delta": 0.0, "hypothesis": "no SQL extracted",
                    "base_sql": iter_base_sql, "candidate_sql": None,
                })
                continue

        # Extract hypothesis from AI reply (skip code fences, get first meaningful line)
        hypothesis = "?"
        if reply:
            for line in reply.split("\n"):
                line = line.strip()
                if line and not line.startswith("```") and not line.startswith("|"):
                    hypothesis = line
                    break
        output.progress(f"  [Hypothesis] {hypothesis[:80]}")

        # Guard 1: Lint check
        lint_ok, lint_msg = _lint_sql(candidate_sql)
        if not lint_ok:
            output.progress(f"  [REVERT] Lint failed: {lint_msg}")
            session["history"].append(new_msg("user", f"SQL failed lint: {lint_msg}. Change REVERTED."))
            history.append({
                "iteration": iteration, "status": "lint_failed",
                "metric": best_metric, "delta": 0.0, "hypothesis": hypothesis,
                "base_sql": iter_base_sql, "candidate_sql": candidate_sql,
            })
            continue

        # Guard 2: Execute and measure
        try:
            candidate = _measure(
                candidate_sql, metric_key, verify_runs, capture_rows=True,
                output=output, label=f"iter {iteration} candidate",
                timeout_ms=candidate_timeout_ms,
            )
        except CandidateTimeoutError as e:
            output.progress(f"  [REVERT] timeout_worse: {e}")
            session["history"].append(new_msg(
                "user",
                f"SQL exceeded the baseline wall-time limit: {e}. Change REVERTED."
            ))
            history.append({
                "iteration": iteration, "status": "timeout_worse",
                "metric": best_metric, "delta": 0.0, "hypothesis": hypothesis,
                "base_sql": iter_base_sql, "candidate_sql": candidate_sql,
            })
            continue
        except Exception as e:
            output.progress(f"  [REVERT] Execution failed: {e}")
            session["history"].append(new_msg("user", f"SQL execution failed: {e}. Change REVERTED."))
            history.append({
                "iteration": iteration, "status": "exec_failed",
                "metric": best_metric, "delta": 0.0, "hypothesis": hypothesis,
                "base_sql": iter_base_sql, "candidate_sql": candidate_sql,
            })
            continue

        candidate_metric = candidate["median"]
        candidate_rows = candidate["row_count"]
        candidate_data = candidate["rows"]
        delta = candidate_metric - best_metric

        # Guard 3: Result equivalence (not just row count — full data comparison)
        equiv, equiv_reason = _results_equivalent(baseline_data, candidate_data)
        if not equiv:
            output.progress(
                f"  [REVERT] Result mismatch: {equiv_reason} "
                f"(semantic drift detected)"
            )
            session["history"].append(new_msg(
                "user",
                f"Query results differ from baseline: {equiv_reason}. "
                f"This means the optimization changed the query semantics. Change REVERTED. "
                f"Try a different approach that preserves the exact same result set."
            ))
            history.append({
                "iteration": iteration, "status": "semantic_drift",
                "metric": candidate_metric, "delta": delta, "hypothesis": hypothesis,
                "base_sql": iter_base_sql, "candidate_sql": candidate_sql,
            })
            continue

        # Decision: keep or revert
        improved = candidate_metric < best_metric
        if improved:
            best_sql = candidate_sql
            best_metric = candidate_metric
            best_metrics_obj = candidate["metrics"]  # v32 T2
            status = "KEPT"
            status_icon = "+"
        else:
            status = "REVERTED"
            status_icon = "-"

        output.progress(
            f"  [{status_icon}] {status} | {metric_key}={candidate_metric:.1f} "
            f"(delta={delta:+.1f}, samples={candidate['samples']}) | "
            f"rows={candidate_rows}"
        )
        _print_metrics(output, candidate["metrics"])

        # Feed result back to AI
        session["history"].append(new_msg(
            "user",
            f"[Iteration {iteration} result]\n"
            f"Status: {status}\n"
            f"Metric ({metric_key}, median of {verify_runs} runs): {candidate_metric:.1f}\n"
            f"Delta vs current best: {delta:+.1f}\n"
            f"Row count: {candidate_rows} (baseline: {baseline_rows})\n"
            f"Samples: {candidate['samples']}\n"
            f"{'Change KEPT — this is now the current best.' if improved else 'Change REVERTED — current best unchanged.'}"
        ))

        history.append({
            "iteration": iteration,
            "status": "improved" if improved else "worse",
            "metric": candidate_metric,
            "delta": delta,
            "hypothesis": hypothesis,
            "base_sql": iter_base_sql,
            "candidate_sql": candidate_sql,
        })

        # Early exit: 3 consecutive non-improvements → plateau
        if len(history) >= 3:
            last_3 = history[-3:]
            if all(h["status"] != "improved" for h in last_3):
                output.progress(
                    f"\n  [EARLY STOP] 3 consecutive iterations without improvement — "
                    f"optimization has plateaued."
                )
                break

    # ── Direction efficacy (v32 T2) — observational attribution (mirrors MCP) ──
    from genie.skills.mcp_trino.pre_execution_diagnosis import (
        attribute_directions as _attribute_directions,
        format_attribution_report as _format_attribution_report,
    )
    _ATTR_KEYS = (
        "wall_time_ms", "query_time_ms", "cpu_time_ms",
        "peak_memory_bytes", "physical_input_bytes", "processed_rows", "total_splits",
    )

    def _metrics_attr_map(m):
        return {
            k: float(getattr(m, k))
            for k in _ATTR_KEYS
            if isinstance(getattr(m, k, None), (int, float))
        }

    _outcomes = _attribute_directions(
        directions, _metrics_attr_map(baseline["metrics"]), _metrics_attr_map(best_metrics_obj)
    )
    _attr_block = _format_attribution_report(_outcomes)
    if _attr_block:
        output.print("")
        for _line in _attr_block.splitlines():
            output.print(f"  {_line}")

    # ── Summary ──
    kept_count = sum(1 for h in history if h["status"] == "improved")
    total_improvement = best_metric - baseline_metric

    return {
        "status": "completed",
        "baseline_metric": baseline_metric,
        "best_metric": best_metric,
        "total_improvement": total_improvement,
        "improvement_pct": (total_improvement / baseline_metric * 100) if baseline_metric else 0,
        "iterations": len(history),
        "kept": kept_count,
        "baseline_rows": baseline_rows,
        "original_sql": original_sql,
        "best_sql": best_sql,
        "history": history,
    }


def _print_metrics(output, metrics: QueryMetrics) -> None:
    """Print key metrics in dim style."""
    output.print(f"    [dim]cpu={metrics.cpu_time_ms}ms wall={metrics.wall_time_ms}ms "
                 f"splits={metrics.total_splits} rows={metrics.processed_rows}[/dim]")


_VERDICT = {
    "improved": "kept — new best",
    "worse": "reverted — slower than current best",
    "semantic_drift": "reverted — row results diverged from baseline",
    "lint_failed": "reverted — failed lint",
    "exec_failed": "reverted — execution error",
    "no_sql": "skipped — no SQL block in AI reply",
}


def _iteration_diff(base_sql: str, candidate_sql: str) -> str:
    """Unified diff scoped to a single iteration's change."""
    import difflib
    diff = difflib.unified_diff(
        base_sql.splitlines(keepends=True),
        candidate_sql.splitlines(keepends=True),
        fromfile="base",
        tofile="candidate",
        n=2,
    )
    return "".join(diff).rstrip()


def _generate_report(result: dict, metric_key: str, model: str, verify_runs: int) -> str:
    """Generate a markdown report — iteration-centric, single Best SQL block."""
    from datetime import datetime

    lines = [
        "# Trino Query Optimization Report",
        "",
        f"**Date:** {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        f"**Model:** {model}",
        f"**Metric:** {metric_key} (lower is better)",
        f"**Verify runs:** {verify_runs} (median)",
        f"**Result validation:** full row-level equivalence check",
        "",
        "## Summary",
        "",
        "| Metric | Value |",
        "|--------|-------|",
        f"| Baseline | {result['baseline_metric']:.1f} |",
        f"| Best | {result['best_metric']:.1f} |",
        f"| Improvement | {result['total_improvement']:+.1f} ({result['improvement_pct']:+.1f}%) |",
        f"| Iterations | {result['iterations']} ({result['kept']} kept) |",
        f"| Row count | {result['baseline_rows']} (preserved) |",
        "",
        "## Iteration history",
        "",
        "| # | Status | Metric | Delta |",
        "|---|--------|--------|-------|",
    ]
    for h in result["history"]:
        lines.append(
            f"| {h['iteration']} | {h['status']} | {h['metric']:.1f} | {h['delta']:+.1f} |"
        )

    lines.append("")
    lines.append("## Iterations")
    lines.append("")
    for h in result["history"]:
        lines.append(f"### Iteration {h['iteration']} — {h['status']}")
        lines.append("")
        lines.append(f"**Hypothesis:** {h['hypothesis']}")
        lines.append("")
        lines.append(
            f"**Metric:** {h['metric']:.1f} (delta {h['delta']:+.1f}) — "
            f"{_VERDICT.get(h['status'], h['status'])}"
        )
        lines.append("")
        candidate_sql = h.get("candidate_sql")
        base_sql = h.get("base_sql")
        if candidate_sql and base_sql:
            diff_text = _iteration_diff(base_sql, candidate_sql)
            if diff_text:
                lines.append("```diff")
                lines.append(diff_text)
                lines.append("```")
            else:
                lines.append("_(candidate SQL identical to current best — no diff)_")
            lines.append("")

    lines.append("## Best SQL")
    lines.append("")
    if result["best_sql"] == result["original_sql"]:
        lines.append("_No improvement kept. Original SQL is the best so far for this metric._")
        lines.append("")
        lines.append("```sql")
        lines.append(result["original_sql"])
        lines.append("```")
    else:
        lines.append("```sql")
        lines.append(result["best_sql"])
        lines.append("```")

    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Entry points
# ---------------------------------------------------------------------------

def run_trino_research(
    provider,
    cfg: dict,
    model: str,
    reasoning: str,
    output,
    build_prompt: Callable[..., str],
    *,
    # Non-interactive params (used when called with --flags from chat.py)
    sql_file: Optional[str] = None,
    sql_text: Optional[str] = None,
    metric: Optional[str] = None,
    iterations: Optional[int] = None,
    runs: Optional[int] = None,
    safe_limit: Optional[int] = None,
    query_timeout: Optional[int] = None,
    long_query_opt_in: bool = True,
    long_query_threshold_s: Optional[int] = None,
    max_fallbacks: Optional[int] = None,
    diagnose_only: bool = False,
) -> None:
    """Entry point for /trino-research command.

    Supports both interactive mode (no kwargs) and non-interactive mode
    (all params passed in via kwargs).
    """
    METRICS = ["cpu_time_ms", "wall_time_ms", "physical_input_bytes", "processed_rows", "total_splits", "peak_memory_bytes"]

    output.print("\n  [yellow]== Trino Query Optimization (v2) ==[/yellow]")

    # ── Get SQL ──
    if sql_file:
        sql = Path(sql_file).read_text().strip()
        output.progress(f"  SQL from file: {sql_file}")
    elif sql_text:
        sql = sql_text.strip()
    else:
        # Interactive: paste mode
        from genie.input import _read_paste_mode
        output.print("  [cyan]Paste SQL (Ctrl-D to finish):[/cyan]")
        sql = _read_paste_mode()

    if not sql:
        output.error("Empty SQL.")
        return

    from genie.skills.mcp_trino.write_analysis import classify_write_operation, run_write_analysis_only

    if classify_write_operation(sql) is not None:
        run_write_analysis_only(
            provider, cfg, model, reasoning, sql, output, build_prompt,
            sql_source=sql_file or ("sql_text" if sql_text else "stdin"),
            route="direct",
            safe_limit=safe_limit,
        )
        return

    output.print(f"  [dim]SQL: {sql[:80]}...[/dim]\n")

    # ── Get metric ──
    if not metric:
        from genie.input import _read_input
        output.print("  [yellow]Metric to minimize:[/yellow]")
        for i, m in enumerate(METRICS, 1):
            output.print(f"    [cyan]{i}[/cyan]. {m}")
        try:
            choice = _read_input("  Choose [1]: ").strip() or "1"
            idx = int(choice) - 1
            metric = METRICS[idx] if 0 <= idx < len(METRICS) else "cpu_time_ms"
        except (ValueError, EOFError, KeyboardInterrupt):
            metric = "cpu_time_ms"

    if metric not in METRICS:
        output.error(f"Unknown metric: {metric}. Use one of: {METRICS}")
        return

    # ── Get iterations ──
    if iterations is None:
        from genie.input import _read_input
        try:
            iter_str = _read_input("  Max iterations [5]: ").strip() or "5"
            iterations = max(1, int(iter_str))
        except (ValueError, EOFError, KeyboardInterrupt):
            iterations = 5

    # ── Get verify runs ──
    if runs is None:
        from genie.input import _read_input
        try:
            runs_str = _read_input("  Verify runs per candidate [3]: ").strip() or "3"
            runs = max(1, int(runs_str))
        except (ValueError, EOFError, KeyboardInterrupt):
            runs = 3

    output.progress(f"  Metric:     {metric} (lower is better)")
    output.progress(f"  Iterations: {iterations}")
    output.progress(f"  Verify:     median of {runs} runs")
    output.print("")

    # ── EXPLAIN (FORMAT JSON) runner — zero query cost, feeds plan diagnosis ──
    def _direct_explain_runner(s: str) -> Optional[str]:
        try:
            _, _, rows = _execute_sql(f"EXPLAIN (FORMAT JSON) {s}", capture_rows=True)
        except Exception:
            return None
        if not rows:
            return None
        first = rows[0]
        cell = first[0] if isinstance(first, (list, tuple)) and first else first
        return cell if isinstance(cell, str) else None

    # ── Run ──
    result = _run_optimization_loop(
        provider=provider,
        model=model,
        reasoning=reasoning,
        original_sql=sql,
        metric_key=metric,
        max_iterations=iterations,
        verify_runs=runs,
        output=output,
        build_prompt=build_prompt,
        long_query_opt_in=long_query_opt_in,
        long_query_threshold_s=long_query_threshold_s,
        max_fallbacks=max_fallbacks,
        explain_runner=_direct_explain_runner,
        diagnose_only=diagnose_only,
    )

    # ── Print summary ──
    output.print("")
    if result["status"] == "failed":
        output.error(f"  Run failed: {result.get('error', 'unknown')}")
        return
    if result["status"] == "diagnosed":
        # Directed report (gate-trip fallback or --diagnose-only).
        from datetime import datetime
        report_md = result.get("report_markdown") or ""
        report_dir = Path.cwd() / "report"
        report_dir.mkdir(parents=True, exist_ok=True)
        report_name = f"trino-research-diagnose-{datetime.now().strftime('%Y%m%d-%H%M%S')}.md"
        report_path = report_dir / report_name
        try:
            report_path.write_text(report_md)
            output.progress(f"\n  Directed report saved: {report_path}")
        except Exception as e:
            output.error(f"  Failed to save directed report: {e}")
        return
    if result["status"] == "no_data":
        # Static-analysis-only report — save it and exit early.
        from datetime import datetime
        report_md = result.get("report_markdown") or ""
        report_dir = Path.cwd() / "report"
        report_dir.mkdir(parents=True, exist_ok=True)
        report_name = f"trino-research-nodata-{datetime.now().strftime('%Y%m%d-%H%M%S')}.md"
        report_path = report_dir / report_name
        try:
            report_path.write_text(report_md)
            output.progress(f"\n  Static report saved: {report_path}")
        except Exception as e:
            output.error(f"  Failed to save report: {e}")
        return

    output.print("  [yellow]══ Summary ══[/yellow]")
    output.print(f"  Baseline:    {result['baseline_metric']:.1f}")
    output.print(f"  Best:        {result['best_metric']:.1f}")
    output.print(f"  Improvement: {result['total_improvement']:+.1f} ({result['improvement_pct']:+.1f}%)")
    output.print(f"  Iterations:  {result['iterations']} ({result['kept']} kept)")
    output.print(f"  Row count:   {result['baseline_rows']} (preserved)")
    output.print("")

    # Iteration history
    for h in result["history"]:
        icon = "+" if h["status"] == "improved" else "-" if h["status"] == "worse" else "!"
        output.print(f"    [{icon}] iter {h['iteration']}: {h['status']:<15s} "
                     f"metric={h['metric']:.1f} delta={h['delta']:+.1f}")

    # Final SQL — full body lives in the report; terminal stays scannable.
    if result["best_sql"] == result["original_sql"]:
        output.print("\n  [dim]No improvement found — original SQL unchanged.[/dim]")

    # Generate and save report
    report = _generate_report(result, metric, model, runs)
    from datetime import datetime
    report_name = f"trino-research-{datetime.now().strftime('%Y%m%d-%H%M%S')}.md"
    report_dir = Path.cwd() / "report"
    report_dir.mkdir(parents=True, exist_ok=True)
    report_path = report_dir / report_name
    try:
        report_path.write_text(report)
        output.progress(f"\n  Report saved: {report_path}")
    except Exception as e:
        output.error(f"  Failed to save report: {e}")
