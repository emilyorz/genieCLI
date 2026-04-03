"""trino-research — Autoresearch mode for SQL query optimization.

Wraps the autoresearch loop with Trino-specific defaults:
- SQL lives in a .sql file (AI modifies it via file_patch)
- Verify command executes the .sql on Trino and outputs a chosen metric
- AI sees the full metrics + EXPLAIN plan each iteration
- Guard: SQL must parse (lint score != F)

Usage in CLI:
  /trino-research
  > Paste your SQL
  > Choose metric to optimize (cpu_time_ms / wall_time_ms / physical_input_bytes / total_splits)
  > Set max iterations
  > Go
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Callable

from genie.core.registry import SkillRegistry

VERIFY_SCRIPT = '''#!{python_bin}
"""Auto-generated verify script for trino-research.
Runs the query multiple times and reports the median metric to reduce cache/warmup noise.
"""
import statistics
import sys
import time

sys.path.insert(0, "{genie_root}")

from genie.skills.trino_query.connection import get_active_profile
from genie.skills.trino_query import _extract_metrics

sql_path = "{sql_path}"
metric_key = "{metric_key}"
runs = {verify_runs}

with open(sql_path) as f:
    sql = f.read().strip()

if not sql:
    print("ERROR: empty SQL")
    sys.exit(1)

samples = []
all_metrics = []

try:
    import trino.dbapi
    cfg = get_active_profile()

    for i in range(runs):
        conn = cfg.connect()
        cur = conn.cursor()
        cur.execute(sql)
        try:
            cur.fetchall()
        except Exception:
            pass
        stats = getattr(cur, 'stats', {{}}) or {{}}
        metrics = _extract_metrics(stats)
        conn.close()

        value = getattr(metrics, metric_key, 0) or 0
        samples.append(float(value))
        all_metrics.append(metrics)
        print(f"sample_{{i + 1}}={{value}}")

    median_value = statistics.median(samples)
    median_idx = min(range(len(samples)), key=lambda i: abs(samples[i] - median_value))
    chosen = all_metrics[median_idx]

    print(f"METRIC={{median_value}}")
    print(f"runs={{runs}}")
    print(f"samples={','.join(str(x) for x in samples)}")
    print(f"cpu_time_ms={{chosen.cpu_time_ms}}")
    print(f"wall_time_ms={{chosen.wall_time_ms}}")
    print(f"peak_memory_bytes={{chosen.peak_memory_bytes}}")
    print(f"physical_input_bytes={{chosen.physical_input_bytes}}")
    print(f"processed_rows={{chosen.processed_rows}}")
    print(f"total_splits={{chosen.total_splits}}")
    print(median_value)
except Exception as e:
    print(f"ERROR: {{e}}")
    print(999999)
    sys.exit(1)
'''

GUARD_SCRIPT = '''#!{python_bin}
"""Auto-generated guard script for trino-research. Ensures SQL is parseable."""
import sys
sys.path.insert(0, "{genie_root}")

sql_path = "{sql_path}"
with open(sql_path) as f:
    sql = f.read().strip()

if not sql:
    print("GUARD FAIL: empty SQL")
    sys.exit(1)

try:
    from genie.skills.trino_linter.analyzer import analyze
    result = analyze(sql)
    if result.score == "F" and result.parse_error:
        print(f"GUARD FAIL: SQL parse error — {{result.parse_error}}")
        sys.exit(1)
    print(f"GUARD PASS: lint score={{result.score}}")
    sys.exit(0)
except Exception as e:
    print(f"GUARD ERROR: {{e}}")
    sys.exit(1)
'''


def setup_trino_research(
    sql: str,
    metric: str,
    max_iterations: int,
    verify_runs: int,
    output,
    build_prompt: Callable[[bool], str],
) -> dict | None:
    """Set up autoresearch for SQL optimization.

    Creates a temp workdir with:
      - query.sql (the SQL to optimize)
      - verify.py (executes SQL, outputs metric)
      - guard.py (ensures SQL parses)

    Returns dict with RunConfig params, or None on failure.
    """
    from genie.input import _read_input

    import sys
    # Find genie package root and the correct python binary
    genie_root = str(Path(__file__).parent.parent.parent.parent)
    python_bin = sys.executable

    # Create workdir
    workdir = Path(tempfile.mkdtemp(prefix="trino-research-"))

    # Init git repo (RunManager requires it)
    os.system(f"cd {workdir} && git init -q && git config user.email 'trino@research' && git config user.name 'trino-research'")

    # Write SQL file
    sql_path = workdir / "query.sql"
    sql_path.write_text(sql.strip() + "\n")

    # Write verify script
    verify_path = workdir / "verify.py"
    verify_path.write_text(VERIFY_SCRIPT.format(
        python_bin=python_bin,
        genie_root=genie_root,
        sql_path=str(sql_path),
        metric_key=metric,
        verify_runs=verify_runs,
    ))
    verify_path.chmod(0o755)

    # Write guard script
    guard_path = workdir / "guard.py"
    guard_path.write_text(GUARD_SCRIPT.format(
        python_bin=python_bin,
        genie_root=genie_root,
        sql_path=str(sql_path),
    ))
    guard_path.chmod(0o755)

    # Initial commit
    os.system(f"cd {workdir} && git add -A && git commit -q -m 'initial: original query'")

    output.progress(f"  Workdir: {workdir}")
    output.progress(f"  SQL:     {sql_path}")
    output.progress(f"  Metric:  {metric} (lower is better)")
    output.progress(f"  Verify:  median of {verify_runs} runs")
    output.progress(f"  Guard:   lint score != F")

    return {
        "goal": f"Optimize SQL query to minimize {metric}. The SQL is in query.sql. "
                f"Read it, understand what it does, then use file_patch to rewrite it. "
                f"Keep the same result set — only optimize performance. "
                f"Trino-specific: use partition filters, avoid SELECT *, prefer APPROX_DISTINCT "
                f"over COUNT(DISTINCT), rewrite correlated subqueries as JOINs/CTEs, "
                f"replace Oracle functions (NVL→COALESCE, DECODE→CASE).",
        "scope": ["query.sql"],
        "verify_command": f"python3 {verify_path}",
        "guard_command": f"python3 {guard_path}",
        "metric_direction": "lower",
        "max_iterations": max_iterations,
        "workdir": str(workdir),
        "sql_path": str(sql_path),
    }


def run_trino_research(
    provider,
    cfg: dict,
    model: str,
    reasoning: str,
    output,
    build_prompt: Callable[[bool], str],
) -> None:
    """Interactive /trino-research command."""
    from genie.input import _read_input, _read_paste_mode

    output.print("\n  [yellow]== Trino Query Autoresearch ==[/yellow]")
    output.print("  Paste your SQL, then choose a metric to optimize.\n")

    # Get SQL
    output.print("  [cyan]Paste SQL (Ctrl-D to finish):[/cyan]")
    sql = _read_paste_mode()
    if not sql.strip():
        output.error("Empty SQL.")
        return

    output.print(f"\n  [dim]SQL: {sql.strip()[:80]}...[/dim]\n")

    # Choose metric
    metrics = ["cpu_time_ms", "wall_time_ms", "physical_input_bytes", "processed_rows", "total_splits"]
    output.print("  [yellow]Metric to minimize:[/yellow]")
    for i, m in enumerate(metrics, 1):
        output.print(f"    [cyan]{i}[/cyan]. {m}")

    try:
        choice = _read_input("  Choose [1]: ").strip() or "1"
        idx = int(choice) - 1
        metric = metrics[idx] if 0 <= idx < len(metrics) else "cpu_time_ms"
    except (ValueError, EOFError, KeyboardInterrupt):
        metric = "cpu_time_ms"

    # Max iterations
    try:
        iter_str = _read_input("  Max iterations [5]: ").strip() or "5"
        max_iter = max(1, int(iter_str))
    except (ValueError, EOFError, KeyboardInterrupt):
        max_iter = 5

    # Verify runs per candidate
    try:
        runs_str = _read_input("  Verify runs per candidate [3]: ").strip() or "3"
        verify_runs = max(1, int(runs_str))
    except (ValueError, EOFError, KeyboardInterrupt):
        verify_runs = 3

    # Setup
    output.progress("\n  Setting up research environment...")
    setup = setup_trino_research(sql, metric, max_iter, verify_runs, output, build_prompt)
    if not setup:
        output.error("Setup failed.")
        return

    # Run via standard autoresearch
    from genie.core.provider import CompletionRequest
    from genie.core.context import SkillContext
    from genie.core.tool_call import normalize_result, parse_tool_call
    from genie.runtime.eval_loop import RunConfig, RunManager
    from genie.session.manager import new_msg, new_session

    run_cfg = RunConfig(
        goal=setup["goal"],
        scope=setup["scope"],
        metric_direction=setup["metric_direction"],
        verify_command=setup["verify_command"],
        guard_command=setup["guard_command"],
        max_iterations=setup["max_iterations"],
    )

    # Build system prompt with Trino-specific instructions
    skill_prompt = build_prompt(True)
    sys_prompt = (
        f"You are optimizing a Trino SQL query. The query is in `query.sql`.\n"
        f"Target metric: {metric} (lower is better).\n"
        f"Rules:\n"
        f"- Read query.sql first to understand the query\n"
        f"- Use file_patch to modify query.sql\n"
        f"- Keep the same result set — only optimize performance\n"
        f"- One change per iteration\n"
        f"- Trino best practices: partition filters, named columns, CTEs over subqueries, "
        f"APPROX_DISTINCT, COALESCE instead of NVL\n\n"
        f"{skill_prompt}"
    )
    ar_session = new_session(sys_prompt)
    workdir = setup["workdir"]
    manager = RunManager()

    output.progress("  Measuring baseline...")
    state = manager.start(run_cfg, workdir)

    if state.status == "failed":
        output.error("  Baseline measurement failed. Check Trino connection.")
        # Show what verify.py outputted
        import subprocess
        try:
            r = subprocess.run(setup["verify_command"].split(), capture_output=True, text=True, cwd=workdir, timeout=15)
            output.print(f"  [dim]verify output: {r.stdout.strip()}[/dim]")
            if r.stderr:
                output.print(f"  [dim]verify stderr: {r.stderr.strip()[:200]}[/dim]")
        except Exception:
            pass
        return

    output.progress(f"  Baseline {metric}: {state.baseline_metric}")
    output.print("")

    # Show original SQL + baseline metrics
    import subprocess
    try:
        r = subprocess.run(setup["verify_command"].split(), capture_output=True, text=True, cwd=workdir, timeout=15)
        for line in r.stdout.strip().split("\n"):
            if "=" in line and not line.startswith("METRIC"):
                output.print(f"  [dim]{line}[/dim]")
    except Exception:
        pass

    output.print("")

    # Iteration loop (same as autoresearch_cli but with Trino context)
    def _is_tool_failure(result: str) -> bool:
        return result.startswith(("ERROR", "Validation error", "Wrong args", "Tool error",
                                  "Unknown tool", "Patch failed", "Error applying patch"))

    ctx = SkillContext(provider=provider, output=output, config=cfg)

    try:
        while manager.should_continue(state):
            iteration = state.iteration + 1
            output.progress(f"  ── Iteration {iteration}/{max_iter} ──")

            # Read current SQL
            current_sql = Path(setup["sql_path"]).read_text().strip()

            last = state.history[-1] if state.history else None
            if last:
                delta_str = f"{last.delta:+.4f}" if last.delta is not None else "N/A"
                last_str = f"{last.status} (metric={last.metric}, delta={delta_str})"
            else:
                last_str = "N/A (first iteration)"

            context = (
                f"[Trino Query Optimization — Iteration {iteration}]\n"
                f"Target metric: {metric} (lower is better)\n"
                f"Baseline: {state.baseline_metric}\n"
                f"Current best: {state.current_best}\n"
                f"Last iteration: {last_str}\n\n"
                f"Current query.sql:\n```sql\n{current_sql}\n```\n\n"
                f"Make ONE focused change to reduce {metric}. Use file_patch on query.sql."
            )
            ar_session["history"].append(new_msg("user", context))

            output.progress("  AI thinking...")
            req = CompletionRequest(messages=ar_session["history"], model=model, reasoning=reasoning)
            reply = provider.complete_text(req)
            if not reply:
                output.error("  Empty AI response — stopping.")
                break

            ar_session["history"].append(new_msg("assistant", reply))
            tool_call = parse_tool_call(reply)

            if not tool_call:
                reminder = (
                    "Please modify query.sql using file_patch. "
                    'Format: {"memory": "what I changed", "tool": "file_patch", "args": {"path": "query.sql", "patch": "..."}}'
                )
                ar_session["history"].append(new_msg("user", reminder))
                req2 = CompletionRequest(messages=ar_session["history"], model=model, reasoning=reasoning)
                reply2 = provider.complete_text(req2)
                if reply2:
                    ar_session["history"].append(new_msg("assistant", reply2))
                    tool_call = parse_tool_call(reply2)

            if not tool_call:
                output.progress("  [WARN] No tool call — skipping.")
                continue

            tool_name = tool_call.get("tool", "?")
            hypothesis = tool_call.get("memory", "")
            tool_args = tool_call.get("args") or {}
            output.progress(f"  [Tool] {tool_name} | {hypothesis[:60]}")

            patch_result = normalize_result(SkillRegistry.run_tool(tool_name, tool_args, ctx))

            if _is_tool_failure(patch_result):
                ar_session["history"].append(new_msg("user", f"[Tool result]\n{patch_result}\n\nPatch failed. Try differently."))
                continue

            state = manager.step(state, hypothesis, [])
            last = state.history[-1] if state.history else None

            if last:
                delta_str = f"{last.delta:+.4f}" if last.delta is not None else "N/A"
                kept = last.status == "improved"

                # Get full metrics for this iteration
                try:
                    r = subprocess.run(setup["verify_command"].split(), capture_output=True, text=True, cwd=workdir, timeout=15)
                    metrics_output = r.stdout.strip()
                except Exception:
                    metrics_output = ""

                status_icon = "✓" if kept else "✗"
                output.progress(
                    f"  [{status_icon}] {last.status.upper()} | {metric}={last.metric} (delta={delta_str}) | "
                    f"{'KEPT' if kept else 'REVERTED'}"
                )

                # Show metrics if available
                for line in metrics_output.split("\n"):
                    if "=" in line and not line.startswith("METRIC") and not line.strip().isdigit():
                        output.print(f"    [dim]{line}[/dim]")

                result_msg = (
                    f"[Iteration {state.iteration} result]\n"
                    f"Status: {last.status}\n"
                    f"Metric ({metric}, median of repeated runs): {last.metric}\n"
                    f"Delta vs current best: {delta_str}\n"
                    f"{'Change KEPT.' if kept else 'Change REVERTED.'}\n"
                    f"\nFull metrics:\n{metrics_output}"
                )
                ar_session["history"].append(new_msg("user", result_msg))

            if state.status == "failed":
                output.error("  Run failed — stopping.")
                break

    except KeyboardInterrupt:
        output.progress("  [INTERRUPTED]")
        state.status = "stopped"

    # Summary
    output.print("")
    output.markdown(manager.summary(state))

    # Show final SQL
    final_sql = Path(setup["sql_path"]).read_text().strip()
    output.print("\n  [yellow]Final optimized SQL:[/yellow]")
    for line in final_sql.split("\n"):
        output.print(f"    {line}")

    if state.journal_path:
        output.progress(f"\n  Journal: {state.journal_path}")
    output.progress(f"  Workdir: {workdir}")
