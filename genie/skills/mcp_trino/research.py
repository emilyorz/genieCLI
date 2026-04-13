"""mcp_trino research — Autoresearch query enhancement via MCP Trino server.

Uses the MCP client to execute queries and collect metrics, then runs
an AI-driven optimization loop with 5 iterations. Outputs a fixed-format
report that is identical in structure every run.

Architecture:
- MCP client sends tools/call to the Trino MCP server for execution
- Metrics are collected from the MCP response (or timed locally)
- AI proposes SQL rewrites; each is verified for correctness + performance
- Report uses a fixed template (see REPORT_TEMPLATE)
"""
from __future__ import annotations

import json
import re
import statistics
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Optional

from .client import McpClient, McpConfig, load_mcp_config


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class RunMetrics:
    """Metrics from a single query execution."""
    query_time_ms: float = 0.0
    cpu_time_ms: float = 0.0
    wall_time_ms: float = 0.0
    peak_memory_bytes: int = 0
    physical_input_bytes: int = 0
    processed_rows: int = 0
    total_splits: int = 0

    def summary(self) -> str:
        return (f"query={self.query_time_ms:.0f}ms cpu={self.cpu_time_ms:.0f}ms "
                f"wall={self.wall_time_ms:.0f}ms rows={self.processed_rows}")


@dataclass
class MeasureResult:
    """Aggregated result from multiple runs."""
    median_metric: float
    samples: list[float]
    row_count: int
    rows: list  # actual result rows for equivalence check
    columns: list[str]
    metrics: RunMetrics


@dataclass
class IterationRecord:
    """Record of a single optimization iteration."""
    iteration: int
    status: str  # improved | worse | lint_failed | exec_failed | semantic_drift | no_sql
    metric_value: float
    delta: float
    hypothesis: str
    sql: str = ""


@dataclass
class EnhancementReport:
    """Complete report from an enhancement run."""
    timestamp: str
    original_sql: str
    original_result_sample: list[dict]
    original_columns: list[str]
    original_row_count: int
    original_metrics: RunMetrics
    enhanced_sql: str
    enhanced_result_sample: list[dict]
    enhanced_columns: list[str]
    enhanced_row_count: int
    enhanced_metrics: RunMetrics
    metric_key: str
    baseline_value: float
    best_value: float
    improvement_abs: float
    improvement_pct: float
    iterations: list[IterationRecord]
    data_consistent: bool
    data_consistency_reason: str
    mcp_server_url: str
    verify_runs: int


# ---------------------------------------------------------------------------
# MCP execution helpers
# ---------------------------------------------------------------------------

def _execute_via_mcp(client: McpClient, sql: str) -> dict:
    """Execute SQL via MCP server, return parsed result with timing."""
    t0 = time.monotonic()
    raw = client.call_tool("query", {"sql": sql})
    elapsed_ms = (time.monotonic() - t0) * 1000

    # Parse the response — MCP tools return text content
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        data = {"text": raw}

    # Extract metrics from response if available
    metrics = RunMetrics(query_time_ms=elapsed_ms)
    if isinstance(data, dict):
        if "metrics" in data and isinstance(data["metrics"], dict):
            m = data["metrics"]
            metrics.cpu_time_ms = float(m.get("cpu_time_ms", m.get("cpuTimeMillis", 0)))
            metrics.wall_time_ms = float(m.get("wall_time_ms", m.get("wallTimeMillis", 0)))
            metrics.peak_memory_bytes = int(m.get("peak_memory_bytes", m.get("peakMemoryBytes", 0)))
            metrics.physical_input_bytes = int(m.get("physical_input_bytes", m.get("physicalInputBytes", 0)))
            metrics.processed_rows = int(m.get("processed_rows", m.get("processedRows", 0)))
            metrics.total_splits = int(m.get("total_splits", m.get("totalSplits", 0)))
        if "duration_ms" in data:
            metrics.query_time_ms = float(data["duration_ms"])

    # Extract rows and columns
    rows = data.get("rows", []) if isinstance(data, dict) else []
    columns = data.get("columns", []) if isinstance(data, dict) else []
    error = data.get("error") if isinstance(data, dict) else None

    return {
        "rows": rows,
        "columns": columns,
        "row_count": len(rows),
        "metrics": metrics,
        "error": error,
        "raw": raw,
    }


