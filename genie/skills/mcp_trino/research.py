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

import requests
from rich.markup import escape

from genie.core.sql_extraction import extract_sql_from_reply
from .client import McpClient, McpConfig, McpError, load_mcp_config
from .preflight import CandidateTimeoutError, make_candidate_timeout_ms

# sqlglot is already a project dependency — used for table name extraction
import sqlglot


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
    table_suggestions: list[TableSuggestion] = field(default_factory=list)
    had_qualified_tables: bool = False
    original_explain: ExplainAnalyzeResult | None = None
    enhanced_explain: ExplainAnalyzeResult | None = None


@dataclass
class ColumnInfo:
    """Column metadata from information_schema."""
    column_name: str
    data_type: str
    is_nullable: str
    ordinal_position: int


@dataclass
class TableMetadata:
    """Metadata for a single table."""
    catalog: str
    schema: str
    table_name: str
    columns: list[ColumnInfo] = field(default_factory=list)
    properties: dict[str, str] = field(default_factory=dict)


@dataclass
class TableSuggestion:
    """A single table-level optimization suggestion."""
    table: str
    category: str  # partition | bucket | data_type | sort | general
    suggestion: str
    suggestion_zh: str  # 繁體中文 version
    severity: str = "info"  # info | warning | critical


# ---------------------------------------------------------------------------
# Table metadata helpers
# ---------------------------------------------------------------------------

def _extract_table_names(sql: str) -> list[tuple[str, str, str]]:
    """Extract (catalog, schema, table) tuples from SQL using sqlglot.

    Returns tuples where catalog/schema may be empty strings if not qualified.
    """
    tables = set()
    try:
        for statement in sqlglot.parse(sql, dialect="trino"):
            if statement is None:
                continue
            for table in statement.find_all(sqlglot.exp.Table):
                catalog = table.catalog or ""
                schema = table.db or ""
                name = table.name or ""
                if name and not name.startswith("__"):
                    tables.add((catalog, schema, name))
    except sqlglot.errors.ParseError:
        pass
    return sorted(tables)


def _fetch_table_metadata(
    client: McpClient,
    tables: list[tuple[str, str, str]],
    default_catalog: str = "",
    default_schema: str = "",
) -> list[TableMetadata]:
    """Query information_schema.columns and table properties via MCP.

    Gracefully returns empty list if the MCP server can't handle these queries.
    """
    results = []
    for catalog, schema, table_name in tables:
        cat = catalog or default_catalog
        sch = schema or default_schema
        if not cat or not sch:
            continue

        meta = TableMetadata(catalog=cat, schema=sch, table_name=table_name)

        # Query columns
        col_sql = (
            f"SELECT column_name, data_type, is_nullable, ordinal_position "
            f"FROM {cat}.information_schema.columns "
            f"WHERE table_schema = '{sch}' AND table_name = '{table_name}' "
            f"ORDER BY ordinal_position"
        )
        try:
            result = _execute_via_mcp(client, col_sql)
            if not result.get("error") and result.get("rows"):
                for row in result["rows"]:
                    if isinstance(row, dict):
                        meta.columns.append(ColumnInfo(
                            column_name=row.get("column_name", ""),
                            data_type=row.get("data_type", ""),
                            is_nullable=row.get("is_nullable", ""),
                            ordinal_position=int(row.get("ordinal_position", 0)),
                        ))
        except Exception:
            pass

        # Query table properties (Trino system metadata)
        prop_sql = (
            f"SELECT property_name, property_value "
            f"FROM system.metadata.table_properties "
            f"WHERE catalog_name = '{cat}' "
            f"AND schema_name = '{sch}' "
            f"AND table_name = '{table_name}'"
        )
        try:
            result = _execute_via_mcp(client, prop_sql)
            if not result.get("error") and result.get("rows"):
                for row in result["rows"]:
                    if isinstance(row, dict):
                        key = row.get("property_name", "")
                        val = row.get("property_value", "")
                        if key:
                            meta.properties[key] = val
        except Exception:
            pass

        if meta.columns or meta.properties:
            results.append(meta)

    return results


def _generate_table_suggestions(metadata: list[TableMetadata]) -> list[TableSuggestion]:
    """Analyze table metadata and generate optimization suggestions."""
    suggestions: list[TableSuggestion] = []

    for meta in metadata:
        fqn = f"{meta.catalog}.{meta.schema}.{meta.table_name}"

        # ── Partition analysis ──
        partitioning = meta.properties.get("partitioning", "")
        if not partitioning or partitioning == "[]":
            # Check for date/timestamp columns that could be partition keys
            date_cols = [
                c for c in meta.columns
                if any(t in c.data_type.lower() for t in ["date", "timestamp"])
            ]
            if date_cols:
                col_names = ", ".join(c.column_name for c in date_cols[:3])
                suggestions.append(TableSuggestion(
                    table=fqn,
                    category="partition",
                    suggestion=(
                        f"No partitioning detected. Consider partitioning by "
                        f"date/timestamp column(s): {col_names}. "
                        f"This enables partition pruning and reduces scan volume."
                    ),
                    suggestion_zh=(
                        f"未偵測到分區設定。建議使用日期/時間戳記欄位進行分區："
                        f"{col_names}。"
                        f"啟用分區裁剪可大幅減少掃描量。"
                    ),
                    severity="warning",
                ))

        # ── Bucketing analysis ──
        bucket_count = meta.properties.get("bucket_count", "")
        if not bucket_count or bucket_count == "0":
            id_cols = [
                c for c in meta.columns
                if any(k in c.column_name.lower() for k in ["_id", "id", "_key", "key"])
                and c.data_type.lower() in ("integer", "bigint", "varchar")
            ]
            if id_cols:
                col_names = ", ".join(c.column_name for c in id_cols[:2])
                suggestions.append(TableSuggestion(
                    table=fqn,
                    category="bucket",
                    suggestion=(
                        f"No bucketing configured. For tables frequently joined on "
                        f"{col_names}, bucketing can improve join performance "
                        f"by enabling bucket-pruned joins."
                    ),
                    suggestion_zh=(
                        f"未設定分桶。若此表經常以 {col_names} 進行 JOIN，"
                        f"建議設定 bucketing 以啟用分桶裁剪，提升 JOIN 效能。"
                    ),
                    severity="info",
                ))

        # ── Data type analysis ──
        for col in meta.columns:
            dtype = col.data_type.lower()
            # varchar without length → potential issue
            if dtype == "varchar" and col.column_name.lower().endswith(("_id", "_code", "_type")):
                suggestions.append(TableSuggestion(
                    table=fqn,
                    category="data_type",
                    suggestion=(
                        f"Column '{col.column_name}' is varchar (unbounded). "
                        f"Consider varchar(N) with explicit length for ID/code columns "
                        f"to improve memory estimation and query planning."
                    ),
                    suggestion_zh=(
                        f"欄位 '{col.column_name}' 使用無限長度 varchar。"
                        f"建議 ID/代碼欄位使用 varchar(N) 指定長度，"
                        f"有助於記憶體估算與查詢規劃。"
                    ),
                    severity="info",
                ))
            # double where decimal might be better
            if dtype == "double" and any(
                k in col.column_name.lower()
                for k in ["amount", "price", "cost", "revenue", "balance"]
            ):
                suggestions.append(TableSuggestion(
                    table=fqn,
                    category="data_type",
                    suggestion=(
                        f"Column '{col.column_name}' uses DOUBLE. "
                        f"For financial/monetary data, consider DECIMAL(p,s) "
                        f"to avoid floating-point precision issues."
                    ),
                    suggestion_zh=(
                        f"欄位 '{col.column_name}' 使用 DOUBLE 型別。"
                        f"財務/金額資料建議改用 DECIMAL(p,s)，"
                        f"避免浮點數精度問題。"
                    ),
                    severity="warning",
                ))

        # ── Sort order analysis ──
        sort_order = meta.properties.get("sorted_by", meta.properties.get("sort_order", ""))
        if not sort_order or sort_order == "[]":
            if len(meta.columns) > 10:
                suggestions.append(TableSuggestion(
                    table=fqn,
                    category="sort",
                    suggestion=(
                        f"No sort order configured on a wide table ({len(meta.columns)} columns). "
                        f"Setting a sort order on frequently filtered columns "
                        f"can improve min/max predicate pushdown and file skipping."
                    ),
                    suggestion_zh=(
                        f"寬表（{len(meta.columns)} 欄位）未設定排序。"
                        f"建議對經常用於篩選的欄位設定排序，"
                        f"可提升 min/max 述詞下推與檔案跳過效率。"
                    ),
                    severity="info",
                ))

    return suggestions


@dataclass
class ExplainAnalyzeResult:
    """Parsed EXPLAIN ANALYZE output from Trino."""
    raw_text: str
    stages: list[dict] = field(default_factory=list)
    total_cpu_ms: float = 0.0
    total_wall_ms: float = 0.0
    total_memory_bytes: int = 0
    total_input_rows: int = 0
    total_output_rows: int = 0
    available: bool = True


# ---------------------------------------------------------------------------
# EXPLAIN ANALYZE helpers
# ---------------------------------------------------------------------------

