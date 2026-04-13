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
import time
from pathlib import Path
from typing import Callable, Optional

from genie.skills.trino_query.connection import get_active_profile
from genie.skills.trino_query import QueryMetrics, _extract_metrics


# ---------------------------------------------------------------------------
# Measurement helpers (run in-process, no subprocess/verify.py needed)
# ---------------------------------------------------------------------------

def _execute_sql(sql: str, capture_rows: bool = False) -> tuple[int, QueryMetrics, list]:
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


def _measure(sql: str, metric_key: str, runs: int, capture_rows: bool = False) -> dict:
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
        rc, m, rows = _execute_sql(sql, capture_rows=capture)
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


def _extract_sql_from_reply(reply: str) -> Optional[str]:
    """Extract SQL from AI reply.

    Tries in order:
    1. Fenced ```sql ... ``` block
    2. Fenced ``` ... ``` block that looks like SQL
    3. None (no SQL found)
    """
    # Try ```sql block first
    sql_blocks = re.findall(r"```sql\s*\n(.*?)```", reply, re.DOTALL | re.IGNORECASE)
    if sql_blocks:
        return sql_blocks[-1].strip().rstrip(";")

    # Try generic fenced block
    generic_blocks = re.findall(r"```\s*\n(.*?)```", reply, re.DOTALL)
    for block in reversed(generic_blocks):
        block = block.strip()
        if any(kw in block.upper() for kw in ["SELECT", "WITH", "INSERT", "UPDATE", "DELETE"]):
            return block.rstrip(";")

    return None


