"""Tests for the static-analysis one-line summary surfaced in /trino-research output."""
from __future__ import annotations

from genie.skills.trino_query.sql_static import (
    Finding,
    StaticAnalysisReport,
    analyze,
    summary_line,
)


def test_none_report_is_skipped():
    assert summary_line(None) == "skipped"


def test_parse_error_is_reported():
    line = summary_line(StaticAnalysisReport(parse_error="bad SQL"))
    assert line.startswith("parse error")
    assert "bad SQL" in line


def test_no_findings_says_no_issues():
    assert summary_line(StaticAnalysisReport(findings=[])) == "no issues found"


def test_findings_show_counts_and_sorted_rule_ids():
    report = StaticAnalysisReport(
        findings=[
            Finding("high", "cartesian-join", "x", "y", 1),
            Finding("low", "select-star", "x", "y", 2),
        ]
    )
    line = summary_line(report)
    assert "1 high" in line
    assert "1 low" in line
    # rule ids appear, de-duplicated and sorted
    assert "cartesian-join, select-star" in line


def test_summary_line_on_real_analyze_output_is_non_empty():
    report = analyze("SELECT DISTINCT a FROM t GROUP BY a")
    line = summary_line(report)
    assert "redundant-distinct-after-group-by" in line