def _fetch_explain_analyze(
    client: McpClient,
    sql: str,
    timeout_ms: Optional[float] = None,
    label: str = "explain analyze",
) -> ExplainAnalyzeResult:
    """Run EXPLAIN ANALYZE via MCP and parse the output.

    Returns ExplainAnalyzeResult with available=False if the query fails
    (e.g. MCP server doesn't support EXPLAIN ANALYZE, or the query errors out).
    This is the fallback-safe path — never raises.
    """
    explain_sql = f"EXPLAIN ANALYZE {sql}"
    try:
        result = _execute_via_mcp(client, explain_sql, timeout_ms=timeout_ms, label=label)
        if result.get("error"):
            return ExplainAnalyzeResult(
                raw_text=str(result["error"]),
                available=False,
            )

        # EXPLAIN ANALYZE returns text rows, not tabular data
        rows = result.get("rows", [])
        raw_lines = []
        for row in rows:
            if isinstance(row, dict):
                # Trino returns single-column result with plan text
                line = str(next(iter(row.values()), ""))
            else:
                line = str(row)
            raw_lines.append(line)

        raw_text = "\n".join(raw_lines)
        if not raw_text.strip():
            raw_text = result.get("raw", "")

        # Parse stage-level metrics from EXPLAIN ANALYZE output
        stages = _parse_explain_stages(raw_text)

        # Aggregate totals
        total_cpu = sum(s.get("cpu_ms", 0) for s in stages)
        total_wall = sum(s.get("wall_ms", 0) for s in stages)
        total_mem = max((s.get("memory_bytes", 0) for s in stages), default=0)
        total_input = sum(s.get("input_rows", 0) for s in stages)
        total_output = sum(s.get("output_rows", 0) for s in stages)

        return ExplainAnalyzeResult(
            raw_text=raw_text,
            stages=stages,
            total_cpu_ms=total_cpu,
            total_wall_ms=total_wall,
            total_memory_bytes=total_mem,
            total_input_rows=total_input,
            total_output_rows=total_output,
            available=True,
        )
    except CandidateTimeoutError:
        raise
    except Exception as exc:
        return ExplainAnalyzeResult(
            raw_text=f"EXPLAIN ANALYZE failed: {exc}",
            available=False,
        )


def _parse_explain_stages(text: str) -> list[dict]:
    """Extract stage-level metrics from Trino EXPLAIN ANALYZE text output.

    Trino EXPLAIN ANALYZE output contains lines like:
        Fragment 1 [HASH]
            CPU: 1.23s, Scheduled: 2.00s, Blocked: ...
            Input: 1000 rows (50kB), Output: 100 rows (5kB)
            ...

    This parser extracts what it can and is lenient about format changes.
    """
    stages: list[dict] = []
    current_stage: dict | None = None

    for line in text.split("\n"):
        stripped = line.strip()

        # Detect stage/fragment boundaries
        fragment_match = re.match(r"(?:Fragment|Stage)\s+(\d+)", stripped, re.IGNORECASE)
        if fragment_match:
            if current_stage:
                stages.append(current_stage)
            current_stage = {"id": int(fragment_match.group(1))}
            continue

        if current_stage is None:
            continue

        # First-match-wins per stage: fragment-level metrics appear before
        # nested operator metrics; we don't want operator-level zeros to
        # overwrite the aggregated fragment values.
        time_units_to_ms = {
            "ns": 1 / 1_000_000,
            "us": 1 / 1000,
            "µs": 1 / 1000,
            "ms": 1.0,
            "s": 1000.0,
            "min": 60_000.0,
            "h": 3_600_000.0,
        }
        time_unit_re = r"(ns|us|µs|ms|s|min|h)"

        # CPU: "CPU: 52.94us" / "CPU: 1.23s" / "CPU: 123ms"
        if "cpu_ms" not in current_stage:
            cpu_match = re.search(rf"CPU:\s*([\d.]+)\s*{time_unit_re}\b", stripped, re.IGNORECASE)
            if cpu_match:
                val = float(cpu_match.group(1))
                unit = cpu_match.group(2).lower()
                current_stage["cpu_ms"] = val * time_units_to_ms.get(unit, 1.0)

        # Wall / Scheduled
        if "wall_ms" not in current_stage:
            wall_match = re.search(rf"(?:Scheduled|Wall):\s*([\d.]+)\s*{time_unit_re}\b", stripped, re.IGNORECASE)
            if wall_match:
                val = float(wall_match.group(1))
                unit = wall_match.group(2).lower()
                current_stage["wall_ms"] = val * time_units_to_ms.get(unit, 1.0)

        # Memory: "Peak Memory: 1.5MB" / "Memory: 132B"
        if "memory_bytes" not in current_stage:
            mem_match = re.search(r"(?:Peak\s+)?Memory:\s*([\d.]+)\s*(B|KB|MB|GB|TB)", stripped, re.IGNORECASE)
            if mem_match:
                val = float(mem_match.group(1))
                unit = mem_match.group(2).upper()
                multiplier = {"B": 1, "KB": 1024, "MB": 1024**2, "GB": 1024**3, "TB": 1024**4}
                current_stage["memory_bytes"] = int(val * multiplier.get(unit, 1))

        # Rows: "Input: 1000 rows" / "Output: 100 rows"
        if "input_rows" not in current_stage:
            input_match = re.search(r"Input:\s*([\d,]+)\s*rows?", stripped, re.IGNORECASE)
            if input_match:
                current_stage["input_rows"] = int(input_match.group(1).replace(",", ""))

        if "output_rows" not in current_stage:
            output_match = re.search(r"Output:\s*([\d,]+)\s*rows?", stripped, re.IGNORECASE)
            if output_match:
                current_stage["output_rows"] = int(output_match.group(1).replace(",", ""))

    if current_stage:
        stages.append(current_stage)

    return stages


# ---------------------------------------------------------------------------
# MCP execution helpers
# ---------------------------------------------------------------------------

_resolved_tool: tuple[str, str] | None = None


def _resolve_query_tool(client: McpClient) -> tuple[str, str]:
    """Find the MCP tool that executes SQL queries. Returns (tool_name, sql_param_name)."""
    global _resolved_tool
    if _resolved_tool:
        return _resolved_tool
    tools = client.list_tools()
    candidates = ("query", "trino_query", "execute", "execute_query", "run_query")
    for t in tools:
        if t["name"] in candidates:
            param = _find_sql_param(t)
            _resolved_tool = (t["name"], param)
            return _resolved_tool
    for t in tools:
        param = _find_sql_param(t)
        if param:
            _resolved_tool = (t["name"], param)
            return _resolved_tool
    available = [t["name"] for t in tools]
    raise McpError(-1, f"No SQL query tool found on MCP server. Available tools: {available}")


def _find_sql_param(tool_def: dict) -> str:
    """Detect the SQL parameter name from a tool's input schema."""
    props = tool_def.get("inputSchema", {}).get("properties", {})
    for name in ("sql", "query", "statement"):
        if name in props:
            return name
    return "sql"


def _execute_via_mcp(client: McpClient, sql: str, timeout_ms: Optional[float] = None, label: str = "candidate") -> dict:
    """Execute SQL via MCP server, return parsed result with timing."""
    tool_name, sql_param = _resolve_query_tool(client)
    t0 = time.monotonic()
    try:
        kwargs = {"timeout": timeout_ms / 1000.0} if timeout_ms is not None else {}
        raw = client.call_tool(tool_name, {sql_param: sql}, **kwargs)
    except requests.exceptions.Timeout as exc:
        raise CandidateTimeoutError(timeout_ms or 0, label) from exc
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

    # Extract rows and columns. Two response shapes seen in the wild:
    #   (a) {"rows": [...], "columns": [...], "metrics": {...}, ...}  — wrapped
    #   (b) [{"col1": val, ...}, ...]                                  — bare list
    # mcp-trino returns (b); previous code silently dropped to rows=[].
    if isinstance(data, dict):
        rows = data.get("rows", [])
        columns = data.get("columns", [])
        error = data.get("error")
    elif isinstance(data, list):
        rows = data
        columns = list(rows[0].keys()) if rows and isinstance(rows[0], dict) else []
        error = None
    else:
        rows = []
        columns = []
        error = None

    return {
        "rows": rows,
        "columns": columns,
        "row_count": len(rows),
        "metrics": metrics,
        "error": error,
        "raw": raw,
    }


def _build_mcp_explain_runner(client: McpClient):
    """Return an `(sql) -> str | None` callable that runs EXPLAIN (FORMAT JSON).

    Reuses the resolved query tool — most mcp-trino servers do not expose a
    dedicated explain tool, so EXPLAIN is issued as an ordinary statement and
    the raw plan text is pulled from the response. Returns None on any failure
    so callers can treat plan-cost as best-effort.
    """
    def _runner(s: str) -> Optional[str]:
        try:
            result = _execute_via_mcp(client, f"EXPLAIN (FORMAT JSON) {s}")
        except Exception:
            return None
        if result.get("error"):
            return None
        rows = result.get("rows") or []
        # EXPLAIN (FORMAT JSON) returns a single-cell row holding the plan text.
        if rows:
            first = rows[0]
            if isinstance(first, dict):
                for val in first.values():
                    if isinstance(val, str) and val.strip():
                        return val
            elif isinstance(first, (list, tuple)) and first:
                if isinstance(first[0], str):
                    return first[0]
            elif isinstance(first, str):
                return first
        raw = result.get("raw")
        return raw if isinstance(raw, str) else None
    return _runner


def _assemble_mcp_directions(client, sql, static_report, *, peak_memory_bytes=None, table_metadata=None):
    """Gather diagnostics → ranked directions at zero query-execution cost.

    Single source of truth for the three MCP call sites that need a diagnosis:
    the optimizer-prompt injection (has-data success path), the long-query
    gate-trip directed report, and the ``--diagnose-only`` short-circuit.
    EXPLAIN (FORMAT JSON) plans the query without running it; metadata is a
    cheap catalog round-trip. Returns ``(directions, table_metadata)`` so the
    success path can reuse the fetched metadata for its post-loop block.
    """
    from genie.skills.mcp_trino.pre_execution_diagnosis import pre_execution_diagnosis
    from .preflight import plan_cost

    if table_metadata is not None:
        # Reuse already-fetched metadata (same tables across a rewrite) to avoid
        # a redundant catalog round-trip on per-iteration re-diagnosis (v32 T1).
        pre_table_metadata = list(table_metadata)
    else:
        pre_table_metadata = []
        diag_refs = [(c, s, t) for (c, s, t) in _extract_table_names(sql) if c and s]
        if diag_refs:
            try:
                pre_table_metadata = _fetch_table_metadata(client, diag_refs)
            except Exception:
                pre_table_metadata = []

    explain_cost = None
    try:
        explain_cost = plan_cost(sql, _build_mcp_explain_runner(client))
    except Exception:
        explain_cost = None

    directions = pre_execution_diagnosis(
        sql,
        static_report=static_report,
        explain_cost=explain_cost,
        table_metadata=pre_table_metadata or None,
        peak_memory_bytes=peak_memory_bytes,
    )
    return directions, pre_table_metadata