def _measure_mcp(client: McpClient, sql: str, metric_key: str,
                  runs: int, capture_rows: bool = False) -> MeasureResult:
    """Run SQL `runs` times via MCP, return median metric + all data."""
    samples = []
    all_metrics = []
    last_rows = []
    last_columns = []
    row_count = 0

    for i in range(runs):
        result = _execute_via_mcp(client, sql)
        if result["error"]:
            raise RuntimeError(f"MCP query failed: {result['error']}")

        metrics = result["metrics"]
        value = getattr(metrics, metric_key, metrics.query_time_ms)
        samples.append(float(value))
        all_metrics.append(metrics)
        row_count = result["row_count"]

        if capture_rows and i == runs - 1:
            last_rows = result["rows"]
            last_columns = result["columns"]

    median_val = statistics.median(samples)
    median_idx = min(range(len(samples)), key=lambda i: abs(samples[i] - median_val))

    return MeasureResult(
        median_metric=median_val,
        samples=samples,
        row_count=row_count,
        rows=last_rows,
        columns=last_columns,
        metrics=all_metrics[median_idx],
    )


def _results_equivalent(rows_a: list, rows_b: list) -> tuple[bool, str]:
    """Check if two MCP result sets are equivalent."""
    if len(rows_a) != len(rows_b):
        return False, f"row count differs: {len(rows_a)} vs {len(rows_b)}"

    if not rows_a:
        return True, "both empty"

    # Compare as sorted JSON for order-independent comparison
    def normalize(row):
        if isinstance(row, dict):
            return json.dumps(row, sort_keys=True, default=str)
        return json.dumps(row, default=str)

    set_a = sorted(normalize(r) for r in rows_a)
    set_b = sorted(normalize(r) for r in rows_b)

    mismatches = sum(1 for a, b in zip(set_a, set_b) if a != b)
    if mismatches > 0:
        return False, f"{mismatches} row(s) differ"
    if len(set_a) != len(set_b):
        return False, f"row count after normalize: {len(set_a)} vs {len(set_b)}"

    return True, "exact match"


def _extract_sql_from_reply(reply: str) -> Optional[str]:
    """Extract SQL from AI reply (```sql block)."""
    sql_blocks = re.findall(r"```sql\s*\n(.*?)```", reply, re.DOTALL | re.IGNORECASE)
    if sql_blocks:
        return sql_blocks[-1].strip().rstrip(";")

    generic_blocks = re.findall(r"```\s*\n(.*?)```", reply, re.DOTALL)
    for block in reversed(generic_blocks):
        block = block.strip()
        if any(kw in block.upper() for kw in ["SELECT", "WITH", "INSERT", "UPDATE", "DELETE"]):
            return block.rstrip(";")

    return None


# ---------------------------------------------------------------------------
# Report generation (fixed format)
# ---------------------------------------------------------------------------

