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

from genie.core.sql_extraction import extract_sql_from_reply
from .client import McpClient, McpConfig, McpError, load_mcp_config

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

def _fetch_explain_analyze(client: McpClient, sql: str) -> ExplainAnalyzeResult:
    """Run EXPLAIN ANALYZE via MCP and parse the output.

    Returns ExplainAnalyzeResult with available=False if the query fails
    (e.g. MCP server doesn't support EXPLAIN ANALYZE, or the query errors out).
    This is the fallback-safe path — never raises.
    """
    explain_sql = f"EXPLAIN ANALYZE {sql}"
    try:
        result = _execute_via_mcp(client, explain_sql)
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

        # Parse CPU/wall time: "CPU: 1.23s" or "CPU: 123.00ms"
        cpu_match = re.search(r"CPU:\s*([\d.]+)(ms|s)", stripped, re.IGNORECASE)
        if cpu_match:
            val = float(cpu_match.group(1))
            if cpu_match.group(2).lower() == "s":
                val *= 1000
            current_stage["cpu_ms"] = val

        wall_match = re.search(r"(?:Scheduled|Wall):\s*([\d.]+)(ms|s)", stripped, re.IGNORECASE)
        if wall_match:
            val = float(wall_match.group(1))
            if wall_match.group(2).lower() == "s":
                val *= 1000
            current_stage["wall_ms"] = val

        # Parse memory: "Peak Memory: 1.5MB" or "Memory: 1234B"
        mem_match = re.search(r"(?:Peak\s+)?Memory:\s*([\d.]+)\s*(B|KB|MB|GB)", stripped, re.IGNORECASE)
        if mem_match:
            val = float(mem_match.group(1))
            unit = mem_match.group(2).upper()
            multiplier = {"B": 1, "KB": 1024, "MB": 1024**2, "GB": 1024**3}
            current_stage["memory_bytes"] = int(val * multiplier.get(unit, 1))

        # Parse rows: "Input: 1000 rows" / "Output: 100 rows"
        input_match = re.search(r"Input:\s*([\d,]+)\s*rows?", stripped, re.IGNORECASE)
        if input_match:
            current_stage["input_rows"] = int(input_match.group(1).replace(",", ""))

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


def _execute_via_mcp(client: McpClient, sql: str) -> dict:
    """Execute SQL via MCP server, return parsed result with timing."""
    tool_name, sql_param = _resolve_query_tool(client)
    t0 = time.monotonic()
    raw = client.call_tool(tool_name, {sql_param: sql})
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
                  runs: int, capture_rows: bool = False,
                  max_capture_rows: int = 100_000) -> MeasureResult:
    """Run SQL `runs` times via MCP, return median metric + all data.

    If captured row count exceeds max_capture_rows, rows are truncated to
    max_capture_rows to protect against OOM. Caller should treat the truncation
    as best-effort: equivalence comparison becomes partial.
    """
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
    "generated_by": "Generated by genieCLI mcp_trino research at",
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
    "generated_by": "由 genieCLI mcp_trino research 產生於",
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
        lines.append(f"| {attr} | {orig:.1f} | {enh:.1f} | {delta:+.1f} | {pct:+.1f}% |")

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
    else:
        lines.append(f"_({L['no_suggestions']})_")
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
                    f"| {s.get('cpu_ms', 0):.0f} "
                    f"| {s.get('wall_ms', 0):.0f} "
                    f"| {mem_str} "
                    f"| {s.get('input_rows', 0):,} "
                    f"| {s.get('output_rows', 0):,} |"
                )
            lines.append("")
        else:
            lines.append("```")
            # Truncate raw text to first 50 lines to keep report manageable
            raw_lines = explain.raw_text.split("\n")[:50]
            lines.append("\n".join(raw_lines))
            if len(explain.raw_text.split("\n")) > 50:
                lines.append("... (truncated)")
            lines.append("```")
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

    # ── EXPLAIN ANALYZE baseline ──
    original_explain: ExplainAnalyzeResult | None = None
    if output:
        output.progress("  Running EXPLAIN ANALYZE on baseline...")
    original_explain = _fetch_explain_analyze(client, sql)
    if output:
        if original_explain.available:
            output.progress(f"  EXPLAIN ANALYZE: {len(original_explain.stages)} stage(s), "
                          f"CPU={original_explain.total_cpu_ms:.0f}ms")
        else:
            output.progress("  EXPLAIN ANALYZE: unavailable (fallback to MCP metrics)")

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
    if skill_instructions:
        sys_prompt += f"## Trino Optimization Guide\n\n{skill_instructions}\n\n"
    sys_prompt += skill_prompt
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
        candidate_sql = extract_sql_from_reply(reply)
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

        # Check result equivalence — full row count first (truncation-safe),
        # then normalized content comparison on captured subset.
        if baseline.row_count != candidate.row_count:
            equiv = False
            equiv_reason = f"row count differs: {baseline.row_count} vs {candidate.row_count}"
        else:
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

    # ── EXPLAIN ANALYZE enhanced ──
    enhanced_explain: ExplainAnalyzeResult | None = None
    if best_sql != sql:
        if output:
            output.progress("  Running EXPLAIN ANALYZE on enhanced SQL...")
        enhanced_explain = _fetch_explain_analyze(client, best_sql)
        if output and enhanced_explain.available:
            output.progress(f"  Enhanced EXPLAIN: {len(enhanced_explain.stages)} stage(s), "
                          f"CPU={enhanced_explain.total_cpu_ms:.0f}ms")

    # ── Table metadata + suggestions ──
    table_suggestions: list[TableSuggestion] = []
    table_refs = _extract_table_names(sql)
    if table_refs and output:
        output.progress(f"  Fetching table metadata for {len(table_refs)} table(s)...")
    if table_refs:
        try:
            metadata = _fetch_table_metadata(client, table_refs)
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
        original_explain=original_explain,
        enhanced_explain=enhanced_explain,
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


# ---------------------------------------------------------------------------
# Adapter for /trino-research auto-routing
# ---------------------------------------------------------------------------

# Metrics supported on the MCP path. `query_time_ms` is MCP-native; the rest
# map onto what the MCP server returns via cursor/REST stats.
MCP_METRICS = [
    "query_time_ms", "cpu_time_ms", "wall_time_ms",
    "physical_input_bytes", "processed_rows", "total_splits",
]


RESEARCH_QUERY_TIMEOUT = 300  # seconds — bumped from default 30s for long-running queries


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

    # ── Run MCP enhancement loop ──
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
    )

    # Save report markdown (same pattern as direct path)
    try:
        report_md = generate_report(report)
        report_name = f"trino-research-mcp-{datetime.now().strftime('%Y%m%d-%H%M%S')}.md"
        report_path = Path.cwd() / report_name
        report_path.write_text(report_md)
        output.progress(f"\n  Report saved: {report_path}")
    except Exception as exc:
        output.error(f"  Failed to save report: {exc}")