def _measure_mcp(client: McpClient, sql: str, metric_key: str,
                  runs: int, capture_rows: bool = False,
                  max_capture_rows: int = 100_000,
                  output=None, label: str = "query",
                  timeout_ms: Optional[float] = None) -> MeasureResult:
    """Run SQL `runs` times via MCP, return median metric + all data.

    If captured row count exceeds max_capture_rows, rows are truncated to
    max_capture_rows to protect against OOM. Caller should treat the truncation
    as best-effort: equivalence comparison becomes partial.

    When `output` is passed and the sink supports `status()`, each run shows
    a live spinner so the user sees progress during long verify loops.
    """
    samples = []
    all_metrics = []
    last_rows = []
    last_columns = []
    row_count = 0

    for i in range(runs):
        run_label = f"{label}: run {i + 1}/{runs}"
        if timeout_ms is not None:
            run_label = f"{run_label} limit={timeout_ms / 1000.0:.1f}s"
        if output and hasattr(output, "status"):
            with output.status(run_label):
                result = _execute_via_mcp(client, sql, timeout_ms=timeout_ms, label=label)
        else:
            result = _execute_via_mcp(client, sql, timeout_ms=timeout_ms, label=label)
        if result["error"]:
            raise RuntimeError(f"MCP query failed: {result['error']}")

        metrics = result["metrics"]

        # Server-stat backfill: some MCP servers (e.g. mcp-trino) execute the
        # query but don't populate structured per-run stats — they return only
        # the rows, so query_time_ms (Python-measured) is non-zero but every
        # server-side field reads as 0. Fall back to a single EXPLAIN ANALYZE
        # round and parse the stage totals so the optimizer can rank candidates.
        if metrics.cpu_time_ms == 0 and metrics.peak_memory_bytes == 0:
            ea_label = f"{label}: explain-analyze backfill {i + 1}/{runs}"
            if timeout_ms is not None:
                ea_label = f"{ea_label} limit={timeout_ms / 1000.0:.1f}s"
            if output and hasattr(output, "status"):
                with output.status(ea_label):
                    ea = _fetch_explain_analyze(
                        client, sql, timeout_ms=timeout_ms, label=f"{label} explain-analyze backfill",
                    )
            else:
                ea = _fetch_explain_analyze(
                    client, sql, timeout_ms=timeout_ms, label=f"{label} explain-analyze backfill",
                )
            if ea.available:
                metrics.cpu_time_ms = ea.total_cpu_ms
                metrics.wall_time_ms = ea.total_wall_ms
                metrics.peak_memory_bytes = ea.total_memory_bytes
                metrics.processed_rows = ea.total_input_rows

        value = getattr(metrics, metric_key, metrics.query_time_ms)
        samples.append(float(value))
        all_metrics.append(metrics)
        row_count = result["row_count"]

        if capture_rows and i == runs - 1:
            raw_rows = result["rows"] or []
            if len(raw_rows) > max_capture_rows:
                last_rows = raw_rows[:max_capture_rows]
            else:
                last_rows = raw_rows
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


# ---------------------------------------------------------------------------
# Long-query plan-cost loop (MCP parity)
# ---------------------------------------------------------------------------

def _run_mcp_plan_cost_loop(
    *,
    client: McpClient,
    provider,
    model: str,
    reasoning: str,
    original_sql: str,
    metric_key: str,
    max_iterations: int,
    verify_runs: int,
    output,
    build_prompt: Callable[..., str] | None,
    baseline: MeasureResult,
    static_report,
    explain_runner: Callable[[str], Optional[str]],
    max_fallbacks: int,
    candidate_timeout_ms: Optional[float] = None,
) -> EnhancementReport:
    """Plan-cost ranking + L1 structural guard + K-retry for the MCP path.

    Iterations run only LLM + EXPLAIN (FORMAT JSON). Real MCP execution is
    delayed until verification, where candidates are tried by ascending plan
    cost until one passes row-equivalence.
    """
    from genie.core.provider import CompletionRequest
    from genie.session.manager import new_msg, new_session
    from genie.skills.mcp_trino.pre_execution_diagnosis import (
        format_directions_for_prompt,
        pre_execution_diagnosis,
    )
    from genie.skills.mcp_trino.preflight import plan_cost
    from genie.skills.mcp_trino.rule_gate import (
        build_rule_gate_summary,
        format_rule_gate_for_prompt,
        render_rule_gate_summary,
    )
    from genie.skills.trino_query.plan_signature import (
        plan_signature,
        structural_equivalent,
    )
    from genie.skills.trino_query.research import (
        _format_static_findings,
        _lint_sql,
    )

    baseline_metric = baseline.median_metric
    if candidate_timeout_ms is None:
        baseline_wall_ms = float(baseline.metrics.wall_time_ms or baseline.metrics.query_time_ms or 0)
        candidate_timeout_ms = make_candidate_timeout_ms(baseline_wall_ms) if baseline_wall_ms > 0 else None

    baseline_rows_est, baseline_bytes_est, baseline_plan = plan_cost(
        original_sql, explain_runner
    )
    baseline_sig = plan_signature(baseline_plan) if baseline_plan is not None else None
    baseline_cost = (baseline_rows_est or 0) * (baseline_bytes_est or 1)
    directions = pre_execution_diagnosis(
        original_sql,
        static_report=static_report,
        explain_cost=(baseline_rows_est, baseline_bytes_est, baseline_plan),
        table_metadata=None,
        peak_memory_bytes=getattr(baseline.metrics, "peak_memory_bytes", 0) or None,
    )
    rule_gate = build_rule_gate_summary(static_report, directions)
    rule_gate_block = format_rule_gate_for_prompt(rule_gate)
    directions_block = format_directions_for_prompt(directions)

    if output:
        output.print("")
        timeout_text = (
            f", candidate_timeout={candidate_timeout_ms / 1000.0:.1f}s"
            if candidate_timeout_ms is not None else ""
        )
        output.progress(
            f"  [long-query] MCP plan-cost loop active "
            f"(baseline rows~{baseline_rows_est}, bytes~{baseline_bytes_est}, "
            f"max_fallbacks={max_fallbacks}{timeout_text})"
        )
        render_rule_gate_summary(output, rule_gate)

    skill_prompt = build_prompt(True, model) if build_prompt else ""
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

    candidates: list[dict] = []
    history: list[dict] = []

    for iteration in range(1, max_iterations + 1):
        if output:
            output.print("")
            output.progress(f"  ── Iteration {iteration}/{max_iterations} (MCP plan-cost mode) ──")

        static_block = ""
        if iteration == 1 and static_report and static_report.findings:
            static_block = (
                "Static analysis findings (sqlglot AST rules — apply these in priority order):\n"
                f"{_format_static_findings(static_report)}\n\n"
            )

        sys_msgs = [m for m in session["history"] if m["role"] == "system"]
        non_sys = [m for m in session["history"] if m["role"] != "system"]
        session["history"] = sys_msgs + non_sys[-4:]

        context = (
            f"[Long-query MCP plan-cost iteration {iteration}]\n"
            f"Baseline rows estimate: {baseline_rows_est}\n"
            f"Baseline bytes estimate: {baseline_bytes_est}\n"
            f"{static_block}"
            f"Current SQL:\n```sql\n{original_sql}\n```\n\n"
            f"Return the COMPLETE optimized SQL in a ```sql block. ONE change only. "
            f"Do NOT include a trailing semicolon."
        )
        session["history"].append(new_msg("user", context))

        if output:
            output.progress("  AI thinking...")
        req = CompletionRequest(messages=session["history"], model=model, reasoning=reasoning)
        reply = provider.complete_text(req)
        if not reply:
            if output:
                output.error("  Empty AI response — stopping iteration phase.")
            break
        session["history"].append(new_msg("assistant", reply))

        candidate_sql = extract_sql_from_reply(reply)
        if not candidate_sql:
            if output:
                output.progress("  [SKIP] No SQL extracted")
            history.append({
                "iteration": iteration, "status": "no_sql",
                "candidate_sql": None, "plan_cost": None,
            })
            continue

        lint_ok, lint_msg = _lint_sql(candidate_sql)
        if not lint_ok:
            if output:
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
            if output:
                output.progress(f"  [SKIP] EXPLAIN failed: {exc}")
            history.append({
                "iteration": iteration, "status": "explain_failed",
                "candidate_sql": candidate_sql, "plan_cost": None,
            })
            continue

        if cand_rows_est is None and cand_bytes_est is None:
            if output:
                output.progress("  [SKIP] EXPLAIN returned no estimates")
            history.append({
                "iteration": iteration, "status": "explain_failed",
                "candidate_sql": candidate_sql, "plan_cost": None,
            })
            continue

        cand_cost = (cand_rows_est or 0) * (cand_bytes_est or 1)
        cand_sig = plan_signature(cand_plan) if cand_plan is not None else None

        if baseline_sig is not None and cand_sig is not None:
            if not structural_equivalent(baseline_plan, cand_plan):
                if output:
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

        verdict = "plan_cost_better" if cand_cost < baseline_cost else "plan_cost_worse"
        if output:
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

    if output:
        output.print("")
        output.progress(f"  [verify] {len(candidates)} candidate(s) survived L1; ranking by plan cost")

    surviving_better = sorted(
        [c for c in candidates if c["plan_cost"] < baseline_cost],
        key=lambda c: c["plan_cost"],
    )

    fallbacks_used = 0
    winner: dict | None = None
    verify_log: list[dict] = []
    if surviving_better:
        for ranked in surviving_better:
            if fallbacks_used > max_fallbacks:
                if output:
                    output.progress(f"  [verify] Exhausted K={max_fallbacks} fallbacks")
                break
            if output:
                output.progress(
                    f"  [verify] Trying iter#{ranked['iteration']} "
                    f"(plan_cost={ranked['plan_cost']:.2e})"
                )
            try:
                measured = _measure_mcp(
                    client, ranked["sql"], metric_key, verify_runs,
                    capture_rows=True,
                    output=output,
                    label=f"verify iter {ranked['iteration']}",
                    timeout_ms=candidate_timeout_ms,
                )
            except CandidateTimeoutError as exc:
                if output:
                    output.progress(f"  [verify] timeout_worse: {exc}")
                verify_log.append({"iter": ranked["iteration"], "result": "timeout_worse", "reason": str(exc)})
                fallbacks_used += 1
                continue
            except Exception as exc:
                if output:
                    output.progress(f"  [verify] _measure_mcp failed: {exc}")
                verify_log.append({"iter": ranked["iteration"], "result": "exec_failed", "reason": str(exc)})
                fallbacks_used += 1
                continue

            if baseline.row_count != measured.row_count:
                equiv = False
                reason = f"row count differs: {baseline.row_count} vs {measured.row_count}"
            else:
                equiv, reason = _results_equivalent(baseline.rows, measured.rows)
            if not equiv:
                if output:
                    output.progress(f"  [verify] L3 row-equiv FAIL — {reason}")
                verify_log.append({"iter": ranked["iteration"], "result": "row_equiv_fail", "reason": reason})
                fallbacks_used += 1
                continue

            winner = {
                **ranked,
                "measure": measured,
            }
            verify_log.append({"iter": ranked["iteration"], "result": "verified", "metric": measured.median_metric})
            break
    elif output:
        output.progress("  [verify] No candidate beats baseline plan cost — original SQL unchanged")

    best_sql = original_sql
    best_measure = baseline
    best_value = baseline_metric
    if winner is not None:
        best_sql = winner["sql"]
        best_measure = winner["measure"]
        best_value = best_measure.median_metric

    iterations = [
        IterationRecord(
            iteration=h["iteration"],
            status="improved" if (winner is not None and h.get("candidate_sql") == winner["sql"]) else h["status"],
            metric_value=best_value if (winner is not None and h.get("candidate_sql") == winner["sql"]) else baseline_metric,
            delta=(best_value - baseline_metric) if (winner is not None and h.get("candidate_sql") == winner["sql"]) else 0.0,
            hypothesis="(plan-cost-loop)",
            sql=h.get("candidate_sql") or "",
        )
        for h in history
    ]

    if baseline.row_count != best_measure.row_count:
        final_equiv = False
        final_reason = f"row count differs: {baseline.row_count} vs {best_measure.row_count}"
    else:
        final_equiv, final_reason = _results_equivalent(baseline.rows, best_measure.rows)

    improvement_abs = best_value - baseline_metric
    improvement_pct = (improvement_abs / baseline_metric * 100) if baseline_metric else 0.0
    qualified_refs = [(c, s, t) for (c, s, t) in _extract_table_names(original_sql) if c and s]

    report = EnhancementReport(
        timestamp=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        original_sql=original_sql,
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
        baseline_value=baseline_metric,
        best_value=best_value,
        improvement_abs=improvement_abs,
        improvement_pct=improvement_pct,
        iterations=iterations,
        data_consistent=final_equiv,
        data_consistency_reason=final_reason,
        mcp_server_url=client.config.url,
        verify_runs=verify_runs,
        table_suggestions=[],
        had_qualified_tables=bool(qualified_refs),
        original_explain=None,
        enhanced_explain=None,
    )

    _render_summary_card(
        output,
        baseline_value=baseline_metric,
        best_value=best_value,
        metric_key=metric_key,
        improvement_abs=improvement_abs,
        improvement_pct=improvement_pct,
        data_consistent=final_equiv,
        data_consistency_reason=final_reason,
        iterations_ran=len(iterations),
    )

    return report