def generate_report(report: EnhancementReport) -> str:
    """Generate a fixed-format markdown report.

    This template is ALWAYS the same structure — sections, headers, and table
    columns never change between runs. Only the data values differ.
    """
    lines = []

    # ── Header ──
    lines.append("# Trino Query Enhancement Report")
    lines.append("")
    lines.append("## Meta")
    lines.append("")
    lines.append("| Field | Value |")
    lines.append("|-------|-------|")
    lines.append(f"| Timestamp | {report.timestamp} |")
    lines.append(f"| MCP Server | {report.mcp_server_url} |")
    lines.append(f"| Target Metric | {report.metric_key} (lower is better) |")
    lines.append(f"| Verify Runs | {report.verify_runs} (median) |")
    lines.append(f"| Iterations | {len(report.iterations)} |")
    lines.append("")

    # ── Performance Comparison ──
    lines.append("## Performance Comparison")
    lines.append("")
    lines.append("| Metric | Original | Enhanced | Delta | Change % |")
    lines.append("|--------|----------|----------|-------|----------|")

    for attr in ["query_time_ms", "cpu_time_ms", "wall_time_ms"]:
        orig = getattr(report.original_metrics, attr, 0)
        enh = getattr(report.enhanced_metrics, attr, 0)
        delta = enh - orig
        pct = (delta / orig * 100) if orig else 0
        lines.append(f"| {attr} | {orig:.1f} | {enh:.1f} | {delta:+.1f} | {pct:+.1f}% |")

    for attr in ["processed_rows", "total_splits", "peak_memory_bytes", "physical_input_bytes"]:
        orig = getattr(report.original_metrics, attr, 0)
        enh = getattr(report.enhanced_metrics, attr, 0)
        delta = enh - orig
        pct = (delta / orig * 100) if orig else 0
        lines.append(f"| {attr} | {orig} | {enh} | {delta:+} | {pct:+.1f}% |")

    lines.append("")

    # ── Summary ──
    lines.append("## Summary")
    lines.append("")
    lines.append("| Field | Value |")
    lines.append("|-------|-------|")
    lines.append(f"| Baseline ({report.metric_key}) | {report.baseline_value:.1f} |")
    lines.append(f"| Best ({report.metric_key}) | {report.best_value:.1f} |")
    lines.append(f"| Improvement | {report.improvement_abs:+.1f} ({report.improvement_pct:+.1f}%) |")
    lines.append(f"| Original Row Count | {report.original_row_count} |")
    lines.append(f"| Enhanced Row Count | {report.enhanced_row_count} |")
    lines.append(f"| Data Consistent | {'YES' if report.data_consistent else 'NO'} |")
    lines.append(f"| Consistency Detail | {report.data_consistency_reason} |")
    lines.append("")

    # ── Iteration History ──
    lines.append("## Iteration History")
    lines.append("")
    lines.append("| Round | Status | Metric Value | Delta | Hypothesis |")
    lines.append("|-------|--------|-------------|-------|------------|")

    for it in report.iterations:
        lines.append(
            f"| {it.iteration} | {it.status} | {it.metric_value:.1f} | "
            f"{it.delta:+.1f} | {it.hypothesis[:60]} |"
        )

    lines.append("")

    # ── Original SQL ──
    lines.append("## Original SQL")
    lines.append("")
    lines.append("```sql")
    lines.append(report.original_sql)
    lines.append("```")
    lines.append("")

    # ── Original Result (sample) ──
    lines.append("## Original Result (sample)")
    lines.append("")
    if report.original_columns:
        lines.append("| " + " | ".join(report.original_columns) + " |")
        lines.append("| " + " | ".join("---" for _ in report.original_columns) + " |")
        for row in report.original_result_sample[:10]:
            if isinstance(row, dict):
                vals = [str(row.get(c, "")) for c in report.original_columns]
            else:
                vals = [str(v) for v in row]
            lines.append("| " + " | ".join(vals) + " |")
    else:
        lines.append("_(no data)_")
    lines.append("")

    # ── Enhanced SQL ──
    lines.append("## Enhanced SQL")
    lines.append("")
    if report.enhanced_sql != report.original_sql:
        lines.append("```sql")
        lines.append(report.enhanced_sql)
        lines.append("```")
    else:
        lines.append("_(no improvement found — original SQL unchanged)_")
    lines.append("")

    # ── Enhanced Result (sample) ──
    lines.append("## Enhanced Result (sample)")
    lines.append("")
    if report.enhanced_columns:
        lines.append("| " + " | ".join(report.enhanced_columns) + " |")
        lines.append("| " + " | ".join("---" for _ in report.enhanced_columns) + " |")
        for row in report.enhanced_result_sample[:10]:
            if isinstance(row, dict):
                vals = [str(row.get(c, "")) for c in report.enhanced_columns]
            else:
                vals = [str(v) for v in row]
            lines.append("| " + " | ".join(vals) + " |")
    else:
        lines.append("_(no data)_")
    lines.append("")

    # ── Footer ──
    lines.append("---")
    lines.append(f"_Generated by genieCLI mcp_trino research at {report.timestamp}_")
    lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Enhancement loop
# ---------------------------------------------------------------------------

