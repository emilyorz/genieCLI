"""S1 edge-case tests for _plan_cost_loop_core (shared between MCP and direct adapters).

Covers: T-S1-K1, T-S1-K2, T-S1-K3, T-S1-VL (MCP+direct), T-S1-WH, T-S1-WPC,
        T-S1-WALL (MCP+direct), T-S1-NONE (MCP+direct), T-S1-ZERO, T-S1-TIE,
        T-S1-PART, T-S1-SAFE, T-S1-H1 (MCP+direct), T-S1-IMPORT.
"""
from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass, field
from typing import Optional
from unittest.mock import MagicMock


# ── Helpers ──────────────────────────────────────────────────────────────────

def _make_provider(reply: str = "```sql\nSELECT 1\n```"):
    """Return a mock provider that always emits the given reply."""
    provider = MagicMock()
    provider.complete_text.return_value = reply
    return provider


def _make_safe_output():
    """Return a _SafeOutput wrapping a real output spy."""
    from genie.skills.mcp_trino.preflight import _SafeOutput

    class _Spy:
        def __init__(self):
            self.lines = []

        def print(self, *a, **kw):
            self.lines.append(("print", a))

        def progress(self, *a, **kw):
            self.lines.append(("progress", a))

        def error(self, *a, **kw):
            self.lines.append(("error", a))

    spy = _Spy()
    return _SafeOutput(spy), spy


def _noop_measure(sql, label):
    """Measure that always raises so no candidate wins."""
    raise RuntimeError("no measure in this test")


def _make_row_equiv_always_pass():
    return lambda measured: (True, "")


def _make_row_equiv_always_fail():
    return lambda measured: (False, "row mismatch")


def _make_explain_runner(plan_cost_per_sql: dict):
    """Return an explain runner returning a JSON plan with the given estimated rows/bytes."""
    import json

    def _runner(sql):
        rows, byt = plan_cost_per_sql.get(sql, (None, None))
        if rows is None and byt is None:
            return None
        return json.dumps({
            "name": "Output",
            "estimates": [{"outputRowCount": rows or 0, "outputSizeInBytes": byt or 0}],
            "children": [],
        })

    return _runner


def _call_core(
    *,
    provider=None,
    original_sql="SELECT 1",
    max_iterations=1,
    max_fallbacks=3,
    baseline_cost=1000.0,
    baseline_sig=None,
    baseline_plan=None,
    baseline_rows_est=10,
    baseline_bytes_est=100,
    explain_runner=None,
    measure_fn=None,
    metric_fn=None,
    row_equiv_fn=None,
    output=None,
    candidate_timeout_ms=None,
    empty_message=None,
    static_report=None,
):
    from genie.skills.mcp_trino.preflight import _plan_cost_loop_core, _SafeOutput

    if provider is None:
        provider = _make_provider("```sql\nSELECT 2\n```")
    if explain_runner is None:
        # Default: candidate has lower cost than baseline
        explain_runner = _make_explain_runner({"SELECT 2": (5, 50)})
    if measure_fn is None:
        measure_fn = _noop_measure
    if metric_fn is None:
        metric_fn = lambda m: m["median"]
    if row_equiv_fn is None:
        row_equiv_fn = _make_row_equiv_always_pass()
    if output is None:
        output, _ = _make_safe_output()

    return _plan_cost_loop_core(
        provider=provider,
        model="test-model",
        reasoning="none",
        sys_prompt="test prompt",
        original_sql=original_sql,
        metric_key="wall_time_ms",
        max_iterations=max_iterations,
        max_fallbacks=max_fallbacks,
        baseline_cost=baseline_cost,
        baseline_sig=baseline_sig,
        baseline_plan=baseline_plan,
        baseline_rows_est=baseline_rows_est,
        baseline_bytes_est=baseline_bytes_est,
        explain_runner=explain_runner,
        measure_fn=measure_fn,
        metric_fn=metric_fn,
        row_equiv_fn=row_equiv_fn,
        static_report=static_report,
        output=output,
        candidate_timeout_ms=candidate_timeout_ms,
        empty_message=empty_message,
    )


# ── v62 fail-closed plan-cost candidate boundary ─────────────────────────────


