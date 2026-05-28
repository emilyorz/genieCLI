"""Tests for MCP Trino research (autoresearch enhancement) module."""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from genie.skills.mcp_trino.client import McpClient, McpConfig
from genie.core.sql_extraction import extract_sql_from_reply as _extract_sql_from_reply
from genie.skills.mcp_trino.research import (
    ColumnInfo,
    EnhancementReport,
    ExplainAnalyzeResult,
    IterationRecord,
    MeasureResult,
    RunMetrics,
    TableMetadata,
    TableSuggestion,
    _execute_via_mcp,
    _extract_table_names,
    _fetch_explain_analyze,
    _fmt_metric_value,
    _generate_table_suggestions,
    _parse_explain_stages,
    _render_iteration_result,
    _render_plan_card,
    _render_sql_diff,
    _render_summary_card,
    _results_equivalent,
    generate_report,
)


# ── RunMetrics ───────────────────────────────────────────────────────────────


class TestRunMetrics:
    def test_summary(self):
        m = RunMetrics(query_time_ms=42.5, cpu_time_ms=30, wall_time_ms=45, processed_rows=100)
        s = m.summary()
        assert "query=42ms" in s or "query=43ms" in s
        assert "rows=100" in s


# ── Result Equivalence ───────────────────────────────────────────────────────


class TestResultsEquivalent:
    def test_identical_dicts(self):
        rows = [{"a": 1, "b": "x"}, {"a": 2, "b": "y"}]
        eq, reason = _results_equivalent(rows, rows)
        assert eq is True
        assert "match" in reason

    def test_different_row_count(self):
        eq, reason = _results_equivalent([{"a": 1}], [{"a": 1}, {"a": 2}])
        assert eq is False
        assert "row count" in reason

    def test_both_empty(self):
        eq, reason = _results_equivalent([], [])
        assert eq is True

    def test_different_values(self):
        rows_a = [{"a": 1}]
        rows_b = [{"a": 999}]
        eq, reason = _results_equivalent(rows_a, rows_b)
        assert eq is False


# ── SQL Extraction ───────────────────────────────────────────────────────────


class TestExtractSql:
    def test_sql_fence(self):
        reply = "Here's the optimized query:\n```sql\nSELECT a FROM t\n```\nDone."
        sql = _extract_sql_from_reply(reply)
        assert sql == "SELECT a FROM t"

    def test_strips_semicolon(self):
        reply = "```sql\nSELECT 1;\n```"
        sql = _extract_sql_from_reply(reply)
        assert sql == "SELECT 1"

    def test_generic_fence_with_sql(self):
        reply = "```\nSELECT a, b FROM t WHERE x = 1\n```"
        sql = _extract_sql_from_reply(reply)
        assert sql is not None
        assert "SELECT" in sql

    def test_no_sql(self):
        reply = "I think we should use a CTE approach."
        sql = _extract_sql_from_reply(reply)
        assert sql is None


# ── MCP Execution ────────────────────────────────────────────────────────────


class TestExecuteViaMcp:
    def setup_method(self):
        import genie.skills.mcp_trino.research as _mod
        _mod._resolved_tool = None

    def _mock_client(self, call_return):
        mock_client = MagicMock(spec=McpClient)
        mock_client.list_tools.return_value = [
            {"name": "query", "inputSchema": {"properties": {"sql": {"type": "string"}}}},
        ]
        mock_client.call_tool.return_value = call_return
        return mock_client

    def test_parses_json_response(self):
        mock_client = self._mock_client(json.dumps({
            "rows": [{"id": 1}, {"id": 2}],
            "columns": ["id"],
            "duration_ms": 42,
            "metrics": {
                "cpu_time_ms": 10,
                "wall_time_ms": 15,
                "processed_rows": 2,
            }
        }))
        result = _execute_via_mcp(mock_client, "SELECT id FROM t")
        assert result["row_count"] == 2
        assert result["columns"] == ["id"]
        assert result["metrics"].cpu_time_ms == 10
        assert result["error"] is None

    def test_handles_text_response(self):
        mock_client = self._mock_client("plain text result")
        result = _execute_via_mcp(mock_client, "SELECT 1")
        assert result["rows"] == []
        assert result["metrics"].query_time_ms > 0


