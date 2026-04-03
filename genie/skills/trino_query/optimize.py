"""trino_optimize — Run original SQL, lint, auto-fix, re-run, show comparison.

Flow:
  1. Try execute original SQL → capture baseline metrics (OK if it fails)
  2. Lint original SQL → find issues
  3. Apply known fixes (NVL→COALESCE, SELECT *→columns, etc.)
  4. Execute fixed SQL → capture optimized metrics
  5. Output side-by-side comparison
"""
from __future__ import annotations

import json
import re
import time
from decimal import Decimal
from datetime import date, datetime, timedelta

from genie.core.arg import Arg
from genie.core.registry import BaseSkill

from . import _clean_row, _extract_metrics, _human_bytes, QueryMetrics, QueryResult
from .connection import get_active_profile


def _try_execute(sql: str, catalog: str = "", schema: str = "") -> dict:
    """Execute SQL, return {rows, columns, metrics, error, query_id}. Never raises."""
    try:
        import trino.dbapi
        cfg = get_active_profile()
        conn = cfg.connect(catalog=catalog or None, schema=schema or None)
        cur = conn.cursor()
        t0 = time.monotonic()
        cur.execute(sql)
        t_ms = int((time.monotonic() - t0) * 1000)
        desc = cur.description or []
        cols = [c[0] for c in desc]
        rows = []
        for row in cur.fetchall():
            rows.append(dict(zip(cols, _clean_row(row))))
            if len(rows) >= 100:
                break
        stats = getattr(cur, 'stats', {}) or {}
        metrics = _extract_metrics(stats)
        query_id = getattr(cur, 'query_id', '') or ''
        conn.close()
        return {
            "rows": rows, "columns": cols, "row_count": len(rows),
            "duration_ms": t_ms, "metrics": metrics.to_json(),
            "query_id": query_id, "error": None,
        }
    except Exception as exc:
        return {
            "rows": [], "columns": [], "row_count": 0,
            "duration_ms": 0, "metrics": None,
            "query_id": "", "error": str(exc),
        }


def _lint(sql: str) -> dict:
    """Run trino_linter and return result dict."""
    try:
        from genie.skills.trino_linter.analyzer import analyze
        result = analyze(sql)
        return result.to_dict()
    except Exception as exc:
        return {"findings": [], "score": "?", "summary": str(exc)}


def _auto_fix(sql: str, findings: list[dict]) -> tuple[str, list[str]]:
    """Apply automatic fixes based on lint findings. Returns (fixed_sql, changes_made)."""
    fixed = sql
    changes: list[str] = []

    rules_found = {f["rule"] for f in findings}

    # Fix NVL → COALESCE
    if "oracle-residual-nvl" in rules_found:
        fixed = re.sub(r'\bNVL\s*\(', 'COALESCE(', fixed, flags=re.IGNORECASE)
        changes.append("NVL() → COALESCE()")

    # Fix DECODE → CASE WHEN (simple 3-arg form)
    decode_pattern = r'\bDECODE\s*\(\s*(\w+)\s*,\s*([^,]+)\s*,\s*([^,]+)\s*,\s*([^)]+)\)'
    if "oracle-residual-decode" in rules_found and re.search(decode_pattern, fixed, re.IGNORECASE):
        def _decode_to_case(m):
            expr, val, result, default = m.group(1), m.group(2).strip(), m.group(3).strip(), m.group(4).strip()
            return f"CASE WHEN {expr} = {val} THEN {result} ELSE {default} END"
        fixed = re.sub(decode_pattern, _decode_to_case, fixed, flags=re.IGNORECASE)
        changes.append("DECODE() → CASE WHEN")

    # Fix SYSDATE → CURRENT_TIMESTAMP
    if "oracle-residual-sysdate" in rules_found:
        fixed = re.sub(r'\bSYSDATE\b', 'CURRENT_TIMESTAMP', fixed, flags=re.IGNORECASE)
        changes.append("SYSDATE → CURRENT_TIMESTAMP")

    # Fix SELECT * → try to resolve columns from DESCRIBE
    if "select-star" in rules_found:
        # Extract table name
        table_match = re.search(r'\bFROM\s+([\w.]+)', fixed, re.IGNORECASE)
        if table_match:
            table_name = table_match.group(1)
            try:
                cols_result = _try_execute(f"DESCRIBE {table_name}")
                if not cols_result["error"] and cols_result["rows"]:
                    col_names = [r["Column"] for r in cols_result["rows"]]
                    col_list = ", ".join(col_names)
                    # Replace SELECT * with column list
                    fixed = re.sub(
                        r'\bSELECT\s+\*',
                        f'SELECT {col_list}',
                        fixed, count=1, flags=re.IGNORECASE
                    )
                    changes.append(f"SELECT * → {len(col_names)} named columns")
            except Exception:
                pass

    return fixed, changes