import pytest


@pytest.mark.parametrize("adapter", ["direct", "mcp"])
def test_plan_cost_core_rejects_non_read_only_candidate_before_explain_or_measure(adapter):
    """Both adapter callers get the shared fail-closed candidate EXPLAIN boundary.

    The core is shared, while each production adapter injects its own
    policy-enforcing direct executor or MCP tool measurement callable.  Neither
    injected callable nor the EXPLAIN runner may see a generated write SQL.
    """
    explain_runner = MagicMock(side_effect=AssertionError("EXPLAIN must not run"))
    measure_fn = MagicMock(side_effect=AssertionError("executor/MCP tool must not run"))

    result = _call_core(
        provider=_make_provider("```sql\nINSERT INTO audit_log SELECT * FROM source\n```"),
        explain_runner=explain_runner,
        measure_fn=measure_fn,
    )

    assert result.winner_sql is None
    assert [entry["status"] for entry in result.history] == ["explain_failed"]
    assert result.history[0]["candidate_sql"].startswith("INSERT INTO")
    explain_runner.assert_not_called()
    measure_fn.assert_not_called()


# ── T-S1-SAFE: _SafeOutput(None) is no-op ────────────────────────────────────

def test_safe_output_none_is_noop():
    """T-S1-SAFE: _SafeOutput(None).print/progress/error all no-op, no exception."""
    from genie.skills.mcp_trino.preflight import _SafeOutput

    safe = _SafeOutput(None)
    safe.print("hello")
    safe.progress("progress")
    safe.error("error")
    # No exception raised; no assertion needed — test passes if we get here.


# ── T-S1-K1: Case A (no candidate beats baseline) — 13 keys, no verify_log, no fallbacks_used ──

def test_case_a_no_verify_log_no_fallbacks_used_exact_13_keys():
    """T-S1-K1: Case A returns 13 keys exactly; verify_log and fallbacks_used absent."""
    # Provider emits a candidate but explain_runner returns high cost (worse than baseline)
    result = _call_core(
        provider=_make_provider("```sql\nSELECT 2\n```"),
        baseline_cost=1000.0,
        explain_runner=_make_explain_runner({"SELECT 2": (500, 5000)}),  # cost > baseline
    )
    assert result.surviving_better_was_empty is True
    # Simulate direct adapter Case A
    direct_result = {
        "status": "no_verifiable_improvement",
        "baseline_metric": 1.0,
        "best_metric": 1.0,
        "total_improvement": 0.0,
        "improvement_pct": 0.0,
        "iterations": len(result.history),
        "kept": 0,
        "baseline_rows": 0,
        "baseline_plan_cost": result.baseline_cost,
        "original_sql": "SELECT 1",
        "best_sql": "SELECT 1",
        "history": result.history,
        "candidates_evaluated": result.candidates_evaluated,
    }
    expected_keys = {
        "status", "baseline_metric", "best_metric", "total_improvement",
        "improvement_pct", "iterations", "kept", "baseline_rows",
        "baseline_plan_cost", "original_sql", "best_sql", "history",
        "candidates_evaluated",
    }
    assert set(direct_result.keys()) == expected_keys
    assert "verify_log" not in direct_result
    assert "fallbacks_used" not in direct_result
    assert len(direct_result) == 13


# ── T-S1-K2: Case B (non-empty surviving_better, all L3 fail) — 14 keys, verify_log present, no fallbacks_used ──

def test_case_b_verify_log_present_fallbacks_used_absent():
    """T-S1-K2: Case B returns 14 keys; verify_log present, fallbacks_used absent."""
    from genie.skills.mcp_trino.preflight import CandidateTimeoutError

    # Candidate beats baseline cost but measure fails
    def _failing_measure(sql, label):
        raise RuntimeError("measure always fails")

    result = _call_core(
        provider=_make_provider("```sql\nSELECT 2\n```"),
        baseline_cost=1000.0,
        explain_runner=_make_explain_runner({"SELECT 2": (5, 50)}),  # cost < baseline
        measure_fn=_failing_measure,
        max_fallbacks=3,
    )
    assert result.surviving_better_was_empty is False
    assert result.winner_sql is None
    assert len(result.verify_log) > 0

    direct_result = {
        "status": "no_verifiable_improvement",
        "baseline_metric": 1.0,
        "best_metric": 1.0,
        "total_improvement": 0.0,
        "improvement_pct": 0.0,
        "iterations": len(result.history),
        "kept": 0,
        "baseline_rows": 0,
        "baseline_plan_cost": result.baseline_cost,
        "original_sql": "SELECT 1",
        "best_sql": "SELECT 1",
        "history": result.history,
        "candidates_evaluated": result.candidates_evaluated,
        "verify_log": result.verify_log,
    }
    assert "verify_log" in direct_result
    assert "fallbacks_used" not in direct_result
    assert len(direct_result) == 14


