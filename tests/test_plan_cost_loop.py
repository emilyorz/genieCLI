"""Tests for v28 T4: plan-cost iteration loop with L1 structural guard + K-retry.

Mocks the LLM provider, EXPLAIN runner, and `_measure` so tests run without a
live Trino server. Each test asserts a single algorithmic property of the
plan-cost loop.
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from genie.skills.trino_query.research import (
    _run_optimization_loop,
    _run_plan_cost_loop,
)


# ── Test fixtures ─────────────────────────────────────────────────────────────

def _make_baseline(rows=100, wall_ms=80_000, samples=None):
    """Synthesise a baseline _measure result dict."""
    metrics = MagicMock(
        wall_time_ms=wall_ms, cpu_time_ms=wall_ms,
        total_splits=4, processed_rows=rows,
    )
    metrics.cpu_time_ms = wall_ms
    metrics.wall_time_ms = wall_ms
    return {
        "median": float(wall_ms),
        "samples": samples or [float(wall_ms)],
        "row_count": rows,
        "rows": [(i,) for i in range(rows)],
        "metrics": metrics,
    }


def _make_explain(rows_est=1000, bytes_est=8000, table="hive.default.t"):
    """Build a Trino-like EXPLAIN JSON with plan signature pinned to one table."""
    return json.dumps({
        "name": f"TableScan[{table}]",
        "descriptor": {"table": table},
        "estimates": [{"outputRowCount": rows_est, "outputSizeInBytes": bytes_est}],
        "children": [],
    })


def _explain_runner_factory(plans_by_sql):
    """Return a runner that maps SQL strings to canned EXPLAIN JSON blobs.

    Default fallback: an "unknown table" plan so unmapped queries still parse.
    """
    def _runner(sql):
        return plans_by_sql.get(sql, _make_explain(rows_est=999, bytes_est=9999, table="hive.default.unknown"))
    return _runner


def _llm_provider_with_replies(replies):
    """Mock provider whose complete_text() returns replies in order."""
    provider = MagicMock()
    provider.complete_text.side_effect = list(replies)
    return provider


def _wrap_sql(sql):
    """Wrap a SQL string in a markdown code block as the LLM would."""
    return f"Here is the rewrite:\n```sql\n{sql}\n```"


# ── Plan-cost loop unit tests ─────────────────────────────────────────────────

def test_plan_cost_loop_picks_lowest_cost_when_l3_passes():
    """Two candidates with identical structure; lowest plan cost should win."""
    output = MagicMock()
    baseline = _make_baseline(rows=100, wall_ms=80_000)
    cand_a = "SELECT a FROM t WHERE a > 0"  # cost = 100
    cand_b = "SELECT a FROM t WHERE a > 100"  # cost = 50 (lower → preferred)
    plans = {
        "SELECT * FROM t":     _make_explain(rows_est=10, bytes_est=10),  # baseline
        cand_a:                _make_explain(rows_est=10, bytes_est=10),  # cost 100
        cand_b:                _make_explain(rows_est=5,  bytes_est=10),  # cost 50
    }
    runner = _explain_runner_factory(plans)
    provider = _llm_provider_with_replies([_wrap_sql(cand_a), _wrap_sql(cand_b)])

    measured = _make_baseline(rows=100, wall_ms=20_000)  # candidate runs faster
    with patch("genie.skills.trino_query.research._measure", return_value=measured):
        result = _run_plan_cost_loop(
            provider=provider,
            model="m", reasoning="default",
            original_sql="SELECT * FROM t",
            metric_key="cpu_time_ms",
            max_iterations=2, verify_runs=1,
            output=output,
            build_prompt=lambda *a, **kw: "",
            baseline=baseline,
            baseline_data=baseline["rows"],
            static_report=None,
            explain_runner=runner,
            max_fallbacks=3,
        )
    assert result["status"] == "completed"
    assert result["mode"] == "plan_cost"
    assert result["best_sql"] == cand_b  # lower plan cost won
    assert result["candidates_evaluated"] == 2
    assert result["best_metric"] == 20_000.0


def test_plan_cost_loop_emits_no_verifiable_when_no_candidate_beats_baseline():
    """If every candidate has plan cost ≥ baseline, return original SQL."""
    output = MagicMock()
    baseline = _make_baseline(rows=100)
    cand = "SELECT a FROM t LIMIT 10000"
    plans = {
        "SELECT * FROM t": _make_explain(rows_est=5, bytes_est=10),  # cost 50
        cand:              _make_explain(rows_est=100, bytes_est=10),  # cost 1000 (worse)
    }
    runner = _explain_runner_factory(plans)
    provider = _llm_provider_with_replies([_wrap_sql(cand)])

    with patch("genie.skills.trino_query.research._measure") as m:
        result = _run_plan_cost_loop(
            provider=provider,
            model="m", reasoning="default",
            original_sql="SELECT * FROM t",
            metric_key="cpu_time_ms",
            max_iterations=1, verify_runs=1,
            output=output,
            build_prompt=lambda *a, **kw: "",
            baseline=baseline,
            baseline_data=baseline["rows"],
            static_report=None,
            explain_runner=runner,
            max_fallbacks=3,
        )
        # No candidate beat baseline → never call _measure for verification
        m.assert_not_called()
    assert result["status"] == "no_verifiable_improvement"
    assert result["best_sql"] == "SELECT * FROM t"


def test_plan_cost_loop_l1_rejects_structural_divergence():
    """Candidate with a totally different plan shape must be rejected at L1."""
    output = MagicMock()
    baseline = _make_baseline(rows=100)
    # Candidate plan changes the table — structural divergence
    cand = "SELECT a FROM other_table"
    baseline_plan = json.dumps({
        "name": "TableScan[hive.default.t]",
        "descriptor": {"table": "hive.default.t"},
        "estimates": [{"outputRowCount": 100, "outputSizeInBytes": 1000}],
        "children": [],
    })
    cand_plan = json.dumps({
        "name": "TableScan[hive.default.other_table]",
        "descriptor": {"table": "hive.default.other_table"},
        "estimates": [{"outputRowCount": 50, "outputSizeInBytes": 500}],  # cheaper, but wrong
        "children": [],
    })
    runner = _explain_runner_factory({
        "SELECT * FROM t": baseline_plan,
        cand:              cand_plan,
    })
    provider = _llm_provider_with_replies([_wrap_sql(cand)])

    with patch("genie.skills.trino_query.research._measure") as m:
        result = _run_plan_cost_loop(
            provider=provider,
            model="m", reasoning="default",
            original_sql="SELECT * FROM t",
            metric_key="cpu_time_ms",
            max_iterations=1, verify_runs=1,
            output=output,
            build_prompt=lambda *a, **kw: "",
            baseline=baseline,
            baseline_data=baseline["rows"],
            static_report=None,
            explain_runner=runner,
            max_fallbacks=3,
        )
        m.assert_not_called()
    assert result["status"] == "no_verifiable_improvement"
    statuses = [h["status"] for h in result["history"]]
    assert "structural_reject" in statuses


def test_plan_cost_loop_k_retry_falls_through_on_l3_failure():
    """If top candidate fails row-equivalence, fall through to next-ranked."""
    output = MagicMock()
    baseline = _make_baseline(rows=100)
    cand_a = "SELECT a FROM t WHERE id > 100"
    cand_b = "SELECT a FROM t WHERE id > 50"
    plans = {
        "SELECT * FROM t": _make_explain(rows_est=100, bytes_est=10),  # cost 1000
        cand_a:            _make_explain(rows_est=10, bytes_est=10),   # cost 100 (best)
        cand_b:            _make_explain(rows_est=20, bytes_est=10),   # cost 200
    }
    runner = _explain_runner_factory(plans)
    provider = _llm_provider_with_replies([_wrap_sql(cand_a), _wrap_sql(cand_b)])

    # First candidate measures fast but returns DIFFERENT rows (L3 fail)
    # Second candidate matches baseline rows (L3 pass)
    bad = _make_baseline(rows=50, wall_ms=10_000)
    good = _make_baseline(rows=100, wall_ms=20_000)
    with patch(
        "genie.skills.trino_query.research._measure",
        side_effect=[bad, good],
    ):
        result = _run_plan_cost_loop(
            provider=provider,
            model="m", reasoning="default",
            original_sql="SELECT * FROM t",
            metric_key="cpu_time_ms",
            max_iterations=2, verify_runs=1,
            output=output,
            build_prompt=lambda *a, **kw: "",
            baseline=baseline,
            baseline_data=baseline["rows"],
            static_report=None,
            explain_runner=runner,
            max_fallbacks=3,
        )
    assert result["status"] == "completed"
    assert result["best_sql"] == cand_b  # top failed L3, fell through to b
    assert result["fallbacks_used"] == 1
    # verify_log shows both attempts
    assert len(result["verify_log"]) == 2
    assert result["verify_log"][0]["result"] == "row_equiv_fail"
    assert result["verify_log"][1]["result"] == "verified"


def test_plan_cost_loop_emits_no_verifiable_when_all_l3_fail():
    """All candidates fail L3 → no winner."""
    output = MagicMock()
    baseline = _make_baseline(rows=100)
    cand_a = "SELECT a FROM t WHERE id > 100"
    plans = {
        "SELECT * FROM t": _make_explain(rows_est=100, bytes_est=10),
        cand_a:            _make_explain(rows_est=10, bytes_est=10),
    }
    runner = _explain_runner_factory(plans)
    provider = _llm_provider_with_replies([_wrap_sql(cand_a)])

    bad = _make_baseline(rows=50, wall_ms=10_000)  # wrong row count → L3 fail
    with patch("genie.skills.trino_query.research._measure", return_value=bad):
        result = _run_plan_cost_loop(
            provider=provider,
            model="m", reasoning="default",
            original_sql="SELECT * FROM t",
            metric_key="cpu_time_ms",
            max_iterations=1, verify_runs=1,
            output=output,
            build_prompt=lambda *a, **kw: "",
            baseline=baseline,
            baseline_data=baseline["rows"],
            static_report=None,
            explain_runner=runner,
            max_fallbacks=3,
        )
    assert result["status"] == "no_verifiable_improvement"
    assert result["best_sql"] == "SELECT * FROM t"


# ── Dispatch from _run_optimization_loop ──────────────────────────────────────

def test_loop_dispatches_to_plan_cost_when_long_query_and_explain_provided():
    """long_query_opt_in=True AND explain_runner provided → plan-cost path."""
    output = MagicMock()
    baseline = _make_baseline(rows=100, wall_ms=80_000)  # > threshold
    plans = {
        "SELECT * FROM t": _make_explain(rows_est=100, bytes_est=10),
    }
    runner = _explain_runner_factory(plans)
    provider = _llm_provider_with_replies([])  # not consumed

    with patch("genie.skills.trino_query.research._measure", return_value=baseline), \
         patch("genie.skills.trino_query.research._run_plan_cost_loop") as mock_loop:
        mock_loop.return_value = {"status": "completed", "mode": "plan_cost"}
        result = _run_optimization_loop(
            provider=provider, model="m", reasoning="default",
            original_sql="SELECT * FROM t",
            metric_key="cpu_time_ms",
            max_iterations=2, verify_runs=1,
            output=output,
            build_prompt=lambda *a, **kw: "",
            long_query_opt_in=True,
            explain_runner=runner,
        )
    mock_loop.assert_called_once()
    assert result["mode"] == "plan_cost"


def test_loop_dispatches_to_plan_cost_by_default_for_slow_query_with_explain():
    """Slow-query tuning is the default when an EXPLAIN runner is available."""
    output = MagicMock()
    baseline = _make_baseline(rows=100, wall_ms=80_000)  # > threshold
    runner = _explain_runner_factory({"SELECT * FROM t": _make_explain(rows_est=100, bytes_est=10)})
    provider = _llm_provider_with_replies([])

    with patch("genie.skills.trino_query.research._measure", return_value=baseline), \
         patch("genie.skills.trino_query.research._run_plan_cost_loop") as mock_loop:
        mock_loop.return_value = {"status": "completed", "mode": "plan_cost"}
        result = _run_optimization_loop(
            provider=provider, model="m", reasoning="default",
            original_sql="SELECT * FROM t",
            metric_key="cpu_time_ms",
            max_iterations=2, verify_runs=1,
            output=output,
            build_prompt=lambda *a, **kw: "",
            explain_runner=runner,
        )
    mock_loop.assert_called_once()
    assert result["mode"] == "plan_cost"


def test_loop_uses_legacy_path_when_explain_runner_absent():
    """explain_runner=None → legacy per-iteration measurement path."""
    output = MagicMock()
    baseline = _make_baseline(rows=100, wall_ms=10_000)  # under threshold
    provider = _llm_provider_with_replies([])

    with patch("genie.skills.trino_query.research._measure", return_value=baseline), \
         patch("genie.skills.trino_query.research._run_plan_cost_loop") as mock_loop:
        result = _run_optimization_loop(
            provider=provider, model="m", reasoning="default",
            original_sql="SELECT * FROM t",
            metric_key="cpu_time_ms",
            max_iterations=0, verify_runs=1,  # 0 iterations → loop body skipped
            output=output,
            build_prompt=lambda *a, **kw: "",
            long_query_opt_in=True,
            explain_runner=None,  # absent → legacy path
        )
    mock_loop.assert_not_called()
    assert result["status"] == "completed"


def test_loop_uses_legacy_path_when_long_query_opt_in_false():
    """long_query_opt_in=False → legacy path even if explain_runner provided."""
    output = MagicMock()
    baseline = _make_baseline(rows=100, wall_ms=10_000)
    provider = _llm_provider_with_replies([])
    runner = _explain_runner_factory({})

    with patch("genie.skills.trino_query.research._measure", return_value=baseline), \
         patch("genie.skills.trino_query.research._run_plan_cost_loop") as mock_loop:
        result = _run_optimization_loop(
            provider=provider, model="m", reasoning="default",
            original_sql="SELECT * FROM t",
            metric_key="cpu_time_ms",
            max_iterations=0, verify_runs=1,
            output=output,
            build_prompt=lambda *a, **kw: "",
            long_query_opt_in=False,
            explain_runner=runner,
        )
    mock_loop.assert_not_called()
    assert result["status"] == "completed"