# ── Report Generation ────────────────────────────────────────────────────────


class TestUxHelpers:
    """Render helpers added in v22 UX sprint."""

    def _mock_output(self):
        mock = MagicMock()
        mock._lines = []
        def _print(msg):
            mock._lines.append(str(msg))
        mock.print = _print
        return mock

    def test_fmt_metric_value_adaptive_precision(self):
        assert _fmt_metric_value(0) == "0"
        assert _fmt_metric_value(0.0005) == "0.0005"
        assert _fmt_metric_value(0.053) == "0.053"
        assert _fmt_metric_value(12.3) == "12.30"
        assert _fmt_metric_value(1234) == "1234"

    def test_render_sql_diff_shows_added_and_removed_lines(self):
        out = self._mock_output()
        _render_sql_diff(out, "SELECT * FROM t", "SELECT id FROM t WHERE id > 0")
        rendered = "\n".join(out._lines)
        assert "green" in rendered  # +added line colored
        assert "red" in rendered    # -removed line colored

    def test_render_sql_diff_noop_when_unchanged(self):
        out = self._mock_output()
        _render_sql_diff(out, "SELECT 1", "SELECT 1")
        assert any("no SQL change" in ln for ln in out._lines)

    def test_render_iteration_result_kept_status(self):
        out = self._mock_output()
        _render_iteration_result(
            out, iteration=1, total=5, status="improved",
            hypothesis="add partition filter", metric_key="query_time_ms",
            metric_value=0.053, delta=-0.012, elapsed_s=1.24,
        )
        rendered = "\n".join(out._lines)
        assert "KEPT" in rendered
        assert "green" in rendered
        assert "1/5" in rendered
        assert "0.053" in rendered

    def test_render_iteration_result_revert_status(self):
        out = self._mock_output()
        _render_iteration_result(
            out, iteration=2, total=5, status="semantic_drift",
            hypothesis="drop WHERE clause", metric_key="query_time_ms",
            metric_value=0.020, delta=-0.030, elapsed_s=1.5,
            reason="semantic_drift: row count differs: 5 vs 0",
        )
        rendered = "\n".join(out._lines)
        assert "REVERT" in rendered
        assert "red" in rendered
        assert 'reason="semantic_drift: row count differs: 5 vs 0"' in rendered

    def test_render_plan_card_shows_sql_stats(self):
        out = self._mock_output()
        _render_plan_card(
            out, sql="SELECT 1\nFROM t", sql_source="q.sql",
            metric="query_time_ms", iterations=5, runs=3,
            server="http://localhost:8811/mcp", safe_limit=None,
            query_timeout=300,
        )
        rendered = "\n".join(out._lines)
        assert "Research Plan" in rendered
        assert "q.sql" in rendered
        assert "2 lines" in rendered
        assert "http://localhost:8811/mcp" in rendered

    def test_render_plan_card_shows_safe_limit_when_set(self):
        out = self._mock_output()
        _render_plan_card(
            out, sql="SELECT 1", sql_source="stdin",
            metric="query_time_ms", iterations=3, runs=3,
            server="url", safe_limit=1000, query_timeout=300,
        )
        rendered = "\n".join(out._lines)
        assert "LIMIT 1000" in rendered

    def test_measure_mcp_shows_progress_via_status(self):
        """_measure_mcp should wrap each run in output.status when available."""
        from genie.skills.mcp_trino.research import _measure_mcp
        from contextlib import nullcontext

        calls = []
        class FakeOutput:
            def status(self, msg):
                calls.append(msg)
                return nullcontext()

        mock_client = MagicMock(spec=McpClient)
        mock_client.list_tools.return_value = [
            {"name": "query", "inputSchema": {"properties": {"sql": {"type": "string"}}}},
        ]
        mock_client.call_tool.return_value = json.dumps({
            "rows": [], "columns": [], "duration_ms": 1,
            # Non-zero server-side metrics so backfill doesn't trigger
            # extra EXPLAIN ANALYZE rounds in this status-coverage test.
            "metrics": {"cpu_time_ms": 1, "peak_memory_bytes": 1},
        })
        import genie.skills.mcp_trino.research as _mod
        _mod._resolved_tool = None

        out = FakeOutput()
        _measure_mcp(mock_client, "SELECT 1", "query_time_ms", runs=3,
                     output=out, label="baseline")
        assert len(calls) == 3
        assert "baseline: run 1/3" in calls[0]
        assert "baseline: run 3/3" in calls[2]

    def test_measure_mcp_applies_candidate_timeout_to_tool_call(self):
        """Candidate timeout should cap the MCP request and show the limit."""
        from genie.skills.mcp_trino.research import _measure_mcp
        from contextlib import nullcontext

        calls = []
        class FakeOutput:
            def status(self, msg):
                calls.append(msg)
                return nullcontext()

        mock_client = MagicMock(spec=McpClient)
        mock_client.list_tools.return_value = [
            {"name": "query", "inputSchema": {"properties": {"sql": {"type": "string"}}}},
        ]
        mock_client.call_tool.return_value = json.dumps({
            "rows": [], "columns": [], "duration_ms": 1,
            "metrics": {"cpu_time_ms": 1, "peak_memory_bytes": 1},
        })
        import genie.skills.mcp_trino.research as _mod
        _mod._resolved_tool = None

        _measure_mcp(
            mock_client, "SELECT 1", "query_time_ms", runs=1,
            output=FakeOutput(), label="iter 1 candidate", timeout_ms=12_345,
        )

        assert mock_client.call_tool.call_args.kwargs["timeout"] == 12.345
        assert "limit=12.3s" in calls[0]

    def test_execute_via_mcp_handles_bare_list_response(self):
        """mcp-trino returns rows as a top-level JSON list, not wrapped in {"rows": ...}.
        _execute_via_mcp must extract rows + infer columns from the list shape."""
        from genie.skills.mcp_trino.research import _execute_via_mcp

        mock_client = MagicMock(spec=McpClient)
        mock_client.list_tools.return_value = [
            {"name": "execute_query", "inputSchema": {"properties": {"query": {"type": "string"}}}},
        ]
        # Server returns a bare JSON list — mcp-trino's actual shape.
        mock_client.call_tool.return_value = json.dumps([
            {"col_a": 1, "col_b": "x"},
            {"col_a": 2, "col_b": "y"},
        ])
        import genie.skills.mcp_trino.research as _mod
        _mod._resolved_tool = None

        result = _execute_via_mcp(mock_client, "SELECT * FROM t")
        assert result["row_count"] == 2
        assert result["rows"] == [{"col_a": 1, "col_b": "x"}, {"col_a": 2, "col_b": "y"}]
        assert sorted(result["columns"]) == ["col_a", "col_b"]
        assert result["error"] is None

    def test_measure_mcp_backfills_metrics_from_explain_analyze(self):
        """When the MCP server returns rows but no structured stats,
        _measure_mcp should fall back to EXPLAIN ANALYZE and parse stage totals."""
        from genie.skills.mcp_trino.research import _measure_mcp

        # Server returns rows but empty metrics dict on raw SELECT,
        # and a Trino EXPLAIN ANALYZE plan text on the EXPLAIN ANALYZE call.
        explain_text = (
            "Trino version: 467\n"
            "Queued: 78us, Analysis: 225us, Planning: 4ms, Execution: 2.40s\n"
            "Fragment 1 [SINGLE]\n"
            "    CPU: 35.44us, Scheduled: 35.98us, Blocked 0.00ns "
            "(Input: 0.00ns, Output: 0.00ns), Input: 1 row (5B); per task: avg.: 1.00 std.dev.: 0.00, "
            "Output: 1 row (5B)\n"
            "    Peak Memory: 132B, Tasks count: 1; per task: max: 132B\n"
        )

        def call_tool_side_effect(tool_name, params):
            sql = params.get("sql") or params.get("query") or ""
            # Use bare-list response shape (mcp-trino's actual behavior)
            # to ensure backfill works end-to-end on the real server contract.
            if sql.upper().startswith("EXPLAIN ANALYZE"):
                return json.dumps([{"Query Plan": explain_text}])
            return json.dumps([{"x": 1}])

        mock_client = MagicMock(spec=McpClient)
        mock_client.list_tools.return_value = [
            {"name": "execute_query", "inputSchema": {"properties": {"query": {"type": "string"}}}},
        ]
        mock_client.call_tool.side_effect = call_tool_side_effect
        import genie.skills.mcp_trino.research as _mod
        _mod._resolved_tool = None

        result = _measure_mcp(mock_client, "SELECT 1", "cpu_time_ms", runs=2)

        # Median run's metrics should now have non-zero server-side fields
        # backfilled from the EXPLAIN ANALYZE parse.
        assert result.metrics.cpu_time_ms > 0, (
            f"cpu_time_ms should be backfilled from EA parse, got {result.metrics.cpu_time_ms}"
        )
        assert result.metrics.peak_memory_bytes > 0, (
            f"peak_memory_bytes should be backfilled, got {result.metrics.peak_memory_bytes}"
        )
        assert result.metrics.processed_rows > 0, (
            f"processed_rows should be backfilled, got {result.metrics.processed_rows}"
        )

        # Median sample uses the requested metric_key (cpu_time_ms here).
        assert result.median_metric > 0

        # 2 runs * (1 raw + 1 EA) = 4 call_tool invocations.
        assert mock_client.call_tool.call_count == 4

    def test_render_summary_card_shows_improvement(self):
        out = self._mock_output()
        _render_summary_card(
            out, baseline_value=1.0, best_value=0.4, metric_key="query_time_ms",
            improvement_abs=-0.6, improvement_pct=-60.0,
            data_consistent=True, data_consistency_reason="exact match",
            iterations_ran=5,
        )
        rendered = "\n".join(out._lines)
        assert "Final Result" in rendered
        assert "↓" in rendered  # improvement arrow
        assert "PASS" in rendered
        assert "█" in rendered  # visual bar


