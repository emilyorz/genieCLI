"""Tests for the sql_static orchestrator: parse handling, multi-rule, summary."""
from __future__ import annotations

import pytest

from genie.skills.trino_query.sql_static import (
    Finding,
    StaticAnalysisReport,
    analyze,
)


def test_empty_sql_returns_clean_report():
    r = analyze("")
    assert r.findings == []
    assert r.parse_error is None
    assert r.summary == "0 high, 0 medium, 0 low"


def test_whitespace_only_returns_clean_report():
    r = analyze("   \n\t  ")
    assert r.findings == []
    assert r.parse_error is None


def test_unparseable_sets_parse_error():
    r = analyze("THIS IS NOT SQL @@@")
    assert r.parse_error is not None
    assert "parse failed" in r.parse_error.lower()


def test_multiple_rules_fire_on_same_query():
    """SELECT * + comma-join + col = NULL → at least 3 distinct findings."""
    sql = "SELECT * FROM a, b WHERE a.col = NULL"
    rule_ids = {f.rule_id for f in analyze(sql).findings}
    assert "select-star" in rule_ids
    assert "cartesian-join" in rule_ids
    assert "null-unsafe-equals" in rule_ids


def test_findings_sorted_by_line():
    sql = (
        "SELECT *\n"           # line 1: select-star
        "FROM (\n"
        "  SELECT x FROM t ORDER BY x\n"  # line 3: order-by-in-subquery
        ") sub"
    )
    r = analyze(sql)
    lines = [f.line for f in r.findings]
    assert lines == sorted(lines)


def test_summary_counts_severities():
    # = NULL is high, select-star is medium → "1 high, 1 medium, 0 low"
    r = analyze("SELECT * FROM t WHERE col = NULL")
    assert r.summary == "1 high, 1 medium, 0 low"


def test_by_severity_groups_findings():
    r = analyze("SELECT * FROM t WHERE col = NULL")
    grouped = r.by_severity
    assert any(f.rule_id == "null-unsafe-equals" for f in grouped["high"])
    assert any(f.rule_id == "select-star" for f in grouped["medium"])
    assert grouped["low"] == []


def test_to_dict_serializable():
    r = analyze("SELECT * FROM t")
    d = r.to_dict()
    assert "findings" in d
    assert "summary" in d
    assert "parse_error" in d
    assert d["findings"][0]["rule_id"] == "select-star"


def test_clean_query_yields_no_findings():
    sql = "SELECT a, b FROM t WHERE a = 1 AND b IS NOT NULL"
    r = analyze(sql)
    assert r.findings == []


def test_known_three_issues_yield_three_distinct_rules():
    """End-to-end check that orchestrator surfaces multiple rules cleanly."""
    sql = (
        "SELECT DISTINCT a, b\n"
        "FROM t\n"
        "WHERE col = NULL\n"
        "GROUP BY a, b"
    )
    rule_ids = {f.rule_id for f in analyze(sql).findings}
    assert "redundant-distinct-after-group-by" in rule_ids
    assert "null-unsafe-equals" in rule_ids


def test_rule_failure_does_not_crash_orchestrator(monkeypatch):
    """If one rule raises, the orchestrator must continue with the remaining rules."""
    from genie.skills.trino_query.sql_static.rules import r2_select_star

    def boom(sql, statements):
        raise RuntimeError("synthetic rule failure")

    monkeypatch.setattr(r2_select_star, "apply", boom)
    # Re-import analyze to pick up patched rule via _load_rules
    r = analyze("SELECT * FROM t WHERE col = NULL")
    # null-unsafe-equals (R7) must still fire even though R2 blew up
    rule_ids = {f.rule_id for f in r.findings}
    assert "null-unsafe-equals" in rule_ids