def run_mcp_enhancement(
    client: McpClient,
    sql: str,
    metric_key: str = "query_time_ms",
    max_iterations: int = 5,
    verify_runs: int = 3,
    provider=None,
    model: str = "",
    reasoning: str = "disable",
    output=None,
    build_prompt: Callable[..., str] | None = None,
) -> EnhancementReport:
    """Run the MCP-based query enhancement loop.

    Args:
        client: MCP client connected to the Trino server
        sql: Original SQL to enhance
        metric_key: Metric to optimize (default: query_time_ms)
        max_iterations: Number of enhancement rounds (default: 5)
        verify_runs: Runs per candidate for median measurement (default: 3)
        provider: LLM provider for generating SQL rewrites
        model: Model name
        reasoning: Reasoning mode
        output: OutputSink for progress messages
        build_prompt: Prompt builder function

    Returns:
        EnhancementReport with all results
    """
    from genie.core.provider import CompletionRequest
    from genie.session.manager import new_msg, new_session

    if output:
        output.print("\n  [yellow]== MCP Trino Query Enhancement ==[/yellow]")
        output.progress(f"  Server: {client.config.url}")
        output.progress(f"  Metric: {metric_key} | Iterations: {max_iterations} | Verify: {verify_runs} runs")

    # ── Baseline ──
    if output:
        output.progress("  Measuring baseline...")

    baseline = _measure_mcp(client, sql, metric_key, verify_runs, capture_rows=True)

    if output:
        output.progress(f"  Baseline {metric_key}: {baseline.median_metric:.1f} (median of {verify_runs} runs)")
        output.progress(f"  Baseline rows: {baseline.row_count}")
        output.print(f"    [dim]{baseline.metrics.summary()}[/dim]")

    # ── Session setup ──
    skill_prompt = build_prompt(True, model) if build_prompt else ""
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

    best_sql = sql
    best_metric = baseline.median_metric
    best_measure = baseline
    iterations: list[IterationRecord] = []

    # ── Iteration loop ──
    for iteration in range(1, max_iterations + 1):
        if output:
            output.print("")
            output.progress(f"  ── Iteration {iteration}/{max_iterations} ──")

        last_str = "N/A (first iteration)"
        if iterations:
            last = iterations[-1]
            last_str = f"{last.status} (metric={last.metric_value:.1f}, delta={last.delta:+.1f})"

        context = (
            f"[Trino Query Enhancement — Iteration {iteration}]\n"
            f"Target metric: {metric_key} (lower is better)\n"
            f"Baseline: {baseline.median_metric:.1f}\n"
            f"Current best: {best_metric:.1f}\n"
            f"Last iteration: {last_str}\n\n"
            f"Current SQL:\n```sql\n{best_sql}\n```\n\n"
            f"Return the COMPLETE optimized SQL in a ```sql block. ONE change only. "
            f"Do NOT include a trailing semicolon."
        )

        # Keep history lean
        sys_msgs = [m for m in session["history"] if m["role"] == "system"]
        non_sys = [m for m in session["history"] if m["role"] != "system"]
        session["history"] = sys_msgs + non_sys[-4:]
        session["history"].append(new_msg("user", context))

        # Get AI response
        if output:
            output.progress("  AI thinking...")
        req = CompletionRequest(messages=session["history"], model=model, reasoning=reasoning)
        reply = provider.complete_text(req)

        if not reply:
            if output:
                output.error("  Empty AI response — stopping.")
            break

        session["history"].append(new_msg("assistant", reply))

        # Extract SQL
        candidate_sql = _extract_sql_from_reply(reply)
        if not candidate_sql:
            if output:
                output.progress("  [SKIP] No SQL found in AI response.")
            iterations.append(IterationRecord(
                iteration=iteration, status="no_sql",
                metric_value=best_metric, delta=0.0,
                hypothesis="no SQL extracted",
            ))
            continue

        # Extract hypothesis
        hypothesis = "?"
        for line in reply.split("\n"):
            line = line.strip()
            if line and not line.startswith("```") and not line.startswith("|"):
                hypothesis = line[:80]
                break

        if output:
            output.progress(f"  [Hypothesis] {hypothesis}")

        # Execute and measure candidate
        try:
            candidate = _measure_mcp(client, candidate_sql, metric_key, verify_runs, capture_rows=True)
        except Exception as exc:
            if output:
                output.progress(f"  [REVERT] Execution failed: {exc}")
            iterations.append(IterationRecord(
                iteration=iteration, status="exec_failed",
                metric_value=best_metric, delta=0.0,
                hypothesis=hypothesis, sql=candidate_sql,
            ))
            continue

        candidate_metric = candidate.median_metric
        delta = candidate_metric - best_metric

        # Check result equivalence
        equiv, equiv_reason = _results_equivalent(baseline.rows, candidate.rows)
        if not equiv:
            if output:
                output.progress(f"  [REVERT] Result mismatch: {equiv_reason}")
            session["history"].append(new_msg(
                "user",
                f"Query results differ from baseline: {equiv_reason}. "
                f"Change REVERTED. Try a different approach that preserves the exact same result set."
            ))
            iterations.append(IterationRecord(
                iteration=iteration, status="semantic_drift",
                metric_value=candidate_metric, delta=delta,
                hypothesis=hypothesis, sql=candidate_sql,
            ))
            continue

        # Decision: keep or revert
        improved = candidate_metric < best_metric
        if improved:
            best_sql = candidate_sql
            best_metric = candidate_metric
            best_measure = candidate
            status = "improved"
        else:
            status = "worse"

        if output:
            icon = "+" if improved else "-"
            output.progress(
                f"  [{icon}] {'KEPT' if improved else 'REVERTED'} | "
                f"{metric_key}={candidate_metric:.1f} (delta={delta:+.1f})"
            )
            output.print(f"    [dim]{candidate.metrics.summary()}[/dim]")

        session["history"].append(new_msg(
            "user",
            f"[Iteration {iteration} result]\n"
            f"Status: {'KEPT' if improved else 'REVERTED'}\n"
            f"Metric ({metric_key}, median of {verify_runs} runs): {candidate_metric:.1f}\n"
            f"Delta vs current best: {delta:+.1f}\n"
            f"Row count: {candidate.row_count} (baseline: {baseline.row_count})\n"
            f"{'Change KEPT — this is now the current best.' if improved else 'Change REVERTED — current best unchanged.'}"
        ))

        iterations.append(IterationRecord(
            iteration=iteration, status=status,
            metric_value=candidate_metric, delta=delta,
            hypothesis=hypothesis, sql=candidate_sql,
        ))

    # ── Build report ──
    improvement_abs = best_metric - baseline.median_metric
    improvement_pct = (improvement_abs / baseline.median_metric * 100) if baseline.median_metric else 0

    # Final equivalence check
    final_equiv, final_reason = _results_equivalent(baseline.rows, best_measure.rows)

    report = EnhancementReport(
        timestamp=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        original_sql=sql,
        original_result_sample=baseline.rows[:10],
        original_columns=baseline.columns,
        original_row_count=baseline.row_count,
        original_metrics=baseline.metrics,
        enhanced_sql=best_sql,
        enhanced_result_sample=best_measure.rows[:10],
        enhanced_columns=best_measure.columns,
        enhanced_row_count=best_measure.row_count,
        enhanced_metrics=best_measure.metrics,
        metric_key=metric_key,
        baseline_value=baseline.median_metric,
        best_value=best_metric,
        improvement_abs=improvement_abs,
        improvement_pct=improvement_pct,
        iterations=iterations,
        data_consistent=final_equiv,
        data_consistency_reason=final_reason,
        mcp_server_url=client.config.url,
        verify_runs=verify_runs,
    )

    # Print summary
    if output:
        output.print("")
        output.print("  [yellow]══ Enhancement Summary ══[/yellow]")
        output.print(f"  Baseline:    {baseline.median_metric:.1f}")
        output.print(f"  Best:        {best_metric:.1f}")
        output.print(f"  Improvement: {improvement_abs:+.1f} ({improvement_pct:+.1f}%)")
        output.print(f"  Data check:  {'PASS' if final_equiv else 'FAIL'} ({final_reason})")

    return report