class TestGenerateReport:
    def _make_report(self) -> EnhancementReport:
        return EnhancementReport(
            timestamp="2026-04-13 14:00:00",
            original_sql="SELECT * FROM t",
            original_result_sample=[{"id": 1, "name": "a"}, {"id": 2, "name": "b"}],
            original_columns=["id", "name"],
            original_row_count=2,
            original_metrics=RunMetrics(query_time_ms=100, cpu_time_ms=80, wall_time_ms=120,
                                         processed_rows=2),
            enhanced_sql="SELECT id, name FROM t",
            enhanced_result_sample=[{"id": 1, "name": "a"}, {"id": 2, "name": "b"}],
            enhanced_columns=["id", "name"],
            enhanced_row_count=2,
            enhanced_metrics=RunMetrics(query_time_ms=60, cpu_time_ms=40, wall_time_ms=70,
                                         processed_rows=2),
            metric_key="query_time_ms",
            baseline_value=100.0,
            best_value=60.0,
            improvement_abs=-40.0,
            improvement_pct=-40.0,
            iterations=[
                IterationRecord(iteration=1, status="improved", metric_value=60.0,
                                delta=-40.0, hypothesis="Replace SELECT * with named columns"),
            ],
            data_consistent=True,
            data_consistency_reason="exact match",
            mcp_server_url="http://localhost:8811/mcp",
            verify_runs=3,
        )

    def test_report_has_all_sections(self):
        report = self._make_report()
        md = generate_report(report)
        assert "# Trino Query Enhancement Report" in md
        assert "## Meta" in md
        assert "## Performance Comparison" in md
        assert "## Summary" in md
        assert "## Iteration History" in md
        assert "## Original SQL" in md
        assert "## Original Result (sample)" in md
        assert "## Enhanced SQL" in md
        assert "## Enhanced Result (sample)" in md

    def test_report_contains_data(self):
        report = self._make_report()
        md = generate_report(report)
        assert "SELECT * FROM t" in md
        assert "SELECT id, name FROM t" in md
        assert "http://localhost:8811/mcp" in md
        assert "query_time_ms" in md
        assert "exact match" in md
        assert "YES" in md  # data_consistent

    def test_report_table_structure_is_fixed(self):
        """Verify that the report uses consistent table headers."""
        report = self._make_report()
        md = generate_report(report)
        # Performance comparison table
        assert "| Metric | Original | Enhanced | Delta | Change % |" in md
        # Summary table
        assert "| Field | Value |" in md
        # Iteration history table
        assert "| Round | Status | Metric Value | Delta | Hypothesis |" in md

    def test_no_improvement_shows_unchanged(self):
        report = self._make_report()
        report.enhanced_sql = report.original_sql  # no change
        md = generate_report(report)
        assert "no improvement found" in md.lower()

    def test_report_deterministic(self):
        """Same input produces identical output."""
        report = self._make_report()
        md1 = generate_report(report)
        md2 = generate_report(report)
        assert md1 == md2

    def test_report_has_table_suggestions_section(self):
        report = self._make_report()
        md = generate_report(report)
        assert "## Table Structure Suggestions" in md

    def test_report_shows_suggestions_when_present(self):
        report = self._make_report()
        report.table_suggestions = [
            TableSuggestion(
                table="cat.sch.orders",
                category="partition",
                suggestion="Consider partitioning by order_date.",
                suggestion_zh="建議使用 order_date 進行分區。",
                severity="warning",
            ),
        ]
        md = generate_report(report)
        assert "cat.sch.orders" in md
        assert "partition" in md
        assert "Consider partitioning" in md

    def test_report_no_suggestions_message(self):
        report = self._make_report()
        report.table_suggestions = []
        report.had_qualified_tables = True
        md = generate_report(report)
        assert "No table structure issues detected" in md

    def test_report_unqualified_tables_hint(self):
        report = self._make_report()
        report.table_suggestions = []
        report.had_qualified_tables = False
        md = generate_report(report)
        assert "No fully-qualified tables" in md