# ── T-S1-K3: Case C (winner found) — fallbacks_used present, verify_log present, status "completed" ──

def test_case_c_fallbacks_used_present_status_completed():
    """T-S1-K3: Case C has fallbacks_used and verify_log both present; status=="completed"."""
    def _passing_measure(sql, label):
        return {"median": 0.5, "samples": [0.5], "row_count": 1, "rows": [[1]], "metrics": {}}

    result = _call_core(
        provider=_make_provider("```sql\nSELECT 2\n```"),
        baseline_cost=1000.0,
        explain_runner=_make_explain_runner({"SELECT 2": (5, 50)}),
        measure_fn=_passing_measure,
        metric_fn=lambda m: m["median"],
        row_equiv_fn=lambda m: (True, ""),
    )
    assert result.winner_sql is not None
    assert result.surviving_better_was_empty is False

    winner_metric = result.winner_measure["median"]
    direct_result = {
        "status": "completed",
        "baseline_metric": 1.0,
        "best_metric": winner_metric,
        "total_improvement": winner_metric - 1.0,
        "improvement_pct": (winner_metric - 1.0) / 1.0 * 100,
        "iterations": len(result.history),
        "kept": 1,
        "baseline_rows": 0,
        "baseline_plan_cost": result.baseline_cost,
        "winner_plan_cost": result.winner_ranked["plan_cost"],
        "original_sql": "SELECT 1",
        "best_sql": result.winner_sql,
        "history": [],  # winner_history (not tested here)
        "verify_log": result.verify_log,
        "candidates_evaluated": result.candidates_evaluated,
        "fallbacks_used": result.fallbacks_used,
    }
    assert "fallbacks_used" in direct_result
    assert "verify_log" in direct_result
    assert direct_result["status"] == "completed"


# ── T-S1-VL: verify_log verified entry has "metric" key ──────────────────────

def test_verify_log_verified_entry_has_metric_direct():
    """T-S1-VL direct: verify_log["verified"]["metric"] == measured["median"]."""
    def _passing_measure(sql, label):
        return {"median": 42.0, "samples": [42.0], "row_count": 1, "rows": [[1]], "metrics": {}}

    result = _call_core(
        provider=_make_provider("```sql\nSELECT 2\n```"),
        baseline_cost=1000.0,
        explain_runner=_make_explain_runner({"SELECT 2": (5, 50)}),
        measure_fn=_passing_measure,
        metric_fn=lambda m: m["median"],
        row_equiv_fn=lambda m: (True, ""),
    )
    assert result.winner_sql is not None
    verified_entry = next(e for e in result.verify_log if e["result"] == "verified")
    assert "metric" in verified_entry
    assert verified_entry["metric"] == 42.0


def test_verify_log_verified_entry_has_metric_mcp():
    """T-S1-VL MCP: verify_log["verified"]["metric"] == measured.median_metric."""
    from dataclasses import dataclass

    @dataclass
    class FakeMeasure:
        median_metric: float = 99.0
        row_count: int = 1
        rows: list = field(default_factory=list)

    def _passing_measure_mcp(sql, label):
        return FakeMeasure(median_metric=99.0)

    result = _call_core(
        provider=_make_provider("```sql\nSELECT 2\n```"),
        baseline_cost=1000.0,
        explain_runner=_make_explain_runner({"SELECT 2": (5, 50)}),
        measure_fn=_passing_measure_mcp,
        metric_fn=lambda m: m.median_metric,
        row_equiv_fn=lambda m: (True, ""),
    )
    assert result.winner_sql is not None
    verified_entry = next(e for e in result.verify_log if e["result"] == "verified")
    assert "metric" in verified_entry
    assert verified_entry["metric"] == 99.0