# ---------------------------------------------------------------------------
# Report generation (fixed format)
# ---------------------------------------------------------------------------

_LABELS_EN = {
    "title": "Trino Query Enhancement Report",
    "meta": "Meta",
    "perf": "Performance Comparison",
    "summary": "Summary",
    "iter_history": "Iteration History",
    "orig_sql": "Original SQL",
    "orig_result": "Original Result (sample)",
    "enh_sql": "Enhanced SQL",
    "enh_result": "Enhanced Result (sample)",
    "table_suggestions": "Table Structure Suggestions",
    "field": "Field",
    "value": "Value",
    "metric": "Metric",
    "original": "Original",
    "enhanced": "Enhanced",
    "delta": "Delta",
    "change_pct": "Change %",
    "round": "Round",
    "status": "Status",
    "metric_value": "Metric Value",
    "hypothesis": "Hypothesis",
    "baseline": "Baseline",
    "best": "Best",
    "improvement": "Improvement",
    "orig_rows": "Original Row Count",
    "enh_rows": "Enhanced Row Count",
    "data_consistent": "Data Consistent",
    "consistency_detail": "Consistency Detail",
    "lower_better": "lower is better",
    "median": "median",
    "no_improve": "no improvement found — original SQL unchanged",
    "no_data": "no data",
    "sample_note": "First 10 rows of the query output, used to visually spot-check that the enhanced SQL preserves the original result set.",
    "generated_by": "Generated by genieCLI mcp_trino research — Lakehouse Team —",
    "unqualified_tables_note": "No fully-qualified tables (catalog.schema.table) found in the SQL, so information_schema metadata was not fetched. Use qualified names (e.g. `hive.default.orders`) or set a default catalog/schema to enable this section.",
    "table": "Table",
    "category": "Category",
    "severity": "Severity",
    "suggestion": "Suggestion",
    "no_suggestions": "No table structure issues detected.",
    "explain_analyze": "EXPLAIN ANALYZE",
    "explain_original": "Original Query Plan",
    "explain_enhanced": "Enhanced Query Plan",
    "explain_unavailable": "EXPLAIN ANALYZE data not available.",
    "stage": "Stage",
    "cpu_ms": "CPU (ms)",
    "wall_ms": "Wall (ms)",
    "memory": "Memory",
    "input_rows": "Input Rows",
    "output_rows": "Output Rows",
    "timestamp": "Timestamp",
    "mcp_server": "MCP Server",
    "target_metric": "Target Metric",
    "verify_runs": "Verify Runs",
    "iterations": "Iterations",
}

_LABELS_ZH = {
    "title": "Trino 查詢優化報告",
    "meta": "基本資訊",
    "perf": "效能比較",
    "summary": "摘要",
    "iter_history": "迭代歷程",
    "orig_sql": "原始 SQL",
    "orig_result": "原始結果（樣本）",
    "enh_sql": "優化後 SQL",
    "enh_result": "優化後結果（樣本）",
    "table_suggestions": "表結構優化建議",
    "field": "欄位",
    "value": "值",
    "metric": "指標",
    "original": "原始",
    "enhanced": "優化後",
    "delta": "差異",
    "change_pct": "變化 %",
    "round": "輪次",
    "status": "狀態",
    "metric_value": "指標值",
    "hypothesis": "假說",
    "baseline": "基線",
    "best": "最佳",
    "improvement": "改善",
    "orig_rows": "原始列數",
    "enh_rows": "優化後列數",
    "data_consistent": "資料一致性",
    "consistency_detail": "一致性細節",
    "lower_better": "越低越好",
    "median": "中位數",
    "no_improve": "未找到改善方案 — 原始 SQL 不變",
    "no_data": "無資料",
    "sample_note": "查詢輸出的前 10 列，供目視比對優化後的 SQL 是否保留原始結果集。",
    "generated_by": "由 genieCLI mcp_trino research — Lakehouse Team — 產生於",
    "unqualified_tables_note": "SQL 中未發現完整限定名稱 (catalog.schema.table)，故未擷取 information_schema 中繼資料。請改用完整名稱（例如 `hive.default.orders`）或設定預設 catalog/schema 以啟用此區段。",
    "table": "表名",
    "category": "類別",
    "severity": "嚴重度",
    "suggestion": "建議",
    "no_suggestions": "未偵測到表結構問題。",
    "explain_analyze": "EXPLAIN ANALYZE",
    "explain_original": "原始查詢計畫",
    "explain_enhanced": "優化後查詢計畫",
    "explain_unavailable": "EXPLAIN ANALYZE 資料不可用。",
    "stage": "階段",
    "cpu_ms": "CPU (ms)",
    "wall_ms": "Wall (ms)",
    "memory": "記憶體",
    "input_rows": "輸入列數",
    "output_rows": "輸出列數",
    "timestamp": "時間戳記",
    "mcp_server": "MCP 伺服器",
    "target_metric": "目標指標",
    "verify_runs": "驗證次數",
    "iterations": "迭代次數",
}