# ── Chinese Locale ──────────────────────────────────────────────────────────


class TestChineseLocale:
    def _make_report(self) -> EnhancementReport:
        return EnhancementReport(
            timestamp="2026-04-13 14:00:00",
            original_sql="SELECT * FROM t",
            original_result_sample=[],
            original_columns=[],
            original_row_count=0,
            original_metrics=RunMetrics(query_time_ms=100),
            enhanced_sql="SELECT id FROM t",
            enhanced_result_sample=[],
            enhanced_columns=[],
            enhanced_row_count=0,
            enhanced_metrics=RunMetrics(query_time_ms=60),
            metric_key="query_time_ms",
            baseline_value=100.0,
            best_value=60.0,
            improvement_abs=-40.0,
            improvement_pct=-40.0,
            iterations=[],
            data_consistent=True,
            data_consistency_reason="exact match",
            mcp_server_url="http://localhost:8811/mcp",
            verify_runs=3,
        )

    def test_zh_headers(self):
        report = self._make_report()
        md = generate_report(report, locale="zh")
        assert "# Trino 查詢優化報告" in md
        assert "## 基本資訊" in md
        assert "## 效能比較" in md
        assert "## 摘要" in md
        assert "## 迭代歷程" in md
        assert "## 原始 SQL" in md
        assert "## 優化後 SQL" in md
        assert "## 表結構優化建議" in md

    def test_zh_summary_labels(self):
        report = self._make_report()
        md = generate_report(report, locale="zh")
        assert "基線" in md
        assert "最佳" in md
        assert "改善" in md
        assert "越低越好" in md

    def test_zh_sql_stays_english(self):
        report = self._make_report()
        md = generate_report(report, locale="zh")
        assert "SELECT * FROM t" in md
        assert "SELECT id FROM t" in md

    def test_zh_metrics_stay_english(self):
        report = self._make_report()
        md = generate_report(report, locale="zh")
        assert "query_time_ms" in md

    def test_zh_suggestion_uses_chinese_text(self):
        report = self._make_report()
        report.table_suggestions = [
            TableSuggestion(
                table="cat.sch.t",
                category="partition",
                suggestion="Consider partitioning by date.",
                suggestion_zh="建議使用日期進行分區。",
                severity="warning",
            ),
        ]
        md = generate_report(report, locale="zh")
        assert "建議使用日期進行分區" in md
        assert "Consider partitioning" not in md

    def test_en_is_default(self):
        report = self._make_report()
        md = generate_report(report)
        assert "# Trino Query Enhancement Report" in md
        assert "Trino 查詢優化報告" not in md