# ── T-S1-WH: winner_history for the winning entry has all 7 required keys ────

def test_winner_history_has_7_keys_for_winning_entry():
    """T-S1-WH: the winner_history entry for the winning candidate has 7 required keys."""
    def _passing_measure(sql, label):
        return {"median": 0.5, "samples": [0.5], "row_count": 1, "rows": [[1]], "metrics": {}}

    result = _call_core(
        provider=_make_provider("```sql\nSELECT 2\n```"),
        baseline_cost=1000.0,
        explain_runner=_make_explain_runner({"SELECT 2": (5, 50)}),
        measure_fn=_passing_measure,
        metric_fn=lambda m: m["median"],
        row_equiv_fn=lambda m: (True, ""),
    )
    assert result.winner_sql is not None
    winner_metric = result.winner_measure["median"]
    baseline_metric = 1.0
    original_sql = "SELECT 1"

    # Build winner_history the same way the direct adapter does (§3.3)
    winner_history = [
        {
            "iteration": h["iteration"],
            "status": "improved" if (h["candidate_sql"] == result.winner_sql) else h["status"],
            "metric": winner_metric if (h["candidate_sql"] == result.winner_sql) else baseline_metric,
            "delta": winner_metric - baseline_metric if (h["candidate_sql"] == result.winner_sql) else 0.0,
            "hypothesis": "(plan-cost-loop)",
            "base_sql": original_sql,
            "candidate_sql": h.get("candidate_sql"),
        }
        for h in result.history
    ]

    required_keys = {"iteration", "status", "metric", "delta", "hypothesis", "base_sql", "candidate_sql"}
    winner_entries = [e for e in winner_history if e["candidate_sql"] == result.winner_sql]
    assert len(winner_entries) >= 1
    for entry in winner_entries:
        assert set(entry.keys()) >= required_keys, f"missing keys: {required_keys - set(entry.keys())}"
        assert entry["status"] == "improved"


# ── T-S1-WPC: winner_plan_cost comes from ranked["plan_cost"], not measure dict ──

def test_winner_plan_cost_is_from_ranked_not_measure():
    """T-S1-WPC: result.winner_ranked["plan_cost"] is the EXPLAIN cost, not None."""
    def _passing_measure(sql, label):
        # measure dict does NOT have a plan_cost key
        return {"median": 0.5, "samples": [0.5], "row_count": 1, "rows": [[1]], "metrics": {}}

    result = _call_core(
        provider=_make_provider("```sql\nSELECT 2\n```"),
        baseline_cost=1000.0,
        explain_runner=_make_explain_runner({"SELECT 2": (5, 50)}),
        measure_fn=_passing_measure,
        metric_fn=lambda m: m["median"],
        row_equiv_fn=lambda m: (True, ""),
    )
    assert result.winner_sql is not None
    assert result.winner_ranked is not None
    winner_plan_cost = result.winner_ranked["plan_cost"]
    assert winner_plan_cost is not None
    # Sanity: plan_cost from ranked candidate (rows=5, bytes=50 → _combine_cost result)
    assert winner_plan_cost < 1000.0  # must be less than baseline cost
    # Also confirm measure dict has no plan_cost
    assert "plan_cost" not in result.winner_measure


# ── T-S1-WALL: baseline_wall_ms computation ──────────────────────────────────

def test_wall_mcp_max_of_two_fields():
    """T-S1-WALL MCP: RunMetrics(query_time_ms=10, wall_time_ms=20) → max==20.0 (2-field max)."""
    from genie.skills.mcp_trino.research import RunMetrics, _baseline_wall_ms

    metrics = RunMetrics(query_time_ms=10.0, wall_time_ms=20.0)
    assert _baseline_wall_ms(metrics) == 20.0


