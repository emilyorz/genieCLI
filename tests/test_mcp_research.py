"""Tests for MCP Trino research (autoresearch enhancement) module."""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from genie.skills.mcp_trino.client import McpClient, McpConfig
from genie.skills.mcp_trino.research import (
    EnhancementReport,
    IterationRecord,
    MeasureResult,
    RunMetrics,
    _execute_via_mcp,
    _extract_sql_from_reply,
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
    def test_parses_json_response(self):
        mock_client = MagicMock(spec=McpClient)
        mock_client.call_tool.return_value = json.dumps({
            "rows": [{"id": 1}, {"id": 2}],
            "columns": ["id"],
            "duration_ms": 42,
            "metrics": {
                "cpu_time_ms": 10,
                "wall_time_ms": 15,
                "processed_rows": 2,
            }
        })
        result = _execute_via_mcp(mock_client, "SELECT id FROM t")
        assert result["row_count"] == 2
        assert result["columns"] == ["id"]
        assert result["metrics"].cpu_time_ms == 10
        assert result["error"] is None

    def test_handles_text_response(self):
        mock_client = MagicMock(spec=McpClient)
        mock_client.call_tool.return_value = "plain text result"
        result = _execute_via_mcp(mock_client, "SELECT 1")
        assert result["rows"] == []
        assert result["metrics"].query_time_ms > 0


# ── Report Generation ────────────────────────────────────────────────────────


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
            mcp_server_url="http://localhost:8811",
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
        assert "http://localhost:8811" in md
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