# ---------------------------------------------------------------------------
# Core iteration loop (no RunManager / file_patch / git dependency)
# ---------------------------------------------------------------------------

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
) -> dict:
    """Run the optimization loop. Returns summary dict."""
    from genie.core.provider import CompletionRequest
    from genie.session.manager import new_msg, new_session

    # ── Baseline ──
    output.progress("  Measuring baseline...")
    try:
        baseline = _measure(original_sql, metric_key, verify_runs, capture_rows=True)
    except Exception as e:
        output.error(f"  Baseline measurement failed: {e}")
        return {"status": "failed", "error": str(e)}

    baseline_metric = baseline["median"]
    baseline_rows = baseline["row_count"]
    baseline_data = baseline["rows"]

    output.progress(f"  Baseline {metric_key}: {baseline_metric} (median of {verify_runs} runs)")
    output.progress(f"  Baseline row count: {baseline_rows}")
    _print_metrics(output, baseline["metrics"])

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
        f"- Trino best practices: partition filters, named columns, CTEs over subqueries, "
        f"APPROX_DISTINCT over COUNT(DISTINCT), COALESCE instead of NVL\n\n"
        f"{skill_prompt}"
    )
    session = new_session(sys_prompt)

    best_sql = original_sql
    best_metric = baseline_metric
    history = []

    # ── Iteration loop ──
    for iteration in range(1, max_iterations + 1):
        output.print("")
        output.progress(f"  ── Iteration {iteration}/{max_iterations} ──")

        # Build context for AI
        last_str = "N/A (first iteration)"
        if history:
            last = history[-1]
            last_str = f"{last['status']} (metric={last['metric']:.1f}, delta={last['delta']:+.1f})"

        context = (
            f"[Trino Query Optimization — Iteration {iteration}]\n"
            f"Target metric: {metric_key} (lower is better)\n"
            f"Baseline: {baseline_metric}\n"
            f"Current best: {best_metric}\n"
            f"Last iteration: {last_str}\n\n"
            f"Current SQL:\n```sql\n{best_sql}\n```\n\n"
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
        candidate_sql = _extract_sql_from_reply(reply)
        if not candidate_sql:
            output.progress("  [SKIP] No SQL found in AI response.")
            session["history"].append(new_msg("user", "I couldn't find a SQL block in your response. Return the COMPLETE SQL in a ```sql block."))
            # One retry
            req2 = CompletionRequest(messages=session["history"], model=model, reasoning=reasoning)
            reply2 = provider.complete_text(req2)
            if reply2:
                session["history"].append(new_msg("assistant", reply2))
                candidate_sql = _extract_sql_from_reply(reply2)
            if not candidate_sql:
                output.progress("  [SKIP] Still no SQL — skipping iteration.")
                history.append({
                    "iteration": iteration, "status": "no_sql",
                    "metric": best_metric, "delta": 0.0, "hypothesis": "no SQL extracted",
                })
                continue

        # Extract hypothesis from AI reply (skip code fences, get first meaningful line)
        hypothesis = "?"
        if reply:
            for line in reply.split("\n"):
                line = line.strip()
                if line and not line.startswith("```") and not line.startswith("|"):
                    hypothesis = line[:80]
                    break
        output.progress(f"  [Hypothesis] {hypothesis}")

        # Guard 1: Lint check
        lint_ok, lint_msg = _lint_sql(candidate_sql)
        if not lint_ok:
            output.progress(f"  [REVERT] Lint failed: {lint_msg}")
            session["history"].append(new_msg("user", f"SQL failed lint: {lint_msg}. Change REVERTED."))
            history.append({
                "iteration": iteration, "status": "lint_failed",
                "metric": best_metric, "delta": 0.0, "hypothesis": hypothesis,
            })
            continue

        # Guard 2: Execute and measure
        try:
            candidate = _measure(candidate_sql, metric_key, verify_runs, capture_rows=True)
        except Exception as e:
            output.progress(f"  [REVERT] Execution failed: {e}")
            session["history"].append(new_msg("user", f"SQL execution failed: {e}. Change REVERTED."))
            history.append({
                "iteration": iteration, "status": "exec_failed",
                "metric": best_metric, "delta": 0.0, "hypothesis": hypothesis,
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
            })
            continue

        # Decision: keep or revert
        improved = candidate_metric < best_metric
        if improved:
            best_sql = candidate_sql
            best_metric = candidate_metric
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


def _generate_report(result: dict, metric_key: str, model: str, verify_runs: int) -> str:
    """Generate a markdown report from optimization results."""
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
        f"| Metric | Value |",
        f"|--------|-------|",
        f"| Baseline | {result['baseline_metric']:.1f} |",
        f"| Best | {result['best_metric']:.1f} |",
        f"| Improvement | {result['total_improvement']:+.1f} ({result['improvement_pct']:+.1f}%) |",
        f"| Iterations | {result['iterations']} ({result['kept']} kept) |",
        f"| Row count | {result['baseline_rows']} (preserved) |",
        "",
        "## Iteration History",
        "",
        "| # | Status | Metric | Delta | Hypothesis |",
        "|---|--------|--------|-------|------------|",
    ]

    for h in result["history"]:
        lines.append(
            f"| {h['iteration']} | {h['status']} | {h['metric']:.1f} | "
            f"{h['delta']:+.1f} | {h['hypothesis'][:60]} |"
        )

    lines.append("")
    lines.append("## Original SQL")
    lines.append("")
    lines.append("```sql")
    lines.append(result["original_sql"])
    lines.append("```")
    lines.append("")

    if result["best_sql"] != result["original_sql"]:
        lines.append("## Optimized SQL")
        lines.append("")
        lines.append("```sql")
        lines.append(result["best_sql"])
        lines.append("```")
        lines.append("")

        # Side-by-side diff
        import difflib
        diff = difflib.unified_diff(
            result["original_sql"].splitlines(keepends=True),
            result["best_sql"].splitlines(keepends=True),
            fromfile="Original",
            tofile="Optimized",
        )
        diff_text = "".join(diff)
        if diff_text:
            lines.append("## Diff")
            lines.append("")
            lines.append("```diff")
            lines.append(diff_text.rstrip())
            lines.append("```")
    else:
        lines.append("## Result")
        lines.append("")
        lines.append("No improvement found. Original SQL is already optimal for this metric.")

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
) -> None:
    """Entry point for /trino-research command.

    Supports both interactive mode (no kwargs) and non-interactive mode
    (all params passed in via kwargs).
    """
    METRICS = ["cpu_time_ms", "wall_time_ms", "physical_input_bytes", "processed_rows", "total_splits"]

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
    )

    # ── Print summary ──
    output.print("")
    if result["status"] == "failed":
        output.error(f"  Run failed: {result.get('error', 'unknown')}")
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

    # Final SQL
    if result["best_sql"] != result["original_sql"]:
        output.print("\n  [yellow]Optimized SQL:[/yellow]")
        for line in result["best_sql"].split("\n"):
            output.print(f"    {line}")
    else:
        output.print("\n  [dim]No improvement found — original SQL unchanged.[/dim]")

    # Generate and save report
    report = _generate_report(result, metric, model, runs)
    from datetime import datetime
    report_name = f"trino-research-{datetime.now().strftime('%Y%m%d-%H%M%S')}.md"
    report_path = Path.cwd() / report_name
    try:
        report_path.write_text(report)
        output.progress(f"\n  Report saved: {report_path}")
    except Exception as e:
        output.error(f"  Failed to save report: {e}")