# ── Table Name Extraction ───────────────────────────────────────────────────


class TestExtractTableNames:
    def test_simple_select(self):
        tables = _extract_table_names("SELECT a FROM my_catalog.my_schema.orders")
        assert ("my_catalog", "my_schema", "orders") in tables

    def test_unqualified_table(self):
        tables = _extract_table_names("SELECT a FROM orders")
        assert any(t[2] == "orders" for t in tables)

    def test_join_multiple_tables(self):
        sql = "SELECT a FROM cat.sch.orders o JOIN cat.sch.items i ON o.id = i.order_id"
        tables = _extract_table_names(sql)
        names = {t[2] for t in tables}
        assert "orders" in names
        assert "items" in names

    def test_subquery(self):
        sql = "SELECT * FROM (SELECT id FROM cat.sch.events) e"
        tables = _extract_table_names(sql)
        assert any(t[2] == "events" for t in tables)

    def test_invalid_sql_returns_empty(self):
        tables = _extract_table_names("THIS IS NOT SQL AT ALL <<<>>>")
        assert tables == []

    def test_cte(self):
        sql = "WITH cte AS (SELECT id FROM cat.sch.users) SELECT * FROM cte"
        tables = _extract_table_names(sql)
        names = {t[2] for t in tables}
        assert "users" in names