def generate_report(report: EnhancementReport, locale: str = "en") -> str:
    """Generate a fixed-format markdown report.

    This template is ALWAYS the same structure — sections, headers, and table
    columns never change between runs. Only the data values differ.

    Args:
        report: The enhancement report data.
        locale: "en" for English, "zh" for Traditional Chinese.
                SQL, metrics, and column names always stay English.
    """
    L = _LABELS_ZH if locale == "zh" else _LABELS_EN
    lines = []

    def _fmt_ms(val: float) -> str:
        """Adaptive millisecond formatter.

        `:.0f` rounds sub-millisecond values to 0, which hides real data for
        fast queries (Trino 467 emits microseconds / nanoseconds for short
        queries). We scale decimal precision to magnitude.
        """
        if val is None:
            return "0"
        if val == 0:
            return "0"
        absv = abs(val)
        if absv < 0.001:
            return f"{val:.4f}"
        if absv < 1:
            return f"{val:.3f}"
        if absv < 100:
            return f"{val:.2f}"
        return f"{val:.0f}"

    # ── Header ──
    lines.append(f"# {L['title']}")
    lines.append("")
    lines.append(f"## {L['meta']}")
    lines.append("")
    lines.append(f"| {L['field']} | {L['value']} |")
    lines.append("|-------|-------|")
    lines.append(f"| {L['timestamp']} | {report.timestamp} |")
    lines.append(f"| {L['mcp_server']} | {report.mcp_server_url} |")
    lines.append(f"| {L['target_metric']} | {report.metric_key} ({L['lower_better']}) |")
    lines.append(f"| {L['verify_runs']} | {report.verify_runs} ({L['median']}) |")
    lines.append(f"| {L['iterations']} | {len(report.iterations)} |")
    lines.append("")

    # ── Performance Comparison ──
    lines.append(f"## {L['perf']}")
    lines.append("")
    lines.append(f"| {L['metric']} | {L['original']} | {L['enhanced']} | {L['delta']} | {L['change_pct']} |")
    lines.append("|--------|----------|----------|-------|----------|")

    for attr in ["query_time_ms", "cpu_time_ms", "wall_time_ms"]:
        orig = getattr(report.original_metrics, attr, 0)
        enh = getattr(report.enhanced_metrics, attr, 0)
        delta = enh - orig
        pct = (delta / orig * 100) if orig else 0
        lines.append(
            f"| {attr} | {_fmt_ms(orig)} | {_fmt_ms(enh)} | {_fmt_ms(delta)} | {pct:+.1f}% |"
        )

    for attr in ["processed_rows", "total_splits", "peak_memory_bytes", "physical_input_bytes"]:
        orig = getattr(report.original_metrics, attr, 0)
        enh = getattr(report.enhanced_metrics, attr, 0)
        delta = enh - orig
        pct = (delta / orig * 100) if orig else 0
        lines.append(f"| {attr} | {orig} | {enh} | {delta:+} | {pct:+.1f}% |")

    lines.append("")

    # ── Summary ──
    lines.append(f"## {L['summary']}")
    lines.append("")
    lines.append(f"| {L['field']} | {L['value']} |")
    lines.append("|-------|-------|")
    lines.append(f"| {L['baseline']} ({report.metric_key}) | {report.baseline_value:.1f} |")
    lines.append(f"| {L['best']} ({report.metric_key}) | {report.best_value:.1f} |")
    lines.append(f"| {L['improvement']} | {report.improvement_abs:+.1f} ({report.improvement_pct:+.1f}%) |")
    lines.append(f"| {L['orig_rows']} | {report.original_row_count} |")
    lines.append(f"| {L['enh_rows']} | {report.enhanced_row_count} |")
    lines.append(f"| {L['data_consistent']} | {'YES' if report.data_consistent else 'NO'} |")
    lines.append(f"| {L['consistency_detail']} | {report.data_consistency_reason} |")
    lines.append("")

    # ── Iteration History ──
    lines.append(f"## {L['iter_history']}")
    lines.append("")
    lines.append(f"| {L['round']} | {L['status']} | {L['metric_value']} | {L['delta']} | {L['hypothesis']} |")
    lines.append("|-------|--------|-------------|-------|------------|")

    for it in report.iterations:
        lines.append(
            f"| {it.iteration} | {it.status} | {it.metric_value:.1f} | "
            f"{it.delta:+.1f} | {it.hypothesis[:60]} |"
        )

    lines.append("")

    # ── Original SQL ──
    lines.append(f"## {L['orig_sql']}")
    lines.append("")
    lines.append("```sql")
    lines.append(report.original_sql)
    lines.append("```")
    lines.append("")

    # ── Original Result (sample) ──
    lines.append(f"## {L['orig_result']}")
    lines.append("")
    lines.append(f"_{L['sample_note']}_")
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
        lines.append(f"_({L['no_data']})_")
    lines.append("")

    # ── Enhanced SQL ──
    lines.append(f"## {L['enh_sql']}")
    lines.append("")
    if report.enhanced_sql != report.original_sql:
        lines.append("```sql")
        lines.append(report.enhanced_sql)
        lines.append("```")
    else:
        lines.append(f"_({L['no_improve']})_")
    lines.append("")

    # ── Enhanced Result (sample) ──
    lines.append(f"## {L['enh_result']}")
    lines.append("")
    lines.append(f"_{L['sample_note']}_")
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
        lines.append(f"_({L['no_data']})_")
    lines.append("")

    # ── Table Structure Suggestions ──
    lines.append(f"## {L['table_suggestions']}")
    lines.append("")
    if report.table_suggestions:
        lines.append(f"| {L['table']} | {L['category']} | {L['severity']} | {L['suggestion']} |")
        lines.append("|-------|----------|----------|------------|")
        for s in report.table_suggestions:
            text = s.suggestion_zh if locale == "zh" else s.suggestion
            lines.append(f"| {s.table} | {s.category} | {s.severity} | {text} |")
    elif report.had_qualified_tables:
        lines.append(f"_({L['no_suggestions']})_")
    else:
        lines.append(f"_{L['unqualified_tables_note']}_")
    lines.append("")

    # ── EXPLAIN ANALYZE ──
    lines.append(f"## {L['explain_analyze']}")
    lines.append("")

    def _render_explain(explain: ExplainAnalyzeResult | None, label: str) -> None:
        lines.append(f"### {label}")
        lines.append("")
        if explain is None or not explain.available:
            lines.append(f"_({L['explain_unavailable']})_")
            lines.append("")
            return
        if explain.stages:
            lines.append(f"| {L['stage']} | {L['cpu_ms']} | {L['wall_ms']} | {L['memory']} | {L['input_rows']} | {L['output_rows']} |")
            lines.append("|-------|---------|---------|--------|------------|-------------|")
            for s in explain.stages:
                mem = s.get("memory_bytes", 0)
                mem_str = f"{mem / 1024 / 1024:.1f}MB" if mem > 1024 * 1024 else f"{mem / 1024:.1f}KB" if mem > 1024 else f"{mem}B"
                lines.append(
                    f"| {s.get('id', '?')} "
                    f"| {_fmt_ms(s.get('cpu_ms', 0))} "
                    f"| {_fmt_ms(s.get('wall_ms', 0))} "
                    f"| {mem_str} "
                    f"| {s.get('input_rows', 0):,} "
                    f"| {s.get('output_rows', 0):,} |"
                )
            lines.append("")
            return
        # Stages not parseable (e.g. MCP server returned JSON or non-text plan).
        # Render a concise totals summary from whatever we captured, and hide
        # the raw dump to keep the report readable.
        mem = explain.total_memory_bytes or 0
        mem_str = f"{mem / 1024 / 1024:.1f}MB" if mem > 1024 * 1024 else f"{mem / 1024:.1f}KB" if mem > 1024 else f"{mem}B"
        lines.append(f"| {L['field']} | {L['value']} |")
        lines.append("|-------|-------|")
        lines.append(f"| {L['cpu_ms']} | {_fmt_ms(explain.total_cpu_ms)} |")
        lines.append(f"| {L['wall_ms']} | {_fmt_ms(explain.total_wall_ms)} |")
        lines.append(f"| {L['memory']} | {mem_str} |")
        lines.append(f"| {L['input_rows']} | {explain.total_input_rows:,} |")
        lines.append(f"| {L['output_rows']} | {explain.total_output_rows:,} |")
        lines.append("")
        lines.append(f"_Plan text was returned by the MCP server in a format this report cannot parse into stages. "
                     f"Run `EXPLAIN ANALYZE <sql>` directly for full detail._")
        lines.append("")

    _render_explain(report.original_explain, L["explain_original"])
    _render_explain(report.enhanced_explain, L["explain_enhanced"])

    # ── Footer ──
    lines.append("---")
    lines.append(f"_{L['generated_by']} {report.timestamp}_")
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
    *,
    long_query_opt_in: bool = True,
    long_query_threshold_s: Optional[int] = None,
    max_fallbacks: Optional[int] = None,
    diagnose_only: bool = False,
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

    # ── Static analysis (cheap; runs in both has-data and no-data paths) ──
    from genie.skills.trino_query.sql_static import analyze as static_analyze
    from genie.skills.trino_query.sql_static import summary_line as _static_summary_line
    try:
        static_report = static_analyze(sql)
    except Exception as exc:
        if output:
            output.progress(f"  [warn] static analysis skipped: {exc}")
        static_report = None
    if output and static_report is not None:
        output.progress(f"  Static analysis: {_static_summary_line(static_report)}")

    from .preflight import (
        DEFAULT_LONG_QUERY_THRESHOLD_S,
        DEFAULT_MAX_FALLBACKS,
        LongQueryAbort,
        NoDataDetected,
        check_long_query_gate,
        detect_no_data_reason,
        make_query_max_run_time_sql,
        plan_cost,
    )

    # ── --diagnose-only short-circuit (v29 T3): zero query cost ──
    # No baseline, no iteration loop, no EXPLAIN ANALYZE. EXPLAIN (FORMAT JSON)
    # plans the query without running it; static + metadata are cheap. Emit a
    # directed report and stop. peak_memory_bytes is None (no run happened).
    if diagnose_only:
        from genie.skills.mcp_trino.pre_execution_diagnosis import format_directions_report
        if output:
            output.progress("  Diagnose only: EXPLAIN-cost + static + metadata, no query execution")
        directions, _ = _assemble_mcp_directions(
            client, sql, static_report, peak_memory_bytes=None
        )
        report_md = format_directions_report(
            directions, sql=sql,
            reason="--diagnose-only requested (no baseline, no iteration)",
            model=model,
        )
        raise LongQueryAbort(
            "diagnose-only: directed report emitted (no query executed)",
            0.0, 0.0, report_markdown=report_md,
        )

    # ── Baseline ──
    if output:
        output.progress("  Measuring baseline...")

    baseline = None
    baseline_exc: BaseException | None = None
    try:
        baseline = _measure_mcp(client, sql, metric_key, verify_runs, capture_rows=True,
                                output=output, label="baseline")
    except Exception as exc:
        baseline_exc = exc

    # ── No-data dispatch (v28 T9 — wired into MCP path) ──
    no_data = detect_no_data_reason(
        baseline_row_count=baseline.row_count if baseline else None,
        baseline_exc=baseline_exc,
    )
    if no_data is not None:
        if output:
            if no_data == "table_not_found":
                output.progress(f"  [yellow]No-data path:[/yellow] table/schema not found — switching to static analysis report")
            else:
                output.progress(f"  [yellow]No-data path:[/yellow] baseline returned 0 rows — switching to static analysis report")
        from genie.skills.trino_query.research import _run_no_data_path
        result = _run_no_data_path(
            provider=provider,
            model=model,
            reasoning=reasoning,
            original_sql=sql,
            no_data_reason=no_data,
            static_report=static_report,
            baseline_exc=baseline_exc,
            output=output,
        )
        raise NoDataDetected(no_data, result)

    if baseline_exc is not None:
        # Real failure — not a no-data case, propagate
        raise baseline_exc

    if output:
        output.progress(f"  Baseline {metric_key}: {baseline.median_metric:.1f} (median of {verify_runs} runs)")
        output.progress(f"  Baseline rows: {baseline.row_count}")
        output.print(f"    [dim]{baseline.metrics.summary()}[/dim]")
        if static_report and static_report.findings:
            output.progress(
                f"  Static analysis: {static_report.summary} "
                f"({len(static_report.findings)} finding(s))"
            )

    # ── Upfront cost gate (v28) ──
    threshold_s = long_query_threshold_s if long_query_threshold_s is not None else DEFAULT_LONG_QUERY_THRESHOLD_S
    fallbacks = max_fallbacks if max_fallbacks is not None else DEFAULT_MAX_FALLBACKS
    gate = check_long_query_gate(
        baseline_wall_ms=float(baseline.metrics.wall_time_ms or baseline.metrics.query_time_ms or 0),
        max_iterations=max_iterations,
        long_query_opt_in=long_query_opt_in,
        threshold_s=threshold_s,
        max_fallbacks=fallbacks,
    )
    if not gate.ok:
        # v29 T3: instead of a bare abort, emit a directed report.
        # The baseline already ran (one query) so its real peak memory feeds
        # the diagnosis; EXPLAIN (FORMAT JSON) + static + metadata add the rest.
        # No further query / no EXPLAIN ANALYZE / no iteration loop.
        from genie.skills.mcp_trino.pre_execution_diagnosis import format_directions_report
        if output:
            output.progress(f"  Long-query gate: {gate.message}")
            output.progress("  Writing directed report and skipping further query executions")
        directions, _ = _assemble_mcp_directions(
            client, sql, static_report,
            peak_memory_bytes=getattr(baseline.metrics, "peak_memory_bytes", 0) or None,
        )
        report_md = format_directions_report(
            directions, sql=sql,
            reason=gate.message,
            model=model,
            baseline_already_ran=True,
        )
        raise LongQueryAbort(
            gate.message, gate.baseline_s, gate.predicted_total_s,
            report_markdown=report_md,
        )

    mcp_explain_runner = _build_mcp_explain_runner(client)
    mcp_explain_available = False
    if long_query_opt_in and max_iterations > 0 and mcp_explain_runner is not None:
        try:
            rows_est, bytes_est, raw_plan = plan_cost(sql, mcp_explain_runner)
            mcp_explain_available = (
                rows_est is not None or bytes_est is not None or raw_plan is not None
            )
        except Exception:
            mcp_explain_available = False

    # ── Per-candidate wall-clock kill (best-effort) ──
    # mcp-trino may or may not persist SET SESSION across separate tool calls.
    # We emit it anyway; if the server ignores it, candidates that overshoot
    # baseline wall-time are also capped by the MCP request timeout below.
    baseline_wall_ms = float(baseline.metrics.wall_time_ms or baseline.metrics.query_time_ms or 0)
    candidate_timeout_ms = make_candidate_timeout_ms(baseline_wall_ms) if baseline_wall_ms > 0 else None
    if baseline_wall_ms > 0:
        timeout_sql = make_query_max_run_time_sql(baseline_wall_ms)
        try:
            _execute_via_mcp(client, timeout_sql)
            if output:
                output.progress(f"  Session property set: {timeout_sql}")
        except Exception as exc:
            if output:
                output.progress(f"  [dim]Session property emit failed (best-effort): {exc}[/dim]")
        if output and candidate_timeout_ms is not None:
            output.progress(
                f"  Candidate timeout: {candidate_timeout_ms / 1000.0:.1f}s "
                f"(baseline wall-time)"
            )

    # ── Long-query plan-cost loop ──
    # Avoid per-iteration real execution: EXPLAIN candidates first, then verify
    # only ranked survivors with real MCP queries.
    if long_query_opt_in and mcp_explain_available and max_iterations > 0:
        return _run_mcp_plan_cost_loop(
            client=client,
            provider=provider,
            model=model,
            reasoning=reasoning,
            original_sql=sql,
            metric_key=metric_key,
            max_iterations=max_iterations,
            verify_runs=verify_runs,
            output=output,
            build_prompt=build_prompt,
            baseline=baseline,
            static_report=static_report,
            explain_runner=mcp_explain_runner,
            max_fallbacks=fallbacks,
            candidate_timeout_ms=candidate_timeout_ms,
        )

    # ── EXPLAIN ANALYZE baseline ──
    original_explain: ExplainAnalyzeResult | None = None
    if output:
        output.progress("  Running EXPLAIN ANALYZE on baseline...")
    if output and hasattr(output, "status"):
        with output.status("baseline: explain analyze"):
            original_explain = _fetch_explain_analyze(client, sql)
    else:
        original_explain = _fetch_explain_analyze(client, sql)
    if output:
        if original_explain.available:
            output.progress(f"  EXPLAIN ANALYZE: {len(original_explain.stages)} stage(s), "
                          f"CPU={original_explain.total_cpu_ms:.0f}ms")
        else:
            output.progress("  EXPLAIN ANALYZE: unavailable (fallback to MCP metrics)")

    # ── Pre-execution diagnosis (v29 T2) ──
    # Combine static findings + plan-cost estimates + table metadata + the
    # baseline's actual peak memory into a ranked list of optimization
    # directions, then feed the top ones into the optimizer prompt so the LLM
    # works with a direction instead of brainstorming blind. Metadata fetched
    # here is reused by the post-loop suggestions block (single fetch).
    from genie.skills.mcp_trino.pre_execution_diagnosis import (
        format_directions_for_prompt,
    )
    from genie.skills.mcp_trino.rule_gate import (
        build_rule_gate_summary,
        format_rule_gate_for_prompt,
        render_rule_gate_summary,
    )

    directions, pre_table_metadata = _assemble_mcp_directions(
        client, sql, static_report,
        peak_memory_bytes=getattr(baseline.metrics, "peak_memory_bytes", 0) or None,
    )
    rule_gate = build_rule_gate_summary(static_report, directions)
    rule_gate_block = format_rule_gate_for_prompt(rule_gate)
    directions_block = format_directions_for_prompt(directions)
    if output:
        render_rule_gate_summary(output, rule_gate)
    if output and directions:
        output.progress(f"  Pre-execution diagnosis: {len(directions)} ranked direction(s) → prompt")

    # ── Session setup ──
    skill_prompt = build_prompt(True, model) if build_prompt else ""
    from genie.core.registry import SkillRegistry
    skill_instructions = SkillRegistry.get_instructions("mcp_trino")
    sys_prompt = (
        f"You are optimizing a Trino SQL query for performance.\n"
        f"Target metric: {metric_key} (lower is better).\n\n"
        f"Rules:\n"
        f"- Return the COMPLETE optimized SQL in a ```sql code block\n"
        f"- Do NOT use file_patch or any tool calls\n"
        f"- Keep the EXACT same result set — same columns, same rows, same values\n"
        f"- Make ONE focused change per iteration\n\n"
    )
    if rule_gate_block:
        sys_prompt += f"{rule_gate_block}\n\n"
    if skill_instructions:
        sys_prompt += f"## Trino Optimization Guide\n\n{skill_instructions}\n\n"
    if directions_block:
        sys_prompt += f"{directions_block}\n\n"
    sys_prompt += skill_prompt
    session = new_session(sys_prompt)

    best_sql = sql
    best_metric = baseline.median_metric
    best_measure = baseline
    iterations: list[IterationRecord] = []
    # v32 T1: cache of rendered direction blocks keyed by SQL. Seeded with the
    # original (already in the system prompt) so a stable best_sql is never
    # re-diagnosed; refreshed only when an improvement changes best_sql.
    rediag_cache: dict[str, str] = {sql: directions_block}

    # ── Iteration loop ──
    for iteration in range(1, max_iterations + 1):
        iter_start = time.monotonic()
        if output:
            output.print("")
            output.progress(f"── iteration {iteration}/{max_iterations}")

        last_str = "N/A (first iteration)"
        if iterations:
            last = iterations[-1]
            last_str = f"{last.status} (metric={last.metric_value:.1f}, delta={last.delta:+.1f})"

        # v32 T1: re-diagnose the CURRENT best_sql. The system-prompt directions
        # describe the original query; once an improvement changes best_sql they
        # go stale. Recompute at zero query cost (static + EXPLAIN FORMAT JSON;
        # table metadata reused) and feed fresh directions into this turn. Cached
        # by SQL, so an unchanged best_sql across iterations is not re-diagnosed.
        fresh_block = rediag_cache.get(best_sql)
        if fresh_block is None:
            try:
                _rd, _ = _assemble_mcp_directions(
                    client, best_sql, static_analyze(best_sql),
                    peak_memory_bytes=getattr(best_measure.metrics, "peak_memory_bytes", 0) or None,
                    table_metadata=pre_table_metadata or None,
                )
                fresh_block = format_directions_for_prompt(_rd)
            except Exception:
                fresh_block = ""
            rediag_cache[best_sql] = fresh_block
        # Only inject when the query has actually changed (iter 1 is already
        # covered by the system prompt) and the diagnosis produced directions.
        diag_line = f"{fresh_block}\n\n" if (fresh_block and best_sql != sql) else ""

        context = (
            f"[Trino Query Enhancement — Iteration {iteration}]\n"
            f"Target metric: {metric_key} (lower is better)\n"
            f"Baseline: {baseline.median_metric:.1f}\n"
            f"Current best: {best_metric:.1f}\n"
            f"Last iteration: {last_str}\n\n"
            f"Current SQL:\n```sql\n{best_sql}\n```\n\n"
            f"{diag_line}"
            f"Return the COMPLETE optimized SQL in a ```sql block. ONE change only. "
            f"Do NOT include a trailing semicolon."
        )

        # Keep history lean
        sys_msgs = [m for m in session["history"] if m["role"] == "system"]
        non_sys = [m for m in session["history"] if m["role"] != "system"]
        session["history"] = sys_msgs + non_sys[-4:]
        session["history"].append(new_msg("user", context))

        # Get AI response
        req = CompletionRequest(messages=session["history"], model=model, reasoning=reasoning)
        if output and hasattr(output, "status"):
            with output.status("AI thinking..."):
                reply = provider.complete_text(req)
        else:
            reply = provider.complete_text(req)

        if not reply:
            if output:
                output.error("  Empty AI response — stopping.")
            break

        session["history"].append(new_msg("assistant", reply))

        # Extract SQL
        candidate_sql = extract_sql_from_reply(reply)
        if not candidate_sql:
            _render_iteration_result(
                output, iteration=iteration, total=max_iterations,
                status="no_sql", hypothesis="no SQL extracted",
                metric_key=metric_key, metric_value=best_metric, delta=0.0,
                elapsed_s=time.monotonic() - iter_start,
                reason="no SQL extracted from model response",
            )
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

        # Show what the AI proposed (visible diff, not just "hypothesis")
        _render_sql_diff(output, best_sql, candidate_sql)

        # Execute and measure candidate
        try:
            candidate = _measure_mcp(client, candidate_sql, metric_key, verify_runs, capture_rows=True,
                                     output=output, label=f"iter {iteration} candidate",
                                     timeout_ms=candidate_timeout_ms)
        except CandidateTimeoutError as exc:
            elapsed = time.monotonic() - iter_start
            _render_iteration_result(
                output, iteration=iteration, total=max_iterations,
                status="timeout_worse", hypothesis=f"timeout: {exc}",
                metric_key=metric_key, metric_value=best_metric, delta=0.0,
                elapsed_s=elapsed,
                reason=str(exc),
            )
            session["history"].append(new_msg(
                "user",
                f"Query exceeded the baseline wall-time limit: {exc}. "
                f"Change REVERTED. Try a faster approach."
            ))
            iterations.append(IterationRecord(
                iteration=iteration, status="timeout_worse",
                metric_value=best_metric, delta=0.0,
                hypothesis=hypothesis, sql=candidate_sql,
            ))
            continue
        except Exception as exc:
            elapsed = time.monotonic() - iter_start
            _render_iteration_result(
                output, iteration=iteration, total=max_iterations,
                status="exec_failed", hypothesis=f"execution failed: {exc}",
                metric_key=metric_key, metric_value=best_metric, delta=0.0,
                elapsed_s=elapsed,
                reason=str(exc),
            )
            iterations.append(IterationRecord(
                iteration=iteration, status="exec_failed",
                metric_value=best_metric, delta=0.0,
                hypothesis=hypothesis, sql=candidate_sql,
            ))
            continue

        candidate_metric = candidate.median_metric
        delta = candidate_metric - best_metric

        # Check result equivalence — full row count first (truncation-safe),
        # then normalized content comparison on captured subset.
        if baseline.row_count != candidate.row_count:
            equiv = False
            equiv_reason = f"row count differs: {baseline.row_count} vs {candidate.row_count}"
        else:
            equiv, equiv_reason = _results_equivalent(baseline.rows, candidate.rows)
        if not equiv:
            elapsed = time.monotonic() - iter_start
            _render_iteration_result(
                output, iteration=iteration, total=max_iterations,
                status="semantic_drift", hypothesis=f"drift: {equiv_reason}",
                metric_key=metric_key, metric_value=candidate_metric, delta=delta,
                elapsed_s=elapsed,
                reason=f"semantic_drift: {equiv_reason}",
            )
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

        elapsed = time.monotonic() - iter_start
        _render_iteration_result(
            output, iteration=iteration, total=max_iterations,
            status=status, hypothesis=hypothesis,
            metric_key=metric_key, metric_value=candidate_metric, delta=delta,
            elapsed_s=elapsed,
            reason=None if improved else "not faster than current best",
        )

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

    # ── Direction efficacy (v32 T2) ──
    # Observational attribution: did each diagnosed direction's target metric
    # actually improve from baseline to the final best? Rendered so "directed
    # optimization" is measurable, not asserted. No causal claim — directions
    # sharing a metric are flagged co-attributed.
    from genie.skills.mcp_trino.pre_execution_diagnosis import (
        attribute_directions,
        format_attribution_report,
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

    direction_outcomes = attribute_directions(
        directions, _metrics_attr_map(baseline.metrics), _metrics_attr_map(best_measure.metrics)
    )
    attribution_block = format_attribution_report(direction_outcomes)
    if output and attribution_block:
        output.print("")
        for _line in attribution_block.splitlines():
            output.print(f"  {_line}")

    # ── EXPLAIN ANALYZE enhanced ──
    enhanced_explain: ExplainAnalyzeResult | None = None
    if best_sql != sql:
        if output:
            output.progress("  Running EXPLAIN ANALYZE on enhanced SQL...")
        if output and hasattr(output, "status"):
            with output.status("enhanced: explain analyze"):
                enhanced_explain = _fetch_explain_analyze(client, best_sql)
        else:
            enhanced_explain = _fetch_explain_analyze(client, best_sql)
        if output and enhanced_explain.available:
            output.progress(f"  Enhanced EXPLAIN: {len(enhanced_explain.stages)} stage(s), "
                          f"CPU={enhanced_explain.total_cpu_ms:.0f}ms")

    # ── Table metadata + suggestions ──
    table_suggestions: list[TableSuggestion] = []
    table_refs = _extract_table_names(sql)
    qualified_refs = [(c, s, t) for (c, s, t) in table_refs if c and s]
    had_qualified_tables = bool(qualified_refs)
    if qualified_refs and output:
        output.progress(f"  Fetching table metadata for {len(qualified_refs)} qualified table(s)...")
    elif table_refs and output:
        output.progress(f"  Skipping table metadata — no fully-qualified tables (use catalog.schema.table).")
    if qualified_refs:
        try:
            # Reuse metadata fetched for the pre-execution diagnosis when present
            # (same refs, same query) to avoid a second round-trip.
            metadata = pre_table_metadata if pre_table_metadata else _fetch_table_metadata(client, qualified_refs)
            table_suggestions = _generate_table_suggestions(metadata)
            if output and table_suggestions:
                output.progress(f"  Found {len(table_suggestions)} table suggestion(s).")
        except Exception:
            pass  # graceful skip

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
        table_suggestions=table_suggestions,
        had_qualified_tables=had_qualified_tables,
        original_explain=original_explain,
        enhanced_explain=enhanced_explain,
    )

    # Final visual summary
    _render_summary_card(
        output,
        baseline_value=baseline.median_metric,
        best_value=best_metric,
        metric_key=metric_key,
        improvement_abs=improvement_abs,
        improvement_pct=improvement_pct,
        data_consistent=final_equiv,
        data_consistency_reason=final_reason,
        iterations_ran=len(iterations),
    )

    return report


