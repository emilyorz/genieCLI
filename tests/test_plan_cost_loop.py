"""Tests for v28 T4: plan-cost iteration loop with L1 structural guard + K-retry.

Mocks the LLM provider, EXPLAIN runner, and `_measure` so tests run without a
live Trino server. Each test asserts a single algorithmic property of the
plan-cost loop.
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest


def test_direct_bounded_reader_row_reference_contract_and_metadata():
    """Direct reader uses only fixed fetchmany batches; this is not a byte bound."""
    from genie.skills.trino_query.research import _read_rows_bounded

    class Cursor:
        def __init__(self):
            self.batches = [[(1,), (2,)], [(3,), (4,)], [(5,)], []]
            self.requests = []
        def fetchmany(self, size):
            self.requests.append(size)
            return self.batches.pop(0)
        def fetchall(self):
            raise AssertionError("bounded research reader must not call fetchall")

    cursor = Cursor()
    result = _read_rows_bounded(cursor, capture_rows=True, max_capture_rows=3, batch_size=2)
    assert result.observed_row_count == 5
    assert result.rows == [(1,), (2,), (3,)]
    assert result.captured_row_count == 3
    assert result.capture_status == "truncated"
    assert result.completeness == "direct_truncated"
    assert cursor.requests == [2, 2, 2, 2]


def test_direct_bounded_reader_below_cap_exact_cap_and_capture_disabled_contract():
    """Reader outcomes are row-reference capture semantics, never byte-memory claims."""
    from genie.skills.trino_query.research import _read_rows_bounded

    class Cursor:
        def __init__(self, batches):
            self.batches = list(batches) + [[]]
            self.requests = []
        def fetchmany(self, size):
            self.requests.append(size)
            return self.batches.pop(0)

    below = _read_rows_bounded(Cursor([[(1,), (2,)]]), capture_rows=True,
                               max_capture_rows=3, batch_size=2)
    assert (below.observed_row_count, below.rows, below.capture_status, below.completeness) == (
        2, [(1,), (2,)], "complete", "verified_complete")

    exact = _read_rows_bounded(Cursor([[(1,), (2,)], [(3,)]]), capture_rows=True,
                               max_capture_rows=3, batch_size=2)
    assert (exact.observed_row_count, exact.captured_row_count, exact.capture_status, exact.completeness) == (
        3, 3, "complete", "verified_complete")

    disabled_cursor = Cursor([[(1,), (2,)], [(3,)]])
    disabled = _read_rows_bounded(disabled_cursor, capture_rows=False,
                                  max_capture_rows=3, batch_size=2)
    assert (disabled.observed_row_count, disabled.rows, disabled.captured_row_count,
            disabled.capture_status, disabled.completeness) == (3, [], 0, "not_captured", "not_captured")
    assert disabled_cursor.requests == [2, 2, 2]


@pytest.mark.parametrize("max_capture_rows,batch_size", [
    (True, 1), (0, 1), (-1, 1), (1.0, 1), ("1", 1), (object(), 1),
    (1, True), (1, 0), (1, -1), (1, 1.0), (1, "1"), (1, object()),
])
def test_direct_bounded_reader_invalid_type_matrix_fails_before_fetch(max_capture_rows, batch_size):
    """All invalid reader bounds fail before requesting a batch."""
    from genie.skills.trino_query.research import _read_rows_bounded

    class Cursor:
        def __init__(self):
            self.requests = []
        def fetchmany(self, size):
            self.requests.append(size)
            return []

    cursor = Cursor()
    with pytest.raises(ValueError):
        _read_rows_bounded(cursor, capture_rows=True,
                           max_capture_rows=max_capture_rows, batch_size=batch_size)
    assert cursor.requests == []


def test_direct_bounded_reader_validates_before_fetch_and_rejects_oversized_batch():
    """Invalid bounds and noncompliant cursor batches cannot yield measurement data."""
    from genie.skills.trino_query.research import _read_rows_bounded

    class Cursor:
        requests = []
        def fetchmany(self, size):
            self.requests.append(size)
            return [(1,), (2,)]

    cursor = Cursor()
    import pytest
    with pytest.raises(ValueError):
        _read_rows_bounded(cursor, capture_rows=True, max_capture_rows=1, batch_size=2)
    assert cursor.requests == []
    with pytest.raises(RuntimeError, match="more rows"):
        _read_rows_bounded(Cursor(), capture_rows=True, max_capture_rows=2, batch_size=1)


def test_direct_report_correctness_claim_follows_canonical_history_contract():
    """Direct report makes equivalence claims only when history has no gate rejection."""
    from genie.skills.trino_query.research import _generate_report

    base = {
        "baseline_metric": 100.0, "best_metric": 90.0,
        "total_improvement": -10.0, "improvement_pct": -10.0,
        "iterations": 1, "kept": 1, "baseline_rows": 2,
        "original_sql": "SELECT id FROM t", "best_sql": "SELECT id FROM t WHERE id > 0",
    }
    verified = _generate_report({**base, "history": [{
        "iteration": 1, "status": "improved", "metric": 90.0, "delta": -10.0,
        "base_sql": base["original_sql"], "candidate_sql": base["best_sql"],
    }]}, "cpu_time_ms", "test-model", 1)
    assert "**Result validation:** full row-level equivalence check" in verified
    assert "semantic preservation were not authorized" not in verified

    unverified = _generate_report({**base, "history": [{
        "iteration": 1, "status": "equivalence_unverified_incomplete_result",
        "rejection_reason": "candidate_direct_truncated", "metric": 90.0, "delta": -10.0,
        "base_sql": base["original_sql"], "candidate_sql": base["best_sql"],
    }]}, "cpu_time_ms", "test-model", 1)
    assert "full row-level equivalence check" not in unverified
    assert "unverified/incomplete result" in unverified
    assert "semantic preservation were not authorized" in unverified
    assert "`candidate_direct_truncated`" in unverified


def test_direct_incomplete_reasons_cover_side_and_mixed_classes():
    """The reason selector emits only the v62 canonical vocabulary."""
    from genie.skills.trino_query.research import _incomplete_rejection_reason
    complete = {"capture_status": "complete", "completeness": "verified_complete"}
    truncated = {"capture_status": "truncated", "completeness": "direct_truncated"}
    not_captured = {"capture_status": "not_captured", "completeness": "not_captured"}
    upstream = {"capture_status": "complete", "completeness": "unverified_received_envelope"}
    assert _incomplete_rejection_reason(complete, truncated) == "candidate_direct_truncated"
    assert _incomplete_rejection_reason(truncated, truncated) == "both_direct_truncated"
    assert _incomplete_rejection_reason(complete, not_captured) == "candidate_capture_not_captured"
    assert _incomplete_rejection_reason(upstream, upstream) == "both_upstream_completeness_unverified"
    assert _incomplete_rejection_reason(truncated, upstream) == "mixed_incomplete_result"

import pytest

from genie.skills.trino_query.research import (
    _run_optimization_loop,
    _run_plan_cost_loop,
)
from genie.skills.trino_query.sql_static import Finding, StaticAnalysisReport
from genie.skills.mcp_trino.preflight import CandidateTimeoutError


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
        "capture_status": "complete",
        "completeness": "verified_complete",
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


def _make_explain_bytes_only(bytes_est, table="hive.default.t"):
    """EXPLAIN JSON with outputSizeInBytes but NO outputRowCount (partial Trino stats)."""
    return json.dumps({
        "name": f"TableScan[{table}]",
        "descriptor": {"table": table},
        "estimates": [{"outputSizeInBytes": bytes_est}],
        "children": [],
    })


def _make_explain_rows_only(rows_est, table="hive.default.t"):
    """EXPLAIN JSON with outputRowCount but NO outputSizeInBytes."""
    return json.dumps({
        "name": f"TableScan[{table}]",
        "descriptor": {"table": table},
        "estimates": [{"outputRowCount": rows_est}],
        "children": [],
    })


def _make_explain_no_estimates(table="hive.default.t"):
    """EXPLAIN JSON with empty estimates list (no usable cost data)."""
    return json.dumps({
        "name": f"TableScan[{table}]",
        "descriptor": {"table": table},
        "estimates": [],
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


class _PromptCaptured(Exception):
    """Carries the captured system prompt out of new_session."""

    def __init__(self, sys_prompt: str):
        super().__init__("captured")
        self.sys_prompt = sys_prompt


def _capturing_new_session(sys_prompt: str):
    raise _PromptCaptured(sys_prompt)


# ── v62 public direct-entry ordering ─────────────────────────────────────────


@pytest.mark.parametrize("sql", ["SELECT * FROM source", "INSERT INTO audit_log SELECT * FROM source"])
def test_direct_public_entry_rejects_invalid_safe_limit_before_advisory_provider_explain_baseline_or_loop(
    monkeypatch, sql
):
    """Invalid public policy cannot reach any direct read/write work surface."""
    from genie.skills.trino_query.research import run_trino_research

    output = MagicMock()
    advisory = MagicMock(side_effect=AssertionError("write advisory must not run"))
    provider = MagicMock()
    monkeypatch.setattr("genie.skills.mcp_trino.write_analysis.run_write_analysis_only", advisory)
    monkeypatch.setattr(
        "genie.skills.mcp_trino.preflight.run_preflight",
        MagicMock(side_effect=AssertionError("preflight/EXPLAIN must not run")),
    )
    monkeypatch.setattr(
        "genie.skills.trino_query.research._run_optimization_loop",
        MagicMock(side_effect=AssertionError("baseline/loop must not run")),
    )
    monkeypatch.setattr(
        "genie.skills.trino_query.research._execute_sql",
        MagicMock(side_effect=AssertionError("executor must not run")),
    )

    with pytest.raises(ValueError, match="safe_limit must be a positive integer or None"):
        run_trino_research(
            provider, {}, "test-model", "default", output, lambda *_: "",
            sql_text=sql, safe_limit=0, metric="cpu_time_ms", iterations=1, runs=1,
        )

    advisory.assert_not_called()
    provider.complete_text.assert_not_called()


def test_direct_plan_cost_generated_write_never_reaches_executor_or_candidate_explain(monkeypatch):
    """Direct adapter wires the shared generated-SQL gate ahead of both boundaries."""
    monkeypatch.setenv("GENIE_V48_SEED_DECOMPOSE", "0")
    original_sql = "SELECT id FROM source"
    candidate_sql = "DELETE FROM source WHERE id = 1"
    explained = []
    executor = MagicMock(side_effect=AssertionError("direct executor must not run"))
    monkeypatch.setattr("genie.skills.trino_query.research._measure", executor)

    def explain_runner(sql):
        explained.append(sql)
        if sql != original_sql:
            raise AssertionError("unsafe candidate reached EXPLAIN")
        return _make_explain()

    result = _run_plan_cost_loop(
        provider=_llm_provider_with_replies([_wrap_sql(candidate_sql)]),
        model="test-model", reasoning="default", original_sql=original_sql,
        metric_key="cpu_time_ms", max_iterations=1, verify_runs=1,
        output=MagicMock(), build_prompt=lambda *_: "", baseline=_make_baseline(),
        baseline_data=[(i,) for i in range(100)], static_report=None,
        explain_runner=explain_runner, max_fallbacks=1,
    )

    assert result["best_sql"] == original_sql
    assert candidate_sql not in explained
    executor.assert_not_called()


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
    with patch("genie.skills.trino_query.research._measure", return_value=measured) as measure:
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
    assert measure.call_args.kwargs["timeout_ms"] == 160_000  # 80s baseline × 2x headroom
    assert result["status"] == "completed"
    assert result["mode"] == "plan_cost"
    assert result["best_sql"] == cand_b  # lower plan cost won
    assert result["candidates_evaluated"] == 2
    assert result["best_metric"] == 20_000.0


def test_plan_cost_loop_injects_rule_gate_before_skill_prompt():
    """Plan-cost mode should get the same pre-AI rule gate as legacy direct."""
    output = MagicMock()
    baseline = _make_baseline(rows=100, wall_ms=80_000)
    runner = _explain_runner_factory({
        "SELECT * FROM t": _make_explain(rows_est=100, bytes_est=10),
    })
    static_report = StaticAnalysisReport(findings=[
        Finding("medium", "select-star", "SELECT * found", "name needed columns", 1),
    ])

    with patch("genie.session.manager.new_session", side_effect=_capturing_new_session):
        with pytest.raises(_PromptCaptured) as exc:
            _run_plan_cost_loop(
                provider=MagicMock(),
                model="m",
                reasoning="default",
                original_sql="SELECT * FROM t",
                metric_key="cpu_time_ms",
                max_iterations=1,
                verify_runs=1,
                output=output,
                build_prompt=lambda *a, **kw: "SKILL_PROMPT_TEXT",
                baseline=baseline,
                baseline_data=baseline["rows"],
                static_report=static_report,
                explain_runner=runner,
                max_fallbacks=3,
            )

    sys_prompt = exc.value.sys_prompt
    assert "Rule-based gate" in sys_prompt
    assert "select-star" in sys_prompt
    assert "side effects: do not emit CTAS/materialized-view DDL" in sys_prompt
    assert sys_prompt.index("Rule-based gate") < sys_prompt.index("SKILL_PROMPT_TEXT")
    assert any("rule gate" in str(call.args[0]) for call in output.print.call_args_list)


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

    # Production adapter reconstruction must keep the compact ranking record
    # unmeasured while retaining the verified winner's actual measurement.
    compact_non_winner = next(h for h in result["history"] if h["candidate_sql"] == cand_a)
    measured_winner = next(h for h in result["history"] if h["candidate_sql"] == cand_b)
    assert compact_non_winner["metric"] is None
    assert compact_non_winner["delta"] is None
    assert measured_winner["status"] == "improved"
    assert measured_winner["metric"] == 20_000.0
    assert measured_winner["delta"] == -60_000.0

    from genie.skills.trino_query.research import _format_history_measurement
    assert _format_history_measurement(compact_non_winner) == ("n/a", "n/a")


def test_direct_plan_cost_decompose_seed_authorization_failure_persists_one_canonical_record(monkeypatch):
    """Direct plan-cost seed provenance rejection keeps the baseline winner."""
    monkeypatch.setenv("GENIE_V48_SEED_DECOMPOSE", "1")
    output = MagicMock()
    sql = "SELECT id FROM t"
    seed_sql = "SELECT id FROM t WHERE id > 0"
    baseline = _make_baseline(rows=2, wall_ms=80_000)
    seed = _make_baseline(rows=2, wall_ms=10_000)
    seed["capture_status"] = "truncated"
    seed["completeness"] = "direct_truncated"
    runner = _explain_runner_factory({sql: _make_explain(rows_est=100, bytes_est=10)})

    with patch("genie.skills.mcp_trino.write_analysis._make_advisory_llm_fn", return_value=MagicMock()), \
         patch("genie.skills.mcp_trino.research._produce_decompose_candidate",
               return_value=(seed_sql, [], [], None)), \
         patch("genie.skills.trino_query.research._measure", return_value=seed):
        result = _run_plan_cost_loop(
            provider=MagicMock(), model="m", reasoning="default", original_sql=sql,
            metric_key="cpu_time_ms", max_iterations=0, verify_runs=1,
            output=output, build_prompt=lambda *a, **kw: "", baseline=baseline,
            baseline_data=baseline["rows"], static_report=None,
            explain_runner=runner, max_fallbacks=3,
        )

    rejections = [h for h in result["history"]
                  if h["status"] == "equivalence_unverified_incomplete_result"]
    assert result["best_sql"] == sql
    assert rejections == [{
        "iteration": 0, "status": "equivalence_unverified_incomplete_result",
        "rejection_reason": "candidate_direct_truncated",
        "metric": 10_000.0, "delta": -70_000.0,
        "base_sql": sql, "candidate_sql": seed_sql,
        "baseline_capture_status": "complete", "candidate_capture_status": "truncated",
        "baseline_completeness": "verified_complete", "candidate_completeness": "direct_truncated",
    }]


def test_direct_plan_cost_incomplete_failure_persists_full_history_and_keeps_baseline():
    """An unverified plan-cost candidate cannot replace its coupled baseline."""
    output = MagicMock()
    baseline = _make_baseline(rows=2, wall_ms=80_000)
    candidate = _make_baseline(rows=2, wall_ms=10_000)
    candidate["capture_status"] = "truncated"
    candidate["completeness"] = "direct_truncated"
    sql = "SELECT id FROM t"
    candidate_sql = "SELECT id FROM t WHERE id > 0"
    runner = _explain_runner_factory({
        sql: _make_explain(rows_est=100, bytes_est=10),
        candidate_sql: _make_explain(rows_est=10, bytes_est=10),
    })
    with patch("genie.skills.trino_query.research._measure", return_value=candidate):
        result = _run_plan_cost_loop(
            provider=_llm_provider_with_replies([_wrap_sql(candidate_sql)]),
            model="m", reasoning="default", original_sql=sql,
            metric_key="cpu_time_ms", max_iterations=1, verify_runs=1,
            output=output, build_prompt=lambda *a, **kw: "", baseline=baseline,
            baseline_data=baseline["rows"], static_report=None,
            explain_runner=runner, max_fallbacks=3,
        )

    assert result["best_sql"] == sql
    rejection = next(h for h in result["history"] if h["status"] == "equivalence_unverified_incomplete_result")
    assert rejection == {
        "iteration": 1,
        "status": "equivalence_unverified_incomplete_result",
        "rejection_reason": "candidate_direct_truncated",
        "metric": 10_000.0,
        "delta": -70_000.0,
        "base_sql": sql,
        "candidate_sql": candidate_sql,
        "baseline_capture_status": "complete",
        "candidate_capture_status": "truncated",
        "baseline_completeness": "verified_complete",
        "candidate_completeness": "direct_truncated",
    }
    # Case B preserves the core's compact plan-cost ranking record alongside the
    # canonical rejection record.  Both report surfaces must render that mixed
    # provenance history rather than assuming every entry has metric/delta.
    assert [h["status"] for h in result["history"]] == [
        "plan_cost_better", "equivalence_unverified_incomplete_result",
    ]
    from genie.skills.trino_query.research import _generate_report, _render_terminal_history
    markdown = _generate_report(result, "cpu_time_ms", "test-model", 1)
    assert "unverified/incomplete result" in markdown
    assert "semantic preservation were not authorized" in markdown
    assert "`candidate_direct_truncated`" in markdown
    assert "| 1 | plan_cost_better | n/a | n/a |" in markdown
    terminal = MagicMock()
    _render_terminal_history(terminal, result["history"])
    assert "metric=n/a delta=n/a" in terminal.print.call_args_list[0].args[0]


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


def test_plan_cost_loop_treats_candidate_timeout_as_failed_fallback():
    """A candidate that exceeds baseline wall time should not remain eligible."""
    output = MagicMock()
    baseline = _make_baseline(rows=100, wall_ms=80_000)
    cand = "SELECT a FROM t WHERE id > 100"
    runner = _explain_runner_factory({
        "SELECT * FROM t": _make_explain(rows_est=100, bytes_est=10),
        cand: _make_explain(rows_est=10, bytes_est=10),
    })
    provider = _llm_provider_with_replies([_wrap_sql(cand)])

    with patch(
        "genie.skills.trino_query.research._measure",
        side_effect=CandidateTimeoutError(80_000, "verify iter 1"),
    ):
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
    assert result["verify_log"][0]["result"] == "timeout_worse"


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


# ── Bugfix: bytes-only / rows-only / None-baseline cost ranking ───────────────

def test_plan_cost_loop_bytes_only_candidate_with_fewer_bytes_still_wins():
    """Bytes-only candidate (rows=None) with bytes < baseline_cost must NOT be
    falsely ranked first due to the old zero-collapse bug.

    With the bug: cand_cost = 0 (always wins regardless of bytes).
    After fix:   cand_cost = bytes = 5_000_000 < 8_000_000_000_000 (correct win).
    """
    output = MagicMock()
    baseline = _make_baseline(rows=100, wall_ms=80_000)
    cand = "SELECT a FROM t WHERE a > 0"
    plans = {
        "SELECT * FROM t": _make_explain(rows_est=1_000_000, bytes_est=8_000_000),
        cand: _make_explain_bytes_only(bytes_est=5_000_000),  # rows=None, bytes=5M
    }
    runner = _explain_runner_factory(plans)
    provider = _llm_provider_with_replies([_wrap_sql(cand)])

    measured = _make_baseline(rows=100, wall_ms=20_000)
    with patch("genie.skills.trino_query.research._measure", return_value=measured):
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
    # Candidate should rank as plan_cost_better (5M < 8T) and get verified
    assert result["status"] == "completed"
    assert result["best_sql"] == cand


def test_plan_cost_loop_bytes_only_candidate_with_more_bytes_does_not_win():
    """Bytes-only candidate with bytes > baseline_cost must NOT win.

    The old bug produced cand_cost=0 which always beat any positive baseline,
    so this sub-case was always a false positive.
    After fix: cand_cost = 90_000 > 80_000 → correctly rejected.
    """
    output = MagicMock()
    baseline = _make_baseline(rows=100, wall_ms=80_000)
    cand = "SELECT a FROM t WHERE a > 0"
    plans = {
        "SELECT * FROM t": _make_explain(rows_est=100, bytes_est=800),   # cost = 80_000
        cand: _make_explain_bytes_only(bytes_est=90_000),                 # cand_cost = 90_000 > 80_000
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
        m.assert_not_called()  # candidate never entered verification
    assert result["status"] == "no_verifiable_improvement"
    assert result["best_sql"] == "SELECT * FROM t"


def test_plan_cost_loop_rows_only_candidate_handled():
    """Rows-only candidate (bytes=None) ranks correctly without sentinel distortion."""
    output = MagicMock()
    baseline = _make_baseline(rows=100, wall_ms=80_000)
    cand = "SELECT a FROM t WHERE a > 0"
    plans = {
        "SELECT * FROM t": _make_explain(rows_est=1_000, bytes_est=8_000),  # cost = 8_000_000
        cand: _make_explain_rows_only(rows_est=900),                         # cand_cost = 900 (< 8M)
    }
    runner = _explain_runner_factory(plans)
    provider = _llm_provider_with_replies([_wrap_sql(cand)])

    measured = _make_baseline(rows=100, wall_ms=20_000)
    with patch("genie.skills.trino_query.research._measure", return_value=measured):
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
    assert result["status"] == "completed"
    assert result["best_sql"] == cand


def test_plan_cost_loop_both_missing_candidate_skipped_no_crash():
    """Candidate with no estimates at all is skipped — existing guard covers it."""
    output = MagicMock()
    baseline = _make_baseline(rows=100, wall_ms=80_000)
    cand = "SELECT a FROM t"
    plans = {
        "SELECT * FROM t": _make_explain(rows_est=100, bytes_est=10),
        cand: _make_explain_no_estimates(),  # empty estimates list → (None, None)
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
        m.assert_not_called()
    assert result["status"] == "no_verifiable_improvement"


def test_plan_cost_loop_baseline_none_cost_does_not_crash():
    """Baseline EXPLAIN with both rows_est=None and bytes_est=None must not crash.

    OI-01 fix: baseline_cost is now guarded — when None, each candidate is
    skipped (status=explain_failed) rather than raising TypeError on comparison.
    """
    output = MagicMock()
    baseline = _make_baseline(rows=100, wall_ms=80_000)
    cand = "SELECT a FROM t WHERE a > 0"
    plans = {
        "SELECT * FROM t": _make_explain_no_estimates(),   # baseline → (None, None)
        cand: _make_explain(rows_est=50, bytes_est=5),     # candidate has cost
    }
    runner = _explain_runner_factory(plans)
    provider = _llm_provider_with_replies([_wrap_sql(cand)])

    with patch("genie.skills.trino_query.research._measure") as m:
        # Must not raise TypeError
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
    # Candidate was not falsely promoted (skipped, not ranked as better)
    statuses = [h["status"] for h in result["history"]]
    assert "plan_cost_better" not in statuses
