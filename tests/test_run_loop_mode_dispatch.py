"""Tests for v28 mode dispatch in _run_optimization_loop + no-data path."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from genie.skills.mcp_trino.preflight import detect_no_data_reason
from genie.skills.trino_query.research import (
    _format_static_findings,
    _no_data_report,
    _run_no_data_path,
    _run_optimization_loop,
)
from genie.skills.trino_query.sql_static import analyze as static_analyze


# ── detect_no_data_reason ─────────────────────────────────────────────────────

def test_detect_returns_empty_result_for_zero_rows():
    assert detect_no_data_reason(baseline_row_count=0) == "empty_result"


def test_detect_returns_none_for_positive_rows():
    assert detect_no_data_reason(baseline_row_count=42) is None


def test_detect_table_not_found_marker():
    exc = Exception("Query failed: TABLE_NOT_FOUND for hive.default.foo")
    assert detect_no_data_reason(baseline_exc=exc) == "table_not_found"


def test_detect_schema_not_found_marker():
    exc = Exception("SCHEMA_NOT_FOUND: hive.missing")
    assert detect_no_data_reason(baseline_exc=exc) == "table_not_found"


def test_detect_does_not_misclassify_unrelated_errors():
    exc = ConnectionError("connection refused at trino:8080")
    assert detect_no_data_reason(baseline_exc=exc) is None


def test_detect_handles_does_not_exist_phrase():
    exc = Exception("Catalog 'foo' does not exist")
    assert detect_no_data_reason(baseline_exc=exc) == "table_not_found"


# ── _format_static_findings ───────────────────────────────────────────────────

def test_format_static_findings_empty():
    report = static_analyze("SELECT a, b FROM t WHERE a IS NOT NULL")
    assert _format_static_findings(report) == ""


def test_format_static_findings_renders_bullets():
    report = static_analyze("SELECT * FROM t WHERE col = NULL")
    text = _format_static_findings(report)
    assert "select-star" in text
    assert "null-unsafe-equals" in text
    assert "→" in text  # suggestion arrow


# ── _no_data_report ───────────────────────────────────────────────────────────

def test_no_data_report_marks_table_not_found():
    report = static_analyze("SELECT * FROM missing_table")
    md = _no_data_report(
        sql="SELECT * FROM missing_table",
        reason="table_not_found",
        static_report=report,
        llm_finishing=None,
        model="test-model",
    )
    assert "no-data path" in md
    assert "does not exist" in md
    assert "select-star" in md
    assert "Original SQL" in md


def test_no_data_report_marks_empty_result():
    report = static_analyze("SELECT a FROM t")
    md = _no_data_report(
        sql="SELECT a FROM t",
        reason="empty_result",
        static_report=report,
        llm_finishing=None,
        model="test-model",
    )
    assert "0 rows" in md


def test_no_data_report_includes_llm_finishing_when_provided():
    report = static_analyze("SELECT * FROM t")
    md = _no_data_report(
        sql="SELECT * FROM t",
        reason="empty_result",
        static_report=report,
        llm_finishing="Recommended: list columns explicitly.",
        model="test-model",
    )
    assert "LLM finishing pass" in md
    assert "list columns explicitly" in md


def test_no_data_report_handles_clean_sql_with_no_findings():
    report = static_analyze("SELECT a, b FROM t WHERE a IS NOT NULL")
    md = _no_data_report(
        sql="SELECT a, b FROM t WHERE a IS NOT NULL",
        reason="empty_result",
        static_report=report,
        llm_finishing=None,
        model="test-model",
    )
    assert "No structural issues detected" in md


# ── _run_no_data_path (provider mocking, no LLM call) ─────────────────────────

def test_run_no_data_path_returns_no_data_status():
    output = MagicMock()
    report = static_analyze("SELECT * FROM t")
    result = _run_no_data_path(
        provider=None,  # provider=None skips LLM finishing
        model="test-model",
        reasoning="default",
        original_sql="SELECT * FROM t",
        no_data_reason="empty_result",
        static_report=report,
        baseline_exc=None,
        output=output,
    )
    assert result["status"] == "no_data"
    assert result["reason"] == "empty_result"
    assert result["best_sql"] == "SELECT * FROM t"
    assert any(f["rule_id"] == "select-star" for f in result["static_findings"])
    assert "report_markdown" in result and result["report_markdown"]


def test_run_no_data_path_skips_llm_when_no_findings():
    output = MagicMock()
    provider = MagicMock()
    provider.complete_text.return_value = "SHOULD NOT BE CALLED"
    report = static_analyze("SELECT a FROM t WHERE a IS NOT NULL")
    result = _run_no_data_path(
        provider=provider,
        model="test-model",
        reasoning="default",
        original_sql="SELECT a FROM t WHERE a IS NOT NULL",
        no_data_reason="empty_result",
        static_report=report,
        baseline_exc=None,
        output=output,
    )
    assert result["llm_finishing"] is None
    provider.complete_text.assert_not_called()


def test_run_no_data_path_calls_llm_when_findings_present():
    output = MagicMock()
    provider = MagicMock()
    provider.complete_text.return_value = "Diagnosis: rewrite needed."
    report = static_analyze("SELECT * FROM t WHERE col = NULL")
    result = _run_no_data_path(
        provider=provider,
        model="test-model",
        reasoning="default",
        original_sql="SELECT * FROM t WHERE col = NULL",
        no_data_reason="empty_result",
        static_report=report,
        baseline_exc=None,
        output=output,
    )
    provider.complete_text.assert_called_once()
    assert result["llm_finishing"] == "Diagnosis: rewrite needed."
    assert "rewrite needed" in result["report_markdown"]


def test_run_no_data_path_survives_llm_exception():
    output = MagicMock()
    provider = MagicMock()
    provider.complete_text.side_effect = RuntimeError("LLM provider down")
    report = static_analyze("SELECT * FROM t")
    result = _run_no_data_path(
        provider=provider,
        model="test-model",
        reasoning="default",
        original_sql="SELECT * FROM t",
        no_data_reason="empty_result",
        static_report=report,
        baseline_exc=None,
        output=output,
    )
    # LLM crash must not break the report
    assert result["status"] == "no_data"
    assert result["llm_finishing"] is None


# ── _run_optimization_loop dispatch (mocking _measure) ────────────────────────

def test_loop_dispatches_to_no_data_when_baseline_returns_zero_rows():
    """row_count == 0 → no-data path runs, iteration loop never entered."""
    output = MagicMock()
    fake_baseline = {
        "median": 100.0,
        "samples": [100.0],
        "row_count": 0,
        "rows": [],
        "metrics": MagicMock(wall_time_ms=50, cpu_time_ms=40, total_splits=1, processed_rows=0),
    }
    with patch("genie.skills.trino_query.research._measure", return_value=fake_baseline):
        result = _run_optimization_loop(
            provider=None,
            model="test-model",
            reasoning="default",
            original_sql="SELECT * FROM t",
            metric_key="cpu_time_ms",
            max_iterations=5,
            verify_runs=1,
            output=output,
            build_prompt=lambda *a, **kw: "",
        )
    assert result["status"] == "no_data"
    assert result["reason"] == "empty_result"


def test_loop_dispatches_to_no_data_when_baseline_raises_table_not_found():
    output = MagicMock()
    exc = Exception("Query failed: TABLE_NOT_FOUND for catalog.schema.bogus")
    with patch("genie.skills.trino_query.research._measure", side_effect=exc):
        result = _run_optimization_loop(
            provider=None,
            model="test-model",
            reasoning="default",
            original_sql="SELECT * FROM bogus",
            metric_key="cpu_time_ms",
            max_iterations=5,
            verify_runs=1,
            output=output,
            build_prompt=lambda *a, **kw: "",
        )
    assert result["status"] == "no_data"
    assert result["reason"] == "table_not_found"
    assert "TABLE_NOT_FOUND" in (result.get("baseline_error") or "")


def test_loop_propagates_real_failure_when_not_no_data_shape():
    """Connection refused / generic errors must NOT be misclassified as no-data."""
    output = MagicMock()
    exc = ConnectionError("connection refused")
    with patch("genie.skills.trino_query.research._measure", side_effect=exc):
        result = _run_optimization_loop(
            provider=None,
            model="test-model",
            reasoning="default",
            original_sql="SELECT * FROM t",
            metric_key="cpu_time_ms",
            max_iterations=5,
            verify_runs=1,
            output=output,
            build_prompt=lambda *a, **kw: "",
        )
    assert result["status"] == "failed"
    assert "connection refused" in result["error"]