# ---------------------------------------------------------------------------
# Adapter for /trino-research auto-routing
# ---------------------------------------------------------------------------

# Metrics supported on the MCP path. `query_time_ms` is MCP-native; the rest
# map onto what the MCP server returns via cursor/REST stats.
MCP_METRICS = [
    "query_time_ms", "cpu_time_ms", "wall_time_ms",
    "physical_input_bytes", "processed_rows", "total_splits",
    "peak_memory_bytes",
]


RESEARCH_QUERY_TIMEOUT = 300  # seconds — bumped from default 30s for long-running queries


# ---------------------------------------------------------------------------
# UX helpers (v22 sprint)
# ---------------------------------------------------------------------------

def _fmt_metric_value(val: float) -> str:
    """Adaptive formatter for metric values (used in live output)."""
    if val is None:
        return "0"
    if val == 0:
        return "0"
    absv = abs(val)
    if absv < 0.001:
        return f"{val:.4f}"
    if absv < 1:
        return f"{val:.3f}"
    if absv < 100:
        return f"{val:.2f}"
    return f"{val:.0f}"


def _render_plan_card(
    output, *, sql: str, sql_source: str, metric: str, iterations: int,
    runs: int, server: str, safe_limit: Optional[int], query_timeout: int,
) -> None:
    """Pre-launch summary — tells the user exactly what is about to happen."""
    if output is None:
        return
    sql_lines = sql.count("\n") + 1
    sql_bytes = len(sql.encode("utf-8"))
    output.print("")
    output.print("  [bold cyan]── Research Plan ──[/bold cyan]")
    output.print(f"  [dim]sql         [/dim] {sql_source} ({sql_lines} lines, {sql_bytes:,}B)")
    output.print(f"  [dim]metric      [/dim] {metric} (lower is better)")
    output.print(f"  [dim]iterations  [/dim] {iterations}")
    output.print(f"  [dim]verify      [/dim] {runs} runs per candidate (median)")
    output.print(f"  [dim]server      [/dim] {server}")
    if safe_limit and safe_limit > 0:
        output.print(f"  [dim]safe-limit  [/dim] LIMIT {safe_limit} wrapper active")
    output.print(f"  [dim]timeout     [/dim] {query_timeout}s per query")
    # SQL preview (first 5 lines with syntax highlighting)
    preview_lines = sql.strip().splitlines()[:5]
    preview_text = "\n".join(preview_lines)
    if len(sql.strip().splitlines()) > 5:
        preview_text += "\n..."
    try:
        from rich.syntax import Syntax
        from rich.console import Console as _C
        _c = _C(force_terminal=True, highlight=False)
        syn = Syntax(preview_text, "sql", theme="monokai", line_numbers=False, padding=(0, 2))
        _c.print(syn)
    except Exception:
        output.print(f"  [dim]{preview_text}[/dim]")
    output.print("")


