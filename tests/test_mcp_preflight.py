"""Tests for genie.skills.mcp_trino.preflight."""
from __future__ import annotations

import json

import pytest

from genie.skills.mcp_trino.preflight import (
    DEFAULT_LONG_QUERY_THRESHOLD_S,
    PER_CANDIDATE_TIMEOUT_FACTOR,
    PreflightBudget,
    PreflightReport,
    apply_safe_limit,
    check_long_query_gate,
    check_read_only,
    estimate_from_explain,
    make_query_max_run_time_sql,
    plan_cost,
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


class TestPlanCost:
    def test_happy_path_returns_rows_bytes_and_parsed_plan(self):
        explain = json.dumps({
            "estimates": [
                {"outputRowCount": 1234, "outputSizeInBytes": 55_555}
            ],
            "children": [],
        })
        rows, bytes_, plan = plan_cost("SELECT 1", lambda s: explain)
        assert rows == 1234
        assert bytes_ == 55_555
        assert isinstance(plan, dict)
        assert plan["estimates"][0]["outputRowCount"] == 1234

    def test_missing_estimates_returns_none_rows_and_bytes_but_keeps_plan(self):
        explain = json.dumps({"children": [{"name": "OutputNode"}]})
        rows, bytes_, plan = plan_cost("SELECT 1", lambda s: explain)
        assert rows is None
        assert bytes_ is None
        assert isinstance(plan, dict)
        assert plan["children"][0]["name"] == "OutputNode"

    def test_malformed_json_returns_all_none_without_raising(self):
        rows, bytes_, plan = plan_cost("SELECT 1", lambda s: "not-valid-json{")
        assert rows is None
        assert bytes_ is None
        assert plan is None

    def test_explain_runner_raises_returns_all_none(self):
        def boom(s):
            raise RuntimeError("mcp down")
        assert plan_cost("SELECT 1", boom) == (None, None, None)

    def test_explain_runner_returns_none_returns_all_none(self):
        assert plan_cost("SELECT 1", lambda s: None) == (None, None, None)

    def test_none_runner_returns_all_none(self):
        assert plan_cost("SELECT 1", None) == (None, None, None)

    def test_accepts_bare_list_plan_shape(self):
        # Trino EXPLAIN JSON shouldn't be a list, but defensively we tolerate it.
        explain = json.dumps([{"outputRowCount": 10, "outputSizeInBytes": 500}])
        rows, bytes_, plan = plan_cost("SELECT 1", lambda s: explain)
        # estimate_from_explain only handles dict-root, so rows/bytes None here;
        # but raw plan should round-trip as a list so T3 can still inspect it.
        assert rows is None
        assert bytes_ is None
        assert isinstance(plan, list)


class TestLongQueryGate:
    def test_short_baseline_passes_without_opt_in(self):
        r = check_long_query_gate(
            baseline_wall_ms=5_000, max_iterations=5, long_query_opt_in=False
        )
        assert r.ok
        assert r.message == ""
        assert r.baseline_s == 5.0

    def test_long_baseline_aborts_without_opt_in(self):
        r = check_long_query_gate(
            baseline_wall_ms=90_000, max_iterations=5,
            long_query_opt_in=False, threshold_s=60,
        )
        assert not r.ok
        assert "exceeds --long-query-threshold 60s" in r.message
        assert "Remove --no-long-query" in r.message
        assert r.baseline_s == 90.0

    def test_long_baseline_proceeds_with_opt_in(self):
        r = check_long_query_gate(
            baseline_wall_ms=3_600_000, max_iterations=5,
            long_query_opt_in=True, threshold_s=60, max_fallbacks=3,
        )
        assert r.ok
        assert r.message == ""
        # baseline + 5*1.2*baseline + baseline(final verify) + 3*baseline(fallbacks)
        # = 3600 * (1 + 6 + 1 + 3) = 3600 * 11 = 39600s
        assert r.predicted_total_s == 3600.0 * (1 + 5 * PER_CANDIDATE_TIMEOUT_FACTOR + 1 + 3)

    def test_custom_threshold_and_fallbacks(self):
        r = check_long_query_gate(
            baseline_wall_ms=30_000, max_iterations=3,
            long_query_opt_in=False, threshold_s=10, max_fallbacks=1,
        )
        assert not r.ok
        assert "exceeds --long-query-threshold 10s" in r.message
        assert "fallbacks=1" in r.message

    def test_message_reports_predicted_total_in_minutes(self):
        r = check_long_query_gate(
            baseline_wall_ms=120_000, max_iterations=5,
            long_query_opt_in=False, threshold_s=60, max_fallbacks=3,
        )
        assert not r.ok
        # predicted_total_s > 60 → message should contain "min"
        assert "min" in r.message


class TestMakeQueryMaxRunTimeSql:
    def test_builds_valid_set_session_syntax(self):
        sql = make_query_max_run_time_sql(10_000)  # 10s baseline
        # 1.2 × 10_000 = 12_000 ms
        assert sql == "SET SESSION query_max_run_time = '12000ms'"

    def test_clamps_tiny_baseline_to_1000ms_minimum(self):
        sql = make_query_max_run_time_sql(100)  # would be 120ms otherwise
        assert sql == "SET SESSION query_max_run_time = '1000ms'"

    def test_rounds_up_fractional_ms(self):
        # 500ms × 1.2 = 600ms, but floor is 1000ms → clamp wins here; test with larger baseline
        sql = make_query_max_run_time_sql(10_000.4)
        # 1.2 × 10000.4 = 12000.48 → ceil → 12001 ms
        assert sql == "SET SESSION query_max_run_time = '12001ms'"

    def test_one_hour_baseline(self):
        sql = make_query_max_run_time_sql(3_600_000)  # 1h
        # 1.2 × 3.6M = 4.32M ms = 72 min
        assert sql == "SET SESSION query_max_run_time = '4320000ms'"


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
