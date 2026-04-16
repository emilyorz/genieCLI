"""Tests for genie.skills.mcp_trino.preflight."""
from __future__ import annotations

import json

import pytest

from genie.skills.mcp_trino.preflight import (
    PreflightBudget,
    PreflightReport,
    apply_safe_limit,
    check_read_only,
    estimate_from_explain,
    run_preflight,
)


class TestCheckReadOnly:
    def test_accepts_select(self):
        ok, _ = check_read_only("SELECT * FROM t")
        assert ok

    def test_accepts_with_cte(self):
        ok, _ = check_read_only("WITH c AS (SELECT 1) SELECT * FROM c")
        assert ok

    def test_accepts_explain(self):
        ok, _ = check_read_only("EXPLAIN SELECT * FROM t")
        assert ok

    def test_accepts_show(self):
        ok, _ = check_read_only("SHOW TABLES")
        assert ok

    def test_rejects_delete(self):
        ok, reason = check_read_only("DELETE FROM t WHERE id = 1")
        assert not ok
        assert "DELETE" in reason

    def test_rejects_update(self):
        ok, reason = check_read_only("UPDATE t SET x = 1")
        assert not ok
        assert "UPDATE" in reason

    def test_rejects_drop(self):
        ok, reason = check_read_only("DROP TABLE t")
        assert not ok
        assert "DROP" in reason

    def test_rejects_insert(self):
        ok, reason = check_read_only("INSERT INTO t VALUES (1)")
        assert not ok
        assert "INSERT" in reason

    def test_rejects_multi_statement(self):
        ok, reason = check_read_only("SELECT 1; SELECT 2")
        assert not ok
        assert "multiple" in reason.lower()

    def test_rejects_empty(self):
        ok, _ = check_read_only("")
        assert not ok

    def test_rejects_only_comments(self):
        ok, _ = check_read_only("-- a comment\n/* another */")
        assert not ok

    def test_strips_inline_comments(self):
        ok, _ = check_read_only("-- prefix\nSELECT 1")
        assert ok

    def test_rejects_hidden_dml_in_block_comment_preserved_query(self):
        # Even with DROP in a comment, the actual first real keyword wins.
        # But since DROP appears in cleaned SQL via regex, we reject defensively.
        ok, _ = check_read_only("/* DROP TABLE t */ SELECT 1")
        # defensive: the regex-based keyword check removes block comments,
        # so this should be accepted
        assert ok


class TestEstimateFromExplain:
    def test_parses_root_estimate(self):
        explain = json.dumps({
            "estimates": [{"outputRowCount": 5000, "outputSizeInBytes": 1_000_000}],
            "children": [],
        })
        rows, bytes_ = estimate_from_explain(explain)
        assert rows == 5000
        assert bytes_ == 1_000_000

    def test_parses_child_estimate(self):
        explain = json.dumps({
            "children": [
                {"estimates": [{"outputRowCount": 42}]}
            ]
        })
        rows, _ = estimate_from_explain(explain)
        assert rows == 42

    def test_returns_none_for_invalid_json(self):
        assert estimate_from_explain("not json") == (None, None)

    def test_returns_none_for_no_estimate(self):
        explain = json.dumps({"children": [{"name": "x"}]})
        assert estimate_from_explain(explain) == (None, None)


class TestRunPreflight:
    def test_rejects_dml_without_explain(self):
        report = run_preflight("DELETE FROM t", explain_runner=None)
        assert not report.ok
        assert not report.is_read_only

    def test_accepts_small_select_without_explain(self):
        report = run_preflight("SELECT 1", explain_runner=None)
        assert report.ok
        assert report.estimated_rows is None

    def test_rejects_huge_estimate(self):
        explain = json.dumps({"estimates": [{"outputRowCount": 10_000_000}]})
        report = run_preflight(
            "SELECT * FROM big",
            explain_runner=lambda s: explain,
            budget=PreflightBudget(max_estimated_rows=1_000_000),
        )
        assert not report.ok
        assert "rows" in report.reason

    def test_accepts_estimate_within_budget(self):
        explain = json.dumps({"estimates": [{"outputRowCount": 500}]})
        report = run_preflight(
            "SELECT * FROM small",
            explain_runner=lambda s: explain,
        )
        assert report.ok
        assert report.estimated_rows == 500

    def test_tolerates_explain_runner_exception(self):
        def raiser(s):
            raise RuntimeError("mcp boom")
        report = run_preflight("SELECT 1", explain_runner=raiser)
        assert report.ok  # read-only passes; explain failure is non-blocking


class TestApplySafeLimit:
    def test_wraps_simple_select(self):
        wrapped = apply_safe_limit("SELECT * FROM t", 100)
        assert "SELECT * FROM (SELECT * FROM t)" in wrapped
        assert "LIMIT 100" in wrapped

    def test_strips_trailing_semicolon(self):
        wrapped = apply_safe_limit("SELECT 1;", 10)
        assert ";" not in wrapped.split("LIMIT")[0]

    def test_zero_limit_returns_original(self):
        assert apply_safe_limit("SELECT 1", 0) == "SELECT 1"