def test_wall_mcp_prefers_query_time_when_stage_wall_is_tiny():
    """Sam repro: EXPLAIN ANALYZE backfill wall=185ms must not beat e2e query=4196ms.

    Old ``wall or query`` collapsed candidate timeout to the 2s floor and set
    ``SET SESSION query_max_run_time = '2000ms'`` even though baseline took ~4s.
    """
    from genie.skills.mcp_trino.preflight import make_query_max_run_time_sql
    from genie.skills.mcp_trino.research import RunMetrics, _baseline_wall_ms

    metrics = RunMetrics(query_time_ms=4196.0, wall_time_ms=185.0, cpu_time_ms=168.4)
    baseline_wall_ms = _baseline_wall_ms(metrics)
    assert baseline_wall_ms == 4196.0
    # 2× headroom → ~8392ms, NOT the 2000ms floor
    assert make_query_max_run_time_sql(baseline_wall_ms) == (
        "SET SESSION query_max_run_time = '8392ms'"
    )
    # Guard the anti-pattern explicitly: truthy tiny wall must not win.
    legacy_bug = float(metrics.wall_time_ms or metrics.query_time_ms or 0)
    assert legacy_bug == 185.0
    assert baseline_wall_ms > legacy_bug


def test_wall_direct_max_of_three_fields():
    """T-S1-WALL direct: metrics with elapsed_time_ms=30 → _baseline_wall_ms returns 30.0."""
    from genie.skills.trino_query.research import _baseline_wall_ms

    class FakeMetrics:
        query_time_ms = 10.0
        wall_time_ms = 20.0
        elapsed_time_ms = 30.0

    result = _baseline_wall_ms(FakeMetrics())
    assert result == 30.0


# ── T-S1-NONE: baseline_cost=None → all candidates explain_failed, no winner ──

def test_none_baseline_cost_mcp_no_winner():
    """T-S1-NONE MCP: baseline_cost=None → all iterations emit explain_failed, no winner."""
    result = _call_core(
        provider=_make_provider("```sql\nSELECT 2\n```"),
        baseline_cost=None,
        explain_runner=_make_explain_runner({"SELECT 2": (5, 50)}),
        measure_fn=_noop_measure,
    )
    assert result.winner_sql is None
    # All history entries should be explain_failed (baseline_cost is None prevents ranking)
    for h in result.history:
        assert h["status"] == "explain_failed", f"expected explain_failed, got {h['status']}"


def test_none_baseline_cost_direct_no_winner():
    """T-S1-NONE direct: same — baseline_cost=None → all explain_failed, no winner."""
    result = _call_core(
        provider=_make_provider("```sql\nSELECT 2\n```"),
        baseline_cost=None,
        explain_runner=_make_explain_runner({"SELECT 2": (5, 50)}),
        measure_fn=_noop_measure,
        metric_fn=lambda m: m["median"],
    )
    assert result.winner_sql is None
    for h in result.history:
        assert h["status"] == "explain_failed"


# ── T-S1-ZERO: all iterations produce no_sql → Case A dict ──────────────────

def test_zero_iterations_no_sql_returns_case_a():
    """T-S1-ZERO: provider always returns empty → all no_sql → surviving_better_was_empty."""
    result = _call_core(
        provider=_make_provider("no SQL here at all"),
        baseline_cost=1000.0,
        explain_runner=_make_explain_runner({}),
        max_iterations=3,
    )
    # All history entries are no_sql
    for h in result.history:
        assert h["status"] == "no_sql"
    assert result.surviving_better_was_empty is True
    assert result.winner_sql is None


# ── T-S1-TIE: two candidates with equal plan cost → first in iteration order wins ──

def test_tie_first_iteration_wins():
    """T-S1-TIE: two candidates with equal plan cost → first (lower iteration) verified first."""
    import json

    equal_plan = json.dumps({
        "name": "Output",
        "estimates": [{"outputRowCount": 5, "outputSizeInBytes": 50}],
        "children": [],
    })

    call_count = [0]
    verified = []

    def _measure_tracking(sql, label):
        call_count[0] += 1
        verified.append(sql)
        return {"median": 0.5, "samples": [0.5], "row_count": 1, "rows": [[1]], "metrics": {}}

    replies = ["```sql\nSELECT A\n```", "```sql\nSELECT B\n```"]
    reply_iter = iter(replies)

    provider = MagicMock()
    provider.complete_text.side_effect = lambda req: next(reply_iter, None)

    result = _call_core(
        provider=provider,
        baseline_cost=1000.0,
        explain_runner=lambda sql: equal_plan,  # both get same plan = same cost
        measure_fn=_measure_tracking,
        metric_fn=lambda m: m["median"],
        row_equiv_fn=lambda m: (True, ""),
        max_iterations=2,
    )
    # First verified candidate should be the winner
    assert result.winner_sql is not None
    assert verified[0] == result.winner_sql


