"""Tests for genie.skills.trino_query.detection_scan.

Pure unit tests — no MCP, no cluster, no network.
"""
from __future__ import annotations

import pytest

from genie.skills.trino_query.detection_scan import DetectionFinding, scan_sql

VALID_ACTIONS = {"block", "rewrite", "advise", "pass"}


def test_scan_whole_query_flags_cartesian_join():
    """A CROSS JOIN should produce a finding with action 'block'."""
    sql = "SELECT a.id, b.name FROM table_a a CROSS JOIN table_b b"
    findings = scan_sql(sql)
    assert findings, "expected at least one finding"
    cartesian = [f for f in findings if f.rule_id == "cartesian-join"]
    assert cartesian, f"expected cartesian-join finding; got {[f.rule_id for f in findings]}"
    assert cartesian[0].action == "block", (
        f"cartesian-join should be 'block', got '{cartesian[0].action}'"
    )


def test_scan_fragment_where_predicate():
    """Bare `x = NULL` fragment -> null-unsafe-equals finding, line 1."""
    # R7 fires on `= NULL` / `!= NULL` literals — NOT on `IS NULL`.
    sql = "x = NULL"
    findings = scan_sql(sql)
    assert findings, "expected at least one finding for null-unsafe-equals fragment"
    null_findings = [f for f in findings if f.rule_id == "null-unsafe-equals"]
    assert null_findings, (
        f"expected null-unsafe-equals finding for 'x = NULL'; "
        f"got rule_ids={[f.rule_id for f in findings]}"
    )
    assert null_findings[0].line == 1, (
        f"fragment line number should be clamped to 1, got {null_findings[0].line}"
    )


def test_scan_fragment_is_null_does_not_trigger_r7():
    """IS NULL should NOT trigger R7 null-unsafe-equals."""
    sql = "x IS NULL"
    findings = scan_sql(sql)
    null_unsafe = [f for f in findings if f.rule_id == "null-unsafe-equals"]
    assert not null_unsafe, (
        f"IS NULL should not trigger R7; got {[f.rule_id for f in findings]}"
    )


def test_scan_clean_sql_returns_injected_pass():
    """Clean SQL -> exactly one finding with rule_id 'none' and action 'pass'."""
    sql = "SELECT a FROM t WHERE id = 1"
    findings = scan_sql(sql)
    assert len(findings) == 1, f"expected exactly one finding, got {len(findings)}: {findings}"
    assert findings[0].rule_id == "none", (
        f"expected rule_id 'none', got '{findings[0].rule_id}'"
    )
    assert findings[0].action == "pass", (
        f"expected action 'pass', got '{findings[0].action}'"
    )


def test_scan_parse_error_returns_advise_not_raise():
    """Garbage SQL -> one parse-error advise finding; no exception raised."""
    sql = ")))"
    findings = scan_sql(sql)  # must NOT raise
    assert len(findings) == 1, f"expected exactly one finding, got {len(findings)}"
    assert findings[0].rule_id == "parse-error", (
        f"expected 'parse-error', got '{findings[0].rule_id}'"
    )
    assert findings[0].action == "advise", (
        f"expected action 'advise', got '{findings[0].action}'"
    )


def test_scan_action_values_are_valid_tiers():
    """Every returned action must be one of block/rewrite/advise/pass."""
    test_cases = [
        "SELECT a FROM t WHERE id = 1",                            # pass
        "SELECT a, b FROM t CROSS JOIN s",                         # block
        "SELECT DISTINCT a FROM (SELECT a FROM t GROUP BY a) sub",  # rewrite
        "SELECT * FROM t",                                         # advise
        ")))",                                                      # parse-error/advise
    ]
    for sql in test_cases:
        findings = scan_sql(sql)
        for f in findings:
            assert f.action in VALID_ACTIONS, (
                f"unexpected action '{f.action}' in findings for: {sql!r}"
            )


def test_scan_returns_list_of_detection_findings():
    """scan_sql always returns a list of DetectionFinding instances."""
    findings = scan_sql("SELECT 1")
    assert isinstance(findings, list)
    for f in findings:
        assert isinstance(f, DetectionFinding)


def test_scan_empty_string_returns_pass():
    """Empty / whitespace SQL -> injected pass (no findings from static analysis)."""
    for sql in ["", "   ", "\n"]:
        findings = scan_sql(sql)
        assert isinstance(findings, list)
        # Should return a pass or empty — not raise
        for f in findings:
            assert f.action in VALID_ACTIONS