def _render_sql_diff(output, old_sql: str, new_sql: str, max_lines: int = 20) -> None:
    """Render a colored unified diff of the AI's proposed SQL vs current best."""
    if output is None:
        return
    import difflib
    if old_sql.strip() == new_sql.strip():
        output.print(f"  [dim](no SQL change)[/dim]")
        return
    diff = list(difflib.unified_diff(
        old_sql.splitlines(),
        new_sql.splitlines(),
        lineterm="",
        n=1,  # small context
    ))
    # Skip the file header lines ("---" / "+++")
    body = [ln for ln in diff if not ln.startswith("---") and not ln.startswith("+++")]
    if not body:
        return
    shown = body[:max_lines]
    output.print("  [dim]sql diff:[/dim]")
    for ln in shown:
        if ln.startswith("+"):
            output.print(f"    [green]{ln}[/green]")
        elif ln.startswith("-"):
            output.print(f"    [red]{ln}[/red]")
        elif ln.startswith("@@"):
            output.print(f"    [dim]{ln}[/dim]")
        else:
            output.print(f"    {ln}")
    if len(body) > max_lines:
        output.print(f"    [dim]... +{len(body) - max_lines} more lines[/dim]")


def _render_iteration_result(
    output, *, iteration: int, total: int, status: str, hypothesis: str,
    metric_key: str, metric_value: float, delta: float, elapsed_s: float,
    reason: str | None = None,
) -> None:
    """One structured line per iteration outcome. Uses the HumanSink palette."""
    if output is None:
        return
    color_by_status = {
        "improved": "green",
        "worse": "yellow",
        "semantic_drift": "red",
        "exec_failed": "red",
        "timeout_worse": "red",
        "no_sql": "dim",
    }
    label_by_status = {
        "improved": "KEPT",
        "worse": "WORSE",
        "semantic_drift": "REVERT",
        "exec_failed": "FAIL",
        "timeout_worse": "TIMEOUT",
        "no_sql": "SKIP",
    }
    color = color_by_status.get(status, "white")
    label = label_by_status.get(status, status.upper())

    def _clean(value: str, limit: int = 110) -> str:
        text = " ".join(str(value).split())
        if len(text) > limit:
            text = text[: limit - 3] + "..."
        return escape(text)

    metric = _fmt_metric_value(metric_value)
    delta_text = _fmt_metric_value(delta)
    elapsed_text = f"{elapsed_s:.1f}s"

    output.print(
        f"  [{color}]{label:<7}[/{color}] "
        f"[dim]iteration[/dim] {iteration}/{total}"
    )
    output.print(
        f"    [dim]metric [/dim] {metric_key:<18} {metric:>10}   "
        f"[dim]delta[/dim] {delta_text:>10}   "
        f"[dim]elapsed[/dim] {elapsed_text:>8}"
    )
    if reason:
        output.print(f"    [dim]reason [/dim] {_clean(reason)}")
    if hypothesis and hypothesis != "?":
        output.print(f"    [dim]note   [/dim] {_clean(hypothesis)}")