# ── Table Suggestions ───────────────────────────────────────────────────────


class TestGenerateTableSuggestions:
    def test_no_partition_suggests_partition(self):
        meta = [TableMetadata(
            catalog="cat", schema="sch", table_name="events",
            columns=[
                ColumnInfo("event_id", "bigint", "NO", 1),
                ColumnInfo("event_date", "date", "NO", 2),
                ColumnInfo("payload", "varchar", "YES", 3),
            ],
            properties={"partitioning": "[]"},
        )]
        suggestions = _generate_table_suggestions(meta)
        partition_sugs = [s for s in suggestions if s.category == "partition"]
        assert len(partition_sugs) == 1
        assert "event_date" in partition_sugs[0].suggestion
        assert "event_date" in partition_sugs[0].suggestion_zh

    def test_no_bucket_suggests_bucket(self):
        meta = [TableMetadata(
            catalog="cat", schema="sch", table_name="orders",
            columns=[
                ColumnInfo("order_id", "bigint", "NO", 1),
                ColumnInfo("customer_id", "bigint", "NO", 2),
            ],
            properties={},
        )]
        suggestions = _generate_table_suggestions(meta)
        bucket_sugs = [s for s in suggestions if s.category == "bucket"]
        assert len(bucket_sugs) == 1

    def test_unbounded_varchar_id_suggests_length(self):
        meta = [TableMetadata(
            catalog="cat", schema="sch", table_name="products",
            columns=[
                ColumnInfo("product_id", "varchar", "NO", 1),
                ColumnInfo("name", "varchar", "YES", 2),
            ],
            properties={},
        )]
        suggestions = _generate_table_suggestions(meta)
        type_sugs = [s for s in suggestions if s.category == "data_type"]
        assert len(type_sugs) == 1
        assert "product_id" in type_sugs[0].suggestion

    def test_double_amount_suggests_decimal(self):
        meta = [TableMetadata(
            catalog="cat", schema="sch", table_name="transactions",
            columns=[
                ColumnInfo("amount", "double", "NO", 1),
            ],
            properties={},
        )]
        suggestions = _generate_table_suggestions(meta)
        type_sugs = [s for s in suggestions if s.category == "data_type"]
        assert len(type_sugs) == 1
        assert "DECIMAL" in type_sugs[0].suggestion

    def test_wide_table_no_sort_suggests_sort(self):
        cols = [ColumnInfo(f"col_{i}", "varchar", "YES", i) for i in range(15)]
        meta = [TableMetadata(
            catalog="cat", schema="sch", table_name="wide",
            columns=cols,
            properties={"sorted_by": "[]"},
        )]
        suggestions = _generate_table_suggestions(meta)
        sort_sugs = [s for s in suggestions if s.category == "sort"]
        assert len(sort_sugs) == 1

    def test_no_suggestions_when_everything_configured(self):
        meta = [TableMetadata(
            catalog="cat", schema="sch", table_name="good_table",
            columns=[
                ColumnInfo("id", "bigint", "NO", 1),
                ColumnInfo("name", "varchar(100)", "YES", 2),
            ],
            properties={"partitioning": "[day(created)]", "bucket_count": "16",
                         "sorted_by": "[id]"},
        )]
        suggestions = _generate_table_suggestions(meta)
        assert len(suggestions) == 0