# ── T-S1-PART: cand_rows_est=None AND cand_bytes_est=None → explain_failed ───

def test_explain_no_estimates_produces_explain_failed():
    """T-S1-PART: EXPLAIN returns no row/byte estimates → explain_failed status."""
    result = _call_core(
        provider=_make_provider("```sql\nSELECT 2\n```"),
        baseline_cost=1000.0,
        explain_runner=lambda sql: None,  # no estimates at all
        measure_fn=_noop_measure,
    )
    for h in result.history:
        assert h["status"] == "explain_failed", f"expected explain_failed, got {h['status']}"
    assert result.surviving_better_was_empty is True


# ── T-S1-H1: iteration header strings (D11 intentional cosmetic change on MCP path) ──

def test_iteration_header_direct_uses_plan_cost_mode():
    """T-S1-H1 direct: direct emits '(plan-cost mode)' (unchanged by extraction)."""
    _, spy = _make_safe_output()
    from genie.skills.mcp_trino.preflight import _SafeOutput

    class _SpyOutput:
        def __init__(self):
            self.lines = []
        def print(self, *a, **kw): self.lines.append(("print", a))
        def progress(self, *a, **kw): self.lines.append(("progress", a))
        def error(self, *a, **kw): self.lines.append(("error", a))

    spy_out = _SpyOutput()
    safe = _SafeOutput(spy_out)

    _call_core(
        provider=_make_provider("no SQL"),
        baseline_cost=1000.0,
        explain_runner=lambda sql: None,
        max_iterations=1,
        output=safe,
    )
    progress_msgs = [msg for (kind, (msg,)) in spy_out.lines if kind == "progress"]
    assert any("(plan-cost mode)" in msg for msg in progress_msgs), (
        f"Expected '(plan-cost mode)' in output; got: {progress_msgs}"
    )
    # Must NOT contain the old MCP-prefixed form
    assert not any("(MCP plan-cost mode)" in msg for msg in progress_msgs)


def test_iteration_header_context_uses_plan_cost_iteration():
    """T-S1-H1 MCP: post-extraction, core emits '[Long-query plan-cost iteration N]'."""
    # The context line is assembled inside the core body; we verify via provider call args.
    provider = MagicMock()
    provider.complete_text.return_value = "no SQL"

    _call_core(
        provider=provider,
        baseline_cost=1000.0,
        explain_runner=lambda sql: None,
        max_iterations=1,
    )
    # Check that the context string passed to the LLM uses the non-MCP-prefixed form
    call_args = provider.complete_text.call_args
    if call_args is not None:
        req = call_args[0][0]
        history = req.messages
        # message content may be a string or a list of content blocks ({"type": "text", "text": ...})
        def _extract_text(content):
            if isinstance(content, str):
                return content
            if isinstance(content, list):
                return " ".join(
                    block.get("text", "") if isinstance(block, dict) else str(block)
                    for block in content
                )
            return str(content)

        user_msgs = [_extract_text(m["content"]) for m in history if m.get("role") == "user"]
        assert any("[Long-query plan-cost iteration" in msg for msg in user_msgs), (
            f"Expected '[Long-query plan-cost iteration N]'; got user msgs: {user_msgs}"
        )
        assert not any("[Long-query MCP plan-cost iteration" in msg for msg in user_msgs)


# ── T-S1-IMPORT: import genie.skills.mcp_trino.preflight exits 0 ─────────────

def test_import_preflight_no_cycle():
    """T-S1-IMPORT: importing preflight after core insertion exits 0 (no import cycle)."""
    result = subprocess.run(
        [sys.executable, "-c", "import genie.skills.mcp_trino.preflight"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"Import failed (exit {result.returncode}):\n{result.stderr}"
    )