def _render_summary_card(
    output, *, baseline_value: float, best_value: float, metric_key: str,
    improvement_abs: float, improvement_pct: float, data_consistent: bool,
    data_consistency_reason: str, iterations_ran: int,
) -> None:
    """Final visual summary — bars scale to the larger of baseline/best."""
    if output is None:
        return
    bar_width = 30
    peak = max(abs(baseline_value), abs(best_value), 1e-9)
    def _bar(v: float) -> str:
        n = int(round(abs(v) / peak * bar_width))
        return "█" * max(n, 0)
    output.print("")
    output.print("  [bold cyan]── Final Result ──[/bold cyan]")
    output.print(f"  [dim]baseline   [/dim] {_fmt_metric_value(baseline_value):>10}  [cyan]{_bar(baseline_value)}[/cyan]")
    output.print(f"  [dim]best       [/dim] {_fmt_metric_value(best_value):>10}  [green]{_bar(best_value)}[/green]")
    arrow = "↓" if improvement_abs < 0 else ("↑" if improvement_abs > 0 else "·")
    improved = improvement_abs < 0
    change_color = "green" if improved else ("yellow" if improvement_abs == 0 else "red")
    output.print(
        f"  [dim]change     [/dim] "
        f"[{change_color}]{_fmt_metric_value(improvement_abs)} ({improvement_pct:+.1f}%) {arrow}[/{change_color}]"
    )
    dc_label = "[green]PASS[/green]" if data_consistent else f"[red]FAIL[/red] ({data_consistency_reason})"
    output.print(f"  [dim]data check [/dim] {dc_label}")
    output.print(f"  [dim]iterations [/dim] {iterations_ran} rounds")
    output.print("")


def run_trino_research_via_mcp(
    provider,
    cfg: dict,
    model: str,
    reasoning: str,
    output,
    build_prompt: Callable[..., str],
    *,
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
    """MCP-routed entry point for /trino-research.

    Mirrors `trino_query.research.run_trino_research` so chat.py can dispatch
    to either path via a single call signature.
    """
    mcp_cfg = load_mcp_config()
    if not mcp_cfg.enabled:
        output.error("  MCP Trino not enabled. Configure [mcp.trino] in ~/.genie/config.toml.")
        return

    # Bump timeout for research workloads; individual queries can be long.
    mcp_cfg.timeout = max(mcp_cfg.timeout, query_timeout or RESEARCH_QUERY_TIMEOUT)
    client = McpClient(mcp_cfg)

    # Reachability preflight — fall through to caller's direct fallback is
    # handled at chat.py; if we got here, caller already decided on MCP.
    try:
        client.list_tools()
    except Exception as exc:
        output.error(f"  MCP server unreachable at {mcp_cfg.url}: {exc}")
        return

    output.print("\n  [yellow]== Trino Query Optimization (MCP) ==[/yellow]")
    output.progress(f"  Server: {mcp_cfg.url}")

    # ── Get SQL ──
    if sql_file:
        sql = Path(sql_file).read_text().strip()
        output.progress(f"  SQL from file: {sql_file}")
    elif sql_text:
        sql = sql_text.strip()
    else:
        from genie.input import _read_paste_mode
        output.print("  [cyan]Paste SQL (Ctrl-D to finish):[/cyan]")
        sql = _read_paste_mode()

    if not sql:
        output.error("Empty SQL.")
        return

    # ── Pre-flight: read-only + size estimation ──
    from .preflight import run_preflight, apply_safe_limit, PreflightBudget

    def _explain_runner(s: str) -> Optional[str]:
        tool_name, _ = _resolve_query_tool(client)
        # Only run EXPLAIN if the server exposes an explain tool; otherwise skip
        tools = {t["name"] for t in client.list_tools()}
        explain_tool = next((n for n in ("explain", "explain_query", "trino_explain") if n in tools), None)
        if not explain_tool:
            return None
        try:
            return client.call_tool(explain_tool, {"sql": f"EXPLAIN (FORMAT JSON) {s}"})
        except Exception:
            return None

    report = run_preflight(sql, _explain_runner, PreflightBudget())
    if not report.ok:
        output.error(f"  Pre-flight rejected: {report.reason}")
        if report.estimated_rows or report.estimated_bytes:
            est = []
            if report.estimated_rows:
                est.append(f"rows~{report.estimated_rows:,}")
            if report.estimated_bytes:
                est.append(f"bytes~{report.estimated_bytes:,}")
            output.print(f"  [dim]Estimate: {', '.join(est)}[/dim]")
        return
    if report.estimated_rows or report.estimated_bytes:
        est = []
        if report.estimated_rows is not None:
            est.append(f"~{report.estimated_rows:,} rows")
        if report.estimated_bytes is not None:
            est.append(f"~{report.estimated_bytes:,} bytes")
        output.progress(f"  Pre-flight OK: {', '.join(est)}")
    else:
        output.progress(f"  Pre-flight OK: read-only verified (size estimate unavailable)")

    # ── Opt-in safe-limit wrap ──
    if safe_limit and safe_limit > 0:
        wrapped = apply_safe_limit(sql, safe_limit)
        output.progress(f"  --safe-limit {safe_limit}: wrapped SQL with LIMIT {safe_limit}")
        sql = wrapped

    output.print(f"  [dim]SQL: {sql[:80]}...[/dim]\n")

    # ── Get metric ──
    if not metric:
        from genie.input import _read_input
        output.print("  [yellow]Metric to minimize:[/yellow]")
        for i, m in enumerate(MCP_METRICS, 1):
            output.print(f"    [cyan]{i}[/cyan]. {m}")
        try:
            choice = _read_input("  Choose [1]: ").strip() or "1"
            idx = int(choice) - 1
            metric = MCP_METRICS[idx] if 0 <= idx < len(MCP_METRICS) else "query_time_ms"
        except (ValueError, EOFError, KeyboardInterrupt):
            metric = "query_time_ms"

    if metric not in MCP_METRICS:
        output.error(f"Unknown metric: {metric}. Use one of: {MCP_METRICS}")
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

    # ── Pre-launch plan card ──
    _render_plan_card(
        output,
        sql=sql,
        sql_source=sql_file or "stdin",
        metric=metric,
        iterations=iterations,
        runs=runs,
        server=mcp_cfg.url,
        safe_limit=safe_limit,
        query_timeout=mcp_cfg.timeout,
    )

    # ── Run MCP enhancement loop ──
    from .preflight import LongQueryAbort, NoDataDetected
    try:
        report = run_mcp_enhancement(
            client=client,
            sql=sql,
            metric_key=metric,
            max_iterations=iterations,
            verify_runs=runs,
            provider=provider,
            model=model,
            reasoning=reasoning,
            output=output,
            build_prompt=build_prompt,
            long_query_opt_in=long_query_opt_in,
            long_query_threshold_s=long_query_threshold_s,
            max_fallbacks=max_fallbacks,
            diagnose_only=diagnose_only,
        )
    except LongQueryAbort as lqa:
        # Message already printed by run_mcp_enhancement.
        # If a directed report rode along, write it to disk.
        if getattr(lqa, "report_markdown", None):
            try:
                report_dir = Path.cwd() / "report"
                report_dir.mkdir(parents=True, exist_ok=True)
                report_name = f"trino-research-diagnose-{datetime.now().strftime('%Y%m%d-%H%M%S')}.md"
                report_path = report_dir / report_name
                report_path.write_text(lqa.report_markdown)
                output.progress(f"\n  Directed report saved: {report_path}")
            except Exception as exc:
                output.error(f"  Failed to save directed report: {exc}")
        return
    except NoDataDetected as nd:
        # No-data dispatch fired: write static-analysis report instead of EnhancementReport.
        try:
            report_dir = Path.cwd() / "report"
            report_dir.mkdir(parents=True, exist_ok=True)
            report_name = f"trino-research-nodata-{datetime.now().strftime('%Y%m%d-%H%M%S')}.md"
            report_path = report_dir / report_name
            report_path.write_text(nd.result.get("report_markdown", ""))
            output.progress(f"\n  Report saved: {report_path}")
        except Exception as exc:
            output.error(f"  Failed to save no-data report: {exc}")
        return

    # Save report markdown (same pattern as direct path)
    try:
        report_md = generate_report(report)
        report_name = f"trino-research-mcp-{datetime.now().strftime('%Y%m%d-%H%M%S')}.md"
        report_path = Path.cwd() / report_name
        report_path.write_text(report_md)
        output.progress(f"\n  Report saved: {report_path}")
    except Exception as exc:
        output.error(f"  Failed to save report: {exc}")