def _format_comparison(before: dict | None, after: dict, changes: list[str], lint_before: dict, lint_after: dict) -> str:
    """Format side-by-side comparison text."""
    lines = []

    # Header
    lines.append("┌─────────────────────────────────────────────────────┐")
    lines.append("│          QUERY OPTIMIZATION COMPARISON              │")
    lines.append("└─────────────────────────────────────────────────────┘")
    lines.append("")

    # Changes applied
    if changes:
        lines.append("Changes applied:")
        for c in changes:
            lines.append(f"  • {c}")
        lines.append("")

    # Lint comparison
    lines.append(f"Lint score:  {lint_before.get('score', '?')} → {lint_after.get('score', '?')}")
    if lint_before.get("findings"):
        lines.append(f"  Before: {lint_before['summary']}")
        for f in lint_before["findings"]:
            lines.append(f"    [{f['severity']:>6}] {f['rule']}")
    if lint_after.get("findings"):
        lines.append(f"  After:  {lint_after['summary']}")
    elif lint_after.get("score") == "A":
        lines.append("  After:  Clean ✓")
    lines.append("")

    # Execution comparison
    if before and before.get("error"):
        lines.append(f"Original:  FAILED ({before['error'][:80]})")
    elif before and before.get("metrics"):
        bm = before["metrics"]
        lines.append("Original execution:")
        lines.append(f"  rows={before['row_count']}  cpu={bm['cpu_time_ms']}ms  wall={bm['wall_time_ms']}ms  "
                     f"mem={bm['peak_memory_human']}  input={bm['physical_input_human']}  splits={bm['total_splits']}")
    elif before is None:
        lines.append("Original:  (not executed)")

    if after.get("error"):
        lines.append(f"Optimized: FAILED ({after['error'][:80]})")
    elif after.get("metrics"):
        am = after["metrics"]
        lines.append("Optimized execution:")
        lines.append(f"  rows={after['row_count']}  cpu={am['cpu_time_ms']}ms  wall={am['wall_time_ms']}ms  "
                     f"mem={am['peak_memory_human']}  input={am['physical_input_human']}  splits={am['total_splits']}")

    # Delta
    if (before and not before.get("error") and before.get("metrics")
            and after and not after.get("error") and after.get("metrics")):
        bm = before["metrics"]
        am = after["metrics"]

        lines.append("")
        lines.append("Improvement:")

        def _delta(label, bv, av, unit="", lower_is_better=True):
            if bv == 0:
                return f"  {label}: {bv}{unit} → {av}{unit}"
            pct = (bv - av) / bv * 100
            arrow = "↓" if (pct > 0 and lower_is_better) or (pct < 0 and not lower_is_better) else "↑" if pct != 0 else "="
            return f"  {label}: {bv}{unit} → {av}{unit}  ({arrow} {abs(pct):.0f}%)"

        lines.append(_delta("CPU", bm["cpu_time_ms"], am["cpu_time_ms"], "ms"))
        lines.append(_delta("Wall", bm["wall_time_ms"], am["wall_time_ms"], "ms"))
        lines.append(_delta("Memory", bm["peak_memory_bytes"], am["peak_memory_bytes"]))
        lines.append(f"         ({bm['peak_memory_human']} → {am['peak_memory_human']})")
        lines.append(_delta("Input I/O", bm["physical_input_bytes"], am["physical_input_bytes"]))
        lines.append(f"         ({bm['physical_input_human']} → {am['physical_input_human']})")
        lines.append(_delta("Rows scanned", bm["processed_rows"], am["processed_rows"]))
        lines.append(_delta("Splits", bm["total_splits"], am["total_splits"]))

    return "\n".join(lines)


class TrinoOptimizeSkill(BaseSkill):
    """Analyze, auto-fix, and compare SQL performance.

    1. Execute original SQL → baseline metrics (OK if it fails)
    2. Lint → find issues
    3. Auto-fix what's fixable
    4. Execute fixed SQL → optimized metrics
    5. Show side-by-side comparison
    """

    name = "trino_optimize"
    description = (
        "Optimize a SQL query: lint it, auto-fix Oracle residuals and anti-patterns, "
        "execute both versions, and show a side-by-side performance comparison."
    )
    group = "trino_query"
    args = [
        Arg("sql", "str", "SQL to optimize", required=True),
        Arg("catalog", "str", "Target catalog", required=False, default=""),
        Arg("schema", "str", "Target schema", required=False, default=""),
    ]

    def run(self, ctx, sql: str = "", catalog: str = "", schema: str = "") -> str:
        out = ctx.output

        # Step 1: Lint original
        out.progress("[1/4] Linting original SQL...")
        lint_before = _lint(sql)

        # Step 2: Try execute original (baseline)
        out.progress("[2/4] Executing original SQL (baseline)...")
        before = _try_execute(sql, catalog, schema)

        # Step 3: Auto-fix
        out.progress("[3/4] Applying auto-fixes...")
        findings = lint_before.get("findings", [])
        fixed_sql, changes = _auto_fix(sql, findings)

        if not changes:
            # Nothing to fix — still show metrics
            lint_after = lint_before
            after = before
            out.progress("[4/4] No auto-fixes applicable.")
        else:
            # Step 4: Lint + execute fixed
            lint_after = _lint(fixed_sql)
            out.progress("[4/4] Executing optimized SQL...")
            after = _try_execute(fixed_sql, catalog, schema)

        # Build comparison
        comparison = _format_comparison(
            before if before and not before.get("error") is None else before,
            after, changes, lint_before, lint_after,
        )

        out.print("")
        for line in comparison.split("\n"):
            out.print(f"  {line}")

        if changes:
            out.print("")
            out.print("  [dim]Fixed SQL:[/dim]")
            for line in fixed_sql.strip().split("\n"):
                out.print(f"    {line}")

        # Return structured JSON
        return json.dumps({
            "original_sql": sql.strip(),
            "fixed_sql": fixed_sql.strip() if changes else None,
            "changes": changes,
            "lint_before": lint_before,
            "lint_after": lint_after,
            "baseline": before,
            "optimized": after if changes else None,
            "comparison_text": comparison,
        }, ensure_ascii=False)