# ── EXPLAIN ANALYZE Parsing ─────────────────────────────────────────────────


class TestParseExplainStages:
    def test_parses_fragment_with_metrics(self):
        text = (
            "Fragment 0 [SINGLE]\n"
            "    CPU: 123.00ms, Scheduled: 200.00ms, Blocked: 0.00ms\n"
            "    Peak Memory: 1.5MB\n"
            "    Input: 1000 rows (50KB), Output: 100 rows (5KB)\n"
        )
        stages = _parse_explain_stages(text)
        assert len(stages) == 1
        assert stages[0]["id"] == 0
        assert stages[0]["cpu_ms"] == 123.0
        assert stages[0]["wall_ms"] == 200.0
        assert stages[0]["memory_bytes"] == int(1.5 * 1024 * 1024)
        assert stages[0]["input_rows"] == 1000
        assert stages[0]["output_rows"] == 100

    def test_parses_multiple_fragments(self):
        text = (
            "Fragment 0 [SINGLE]\n"
            "    CPU: 10.00ms, Scheduled: 20.00ms\n"
            "    Input: 100 rows\n"
            "Fragment 1 [HASH]\n"
            "    CPU: 50.00ms, Scheduled: 80.00ms\n"
            "    Input: 5000 rows\n"
        )
        stages = _parse_explain_stages(text)
        assert len(stages) == 2
        assert stages[0]["id"] == 0
        assert stages[1]["id"] == 1
        assert stages[1]["cpu_ms"] == 50.0

    def test_handles_seconds_unit(self):
        text = (
            "Fragment 0 [SINGLE]\n"
            "    CPU: 1.23s, Scheduled: 2.00s\n"
        )
        stages = _parse_explain_stages(text)
        assert stages[0]["cpu_ms"] == 1230.0
        assert stages[0]["wall_ms"] == 2000.0

    def test_empty_text_returns_empty(self):
        assert _parse_explain_stages("") == []

    def test_no_fragments_returns_empty(self):
        text = "Query plan without fragment headers\nSome random text"
        assert _parse_explain_stages(text) == []

    def test_handles_microseconds_unit(self):
        """Trino 467 uses `us` (microseconds) in EXPLAIN ANALYZE output."""
        text = (
            "Fragment 1 [SINGLE]\n"
            "    CPU: 52.94us, Scheduled: 54.05us\n"
            "    Peak Memory: 132B\n"
            "    Input: 1 row (5B), Output: 1 row (5B)\n"
        )
        stages = _parse_explain_stages(text)
        assert stages[0]["cpu_ms"] == pytest.approx(0.05294)
        assert stages[0]["wall_ms"] == pytest.approx(0.05405)
        assert stages[0]["memory_bytes"] == 132
        assert stages[0]["input_rows"] == 1
        assert stages[0]["output_rows"] == 1

    def test_handles_nanoseconds_unit(self):
        text = (
            "Fragment 0 [SINGLE]\n"
            "    CPU: 500.00ns\n"
        )
        stages = _parse_explain_stages(text)
        assert stages[0]["cpu_ms"] == pytest.approx(0.0005)

    def test_first_match_wins_within_stage(self):
        """Nested operator metrics must not overwrite fragment-level aggregates."""
        text = (
            "Fragment 1 [SINGLE]\n"
            "    CPU: 52.94us, Scheduled: 54.05us, Input: 10 rows, Output: 10 rows\n"
            "    Peak Memory: 132B\n"
            "    Values[]\n"
            "        CPU: 0.00ns, Scheduled: 0.00ns, Output: 1 row (5B)\n"
            "        Input avg.: 1.00 rows\n"
        )
        stages = _parse_explain_stages(text)
        # Fragment-level 52.94us must survive the operator-level 0.00ns
        assert stages[0]["cpu_ms"] == pytest.approx(0.05294)
        assert stages[0]["wall_ms"] == pytest.approx(0.05405)
        assert stages[0]["memory_bytes"] == 132
        assert stages[0]["input_rows"] == 10
        assert stages[0]["output_rows"] == 10


class TestFetchExplainAnalyze:
    def test_returns_unavailable_on_error(self):
        mock_client = MagicMock(spec=McpClient)
        mock_client.call_tool.return_value = json.dumps({"error": "not supported"})
        result = _fetch_explain_analyze(mock_client, "SELECT 1")
        assert result.available is False

    def test_returns_unavailable_on_exception(self):
        mock_client = MagicMock(spec=McpClient)
        mock_client.call_tool.side_effect = RuntimeError("connection lost")
        result = _fetch_explain_analyze(mock_client, "SELECT 1")
        assert result.available is False

    def test_parses_successful_explain(self):
        mock_client = MagicMock(spec=McpClient)
        explain_output = (
            "Fragment 0 [SINGLE]\n"
            "    CPU: 50.00ms, Scheduled: 80.00ms\n"
            "    Peak Memory: 2.0MB\n"
            "    Input: 500 rows, Output: 50 rows\n"
        )
        mock_client.call_tool.return_value = json.dumps({
            "rows": [{"Query Plan": explain_output}],
            "columns": ["Query Plan"],
        })
        result = _fetch_explain_analyze(mock_client, "SELECT 1")
        assert result.available is True
        assert len(result.stages) == 1
        assert result.total_cpu_ms == 50.0


class TestExplainInReport:
    def _make_report_with_explain(self) -> EnhancementReport:
        return EnhancementReport(
            timestamp="2026-04-13 23:30:00",
            original_sql="SELECT * FROM t",
            original_result_sample=[],
            original_columns=[],
            original_row_count=0,
            original_metrics=RunMetrics(query_time_ms=100),
            enhanced_sql="SELECT id FROM t",
            enhanced_result_sample=[],
            enhanced_columns=[],
            enhanced_row_count=0,
            enhanced_metrics=RunMetrics(query_time_ms=60),
            metric_key="query_time_ms",
            baseline_value=100.0,
            best_value=60.0,
            improvement_abs=-40.0,
            improvement_pct=-40.0,
            iterations=[],
            data_consistent=True,
            data_consistency_reason="exact match",
            mcp_server_url="http://localhost:8811/mcp",
            verify_runs=3,
            original_explain=ExplainAnalyzeResult(
                raw_text="Fragment 0...",
                stages=[{"id": 0, "cpu_ms": 50, "wall_ms": 80, "memory_bytes": 2097152,
                         "input_rows": 500, "output_rows": 50}],
                total_cpu_ms=50, total_wall_ms=80, total_memory_bytes=2097152,
                total_input_rows=500, total_output_rows=50,
                available=True,
            ),
            enhanced_explain=ExplainAnalyzeResult(
                raw_text="Fragment 0...",
                stages=[{"id": 0, "cpu_ms": 20, "wall_ms": 30, "memory_bytes": 1048576,
                         "input_rows": 500, "output_rows": 50}],
                total_cpu_ms=20, total_wall_ms=30, total_memory_bytes=1048576,
                total_input_rows=500, total_output_rows=50,
                available=True,
            ),
        )

    def test_report_has_explain_section(self):
        report = self._make_report_with_explain()
        md = generate_report(report)
        assert "## EXPLAIN ANALYZE" in md
        assert "### Original Query Plan" in md
        assert "### Enhanced Query Plan" in md

    def test_report_shows_stage_table(self):
        report = self._make_report_with_explain()
        md = generate_report(report)
        assert "| 0 |" in md
        assert "500" in md

    def test_report_unavailable_explain(self):
        report = self._make_report_with_explain()
        report.original_explain = ExplainAnalyzeResult(
            raw_text="failed", available=False,
        )
        report.enhanced_explain = None
        md = generate_report(report)
        assert "not available" in md.lower()

    def test_zh_explain_headers(self):
        report = self._make_report_with_explain()
        md = generate_report(report, locale="zh")
        assert "原始查詢計畫" in md
        assert "優化後查詢計畫" in md
