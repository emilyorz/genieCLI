"""tests/test_step_trace.py — Unit tests for genie.output.step_trace (T9, v48)."""
from __future__ import annotations

import dataclasses
from typing import Any, List
from unittest.mock import MagicMock

import pytest

from genie.output.step_trace import (
    CANONICAL_COPY,
    StepEvent,
    StepStatus,
    StepTrace,
    render_report,
    render_tui,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ev(
    step_id: str,
    stage: str,
    status: StepStatus,
    headline: str = "",
    detail: dict | None = None,
    na_reason: str = "",
    applicable: bool = True,
) -> StepEvent:
    return StepEvent(
        step_id=step_id,
        stage=stage,
        status=status,
        applicable=applicable,
        tui_headline=headline,
        detail=detail or {},
        na_reason=na_reason,
    )


# ---------------------------------------------------------------------------
# StepStatus enum
# ---------------------------------------------------------------------------

class TestStepStatus:
    def test_values_are_strings(self):
        assert StepStatus.RAN == "ran"
        assert StepStatus.SKIPPED == "skipped"
        assert StepStatus.NA == "na"
        assert StepStatus.DEGRADED == "degraded"


# ---------------------------------------------------------------------------
# CANONICAL_COPY
# ---------------------------------------------------------------------------

class TestCanonicalCopy:
    def test_all_keys_present(self):
        assert "NO_SAFE_REWRITE_BEAT_ORIGINAL" in CANONICAL_COPY
        assert "RECOMPOSE_SCAN_INCONCLUSIVE" in CANONICAL_COPY
        assert "SEED_REJECTED" in CANONICAL_COPY

    def test_values_are_non_empty_strings(self):
        for key, val in CANONICAL_COPY.items():
            assert isinstance(val, str) and val.strip(), f"Empty value for key {key}"


# ---------------------------------------------------------------------------
# render_tui
# ---------------------------------------------------------------------------

class TestRenderTui:
    def test_empty_trace_returns_empty_string(self):
        assert render_tui([]) == ""

    def test_ran_event_uses_check_symbol(self):
        ev = _ev("baseline", "Baseline", StepStatus.RAN, "1 500.0 ms / 10 rows")
        out = render_tui([ev])
        assert "✓" in out
        assert "Baseline" in out
        assert "1 500.0 ms" in out

    def test_skipped_event_uses_tilde_symbol(self):
        ev = _ev("decompose", "Decompose", StepStatus.SKIPPED, "single SELECT", applicable=True)
        out = render_tui([ev])
        assert "~" in out
        assert "skipped" in out

    def test_na_event_uses_dot_symbol(self):
        ev = _ev(
            "decompose", "Decompose", StepStatus.NA,
            headline="", na_reason="no provider", applicable=False,
        )
        out = render_tui([ev])
        assert "·" in out
        assert "N/A" in out
        assert "no provider" in out

    def test_degraded_event_uses_exclamation(self):
        ev = _ev("recompose", "Recompose", StepStatus.DEGRADED, "LLM timeout")
        out = render_tui([ev])
        assert "!" in out
        assert "DEGRADED" in out

    def test_over_cap_fragments_collapsed(self):
        trace: StepTrace = []
        # 7 fragments — 5 normal, 2 over-cap
        for i in range(1, 6):
            trace.append(_ev(f"fragment_{i}", f"Fragment {i}", StepStatus.RAN, "optimized"))
        for i in range(6, 8):
            trace.append(_ev(
                f"fragment_{i}", f"Fragment {i}", StepStatus.SKIPPED, "over_cap",
                detail={"action": "over_cap"},
            ))
        out = render_tui(trace)
        # over-cap lines collapsed into one summary
        assert "2 lower-rank fragment(s) not optimized" in out
        # normal fragments still shown
        assert "Fragment 1" in out

    def test_iterations_collapsed_after_10(self):
        trace: StepTrace = []
        for i in range(1, 14):
            trace.append(_ev(f"iteration_{i}", f"Iteration {i}", StepStatus.RAN, "ran"))
        out = render_tui(trace)
        assert "3 more iterations" in out
        # First 10 are rendered
        assert "Iteration 1" in out
        assert "Iteration 10" in out
        # 11th should NOT appear (collapsed)
        assert "Iteration 11" not in out

    def test_iterations_not_collapsed_at_exactly_10(self):
        trace: StepTrace = []
        for i in range(1, 11):
            trace.append(_ev(f"iteration_{i}", f"Iteration {i}", StepStatus.RAN, "ran"))
        out = render_tui(trace)
        assert "more iterations" not in out
        assert "Iteration 10" in out


# ---------------------------------------------------------------------------
# render_report
# ---------------------------------------------------------------------------

class TestRenderReport:
    def test_empty_trace_returns_step_summary_header(self):
        out = render_report([])
        assert "Step Summary" in out

    def test_summary_table_contains_all_events(self):
        trace = [
            _ev("baseline", "Baseline", StepStatus.RAN, "200 ms"),
            _ev("decompose", "Decompose", StepStatus.SKIPPED, "trivial"),
            _ev("verify", "Verify", StepStatus.NA, na_reason="no rows", applicable=False),
        ]
        out = render_report(trace)
        assert "Baseline" in out
        assert "Decompose" in out
        assert "Verify" in out
        assert "✓ ran" in out
        assert "~ skipped" in out
        assert "· N/A" in out

    def test_detail_subsection_rendered_for_ran_with_detail(self):
        trace = [
            _ev(
                "iteration_1", "Iteration 1", StepStatus.RAN, "improved",
                detail={
                    "hypothesis": "push filter early",
                    "metric_before": 1000,
                    "metric_after": 800,
                    "verdict": "improved",
                },
            )
        ]
        out = render_report(trace)
        assert "push filter early" in out
        assert "1,000 ms" in out or "1000" in out
        assert "800" in out

    def test_na_events_listed_in_skipped_section(self):
        trace = [
            _ev("verify", "Verify", StepStatus.NA, na_reason="no live data", applicable=False),
        ]
        out = render_report(trace)
        assert "Skipped / N/A" in out
        assert "no live data" in out

    def test_report_never_collapses_iterations(self):
        trace = [
            _ev(f"iteration_{i}", f"Iteration {i}", StepStatus.RAN, "ran")
            for i in range(1, 16)
        ]
        out = render_report(trace)
        # All 15 appear in the summary table
        assert "Iteration 15" in out
        assert "more iterations" not in out

    def test_degraded_detail_included(self):
        trace = [
            _ev(
                "decompose", "Decompose", StepStatus.DEGRADED, "LLM timeout",
                detail={"fragment_count": 0, "fragment_ids": []},
            )
        ]
        out = render_report(trace)
        assert "! DEGRADED" in out


# ---------------------------------------------------------------------------
# v50 Fix 2: _fmt_detail_fragment renders SQL in report
# ---------------------------------------------------------------------------

class TestFragmentSqlInReport:
    """v50 — Test 1, Test 2: fragment SQL rendered in render_report."""

    def test_should_render_fragment_sql_in_report_when_decomposed(self):
        """Test 1: fragment detail with 'sql' → render_report includes that SQL.

        Uses RAN status so render_report emits the detail section (SKIPPED fragments
        are in the summary table but their detail blocks are intentionally omitted by
        render_report — this is correct behaviour for unchanged fragments).
        """
        subquery_sql = "SELECT 1 FROM refunds r WHERE r.order_id = o.id"
        trace = [
            _ev(
                "fragment_1", "Fragment 1/2", StepStatus.RAN,
                headline="__subquery_0__: optimized",
                detail={
                    "fragment_id": "__subquery_0__",
                    "role": "subquery",
                    "action": "optimized",
                    "sql": subquery_sql,
                },
            )
        ]
        out = render_report(trace)
        assert subquery_sql in out

    def test_should_render_before_after_when_fragment_optimized(self):
        """Test 2: optimized fragment with original_sql != rewritten_sql → both appear."""
        original_sql = "SELECT * FROM orders WHERE status = 'PENDING'"
        rewritten_sql = "SELECT id, amount FROM orders WHERE status = 'PENDING'"
        trace = [
            _ev(
                "fragment_1", "Fragment 1/1", StepStatus.RAN,
                headline="__root__: optimized",
                detail={
                    "fragment_id": "__root__",
                    "role": "root",
                    "action": "optimized",
                    "sql": original_sql,
                    "original_sql": original_sql,
                    "rewritten_sql": rewritten_sql,
                },
            )
        ]
        out = render_report(trace)
        assert original_sql in out
        assert rewritten_sql in out

    def test_should_render_fragment_sql_when_fragment_unchanged(self):
        """v50 review fix: a SKIPPED (unchanged) fragment must STILL show its SQL.

        Regression guard for the common case: for the EXISTS example both fragments
        are unchanged → status SKIPPED. Before the fix render_report gated detail on
        RAN/DEGRADED only, so unchanged fragments showed an ID with no SQL.
        """
        subquery_sql = "SELECT 1 FROM refunds r WHERE r.order_id = o.id"
        trace = [
            _ev(
                "fragment_2", "Fragment 2/2", StepStatus.SKIPPED,
                headline="__subquery_0__: unchanged",
                detail={
                    "fragment_id": "__subquery_0__",
                    "role": "subquery",
                    "action": "unchanged",
                    "sql": subquery_sql,
                },
            )
        ]
        out = render_report(trace)
        assert subquery_sql in out


# ---------------------------------------------------------------------------
# v50 Fix 2: render_tui shows fragment id + action
# ---------------------------------------------------------------------------

class TestFragmentActionsInTuiBreadcrumb:
    """v50 — Test 3: render_tui includes fragment id and action token."""

    def test_should_render_fragment_actions_in_tui_breadcrumb(self):
        """Test 3: 2-fragment trace → render_tui output contains fragment ids and actions."""
        trace = [
            _ev(
                "decompose", "Decompose", StepStatus.RAN,
                headline="2 fragment(s) identified (__root__, __subquery_0__)",
                detail={"fragment_count": 2, "fragment_ids": ["__root__", "__subquery_0__"]},
            ),
            _ev(
                "fragment_1", "Fragment 1/2", StepStatus.SKIPPED,
                headline="__root__: unchanged",
                detail={"fragment_id": "__root__", "action": "unchanged"},
            ),
            _ev(
                "fragment_2", "Fragment 2/2", StepStatus.RAN,
                headline="__subquery_0__: optimized",
                detail={"fragment_id": "__subquery_0__", "action": "optimized"},
            ),
        ]
        out = render_tui(trace)
        assert "__subquery_0__" in out
        assert "optimized" in out


# ---------------------------------------------------------------------------
# v50 Fix 3: live breadcrumb emitted through _seed_decompose_and_select
# ---------------------------------------------------------------------------

class TestLiveBreadcrumbThroughSeedDecompose:
    """v50 — Test 4: spy output sink observes breadcrumb for BOTH MCP and --direct call patterns."""

    def _make_measure_result(self):
        """Build a minimal MeasureResult for test use."""
        from genie.skills.mcp_trino.research import MeasureResult
        try:
            from genie.skills.mcp_trino.research import RunMetrics
            metrics = RunMetrics(cpu_ms=0, wall_ms=100, peak_memory_bytes=0, processed_rows=0, processed_bytes=0)
        except Exception:
            metrics = {}
        return MeasureResult(
            median_metric=100.0,
            samples=[100.0],
            row_count=1,
            rows=[],
            columns=[],
            metrics=metrics,
        )

    def _make_two_fragment_trace(self) -> list:
        """Return a trace with decompose + 2 fragment events (simulates produce_fn output)."""
        return [
            StepEvent(
                step_id="decompose",
                stage="Decompose",
                status=StepStatus.RAN,
                applicable=True,
                tui_headline="2 fragment(s) identified (__root__, __subquery_0__)",
                detail={
                    "fragment_count": 2,
                    "fragment_ids": ["__root__", "__subquery_0__"],
                },
            ),
            StepEvent(
                step_id="fragment_1",
                stage="Fragment 1/2",
                status=StepStatus.SKIPPED,
                applicable=True,
                tui_headline="__root__: unchanged",
                detail={"fragment_id": "__root__", "action": "unchanged",
                        "sql": "SELECT * FROM orders"},
            ),
            StepEvent(
                step_id="fragment_2",
                stage="Fragment 2/2",
                status=StepStatus.SKIPPED,
                applicable=True,
                tui_headline="__subquery_0__: unchanged",
                detail={"fragment_id": "__subquery_0__", "action": "unchanged",
                        "sql": "SELECT 1 FROM refunds"},
            ),
        ]

    def _call_seed_mcp_style(self, spy_output, shared_trace):
        """Drive _seed_decompose_and_select the way the MCP standard loop does."""
        from genie.skills.mcp_trino.research import _seed_decompose_and_select
        baseline = self._make_measure_result()

        # produce_fn populates trace (as real MCP does via _produce_decompose_candidate)
        # and returns original sql unchanged (simplest path that still runs the breadcrumb)
        def _produce_fn(sql: str):
            shared_trace.extend(self._make_two_fragment_trace())
            # return original sql to skip the measure step
            return sql, [], [], None

        _seed_decompose_and_select(
            "SELECT * FROM orders",
            baseline,
            produce_fn=_produce_fn,
            measure_fn=lambda s: baseline,
            flag_enabled=True,
            output=spy_output,
            trace=shared_trace,
        )

    def _call_seed_direct_style(self, spy_output, shared_trace):
        """Drive _seed_decompose_and_select the way the --direct standard loop does."""
        from genie.skills.mcp_trino.research import _seed_decompose_and_select
        baseline = self._make_measure_result()

        def _produce_fn(sql: str):
            shared_trace.extend(self._make_two_fragment_trace())
            return sql, [], [], None

        _seed_decompose_and_select(
            "SELECT * FROM orders",
            baseline,
            produce_fn=_produce_fn,
            measure_fn=lambda s: baseline,
            flag_enabled=True,
            output=spy_output,
            trace=shared_trace,
        )

    def test_should_emit_live_breadcrumb_through_seed_decompose(self):
        """Test 4: spy output captures breadcrumb containing fragment ids for BOTH call patterns."""
        # MCP-style call
        mcp_captured: list[str] = []
        mcp_spy = MagicMock()
        mcp_spy.progress.side_effect = lambda msg: mcp_captured.append(msg)
        mcp_trace: list = []
        self._call_seed_mcp_style(mcp_spy, mcp_trace)
        mcp_combined = "\n".join(mcp_captured)
        assert "__root__" in mcp_combined or "__subquery_0__" in mcp_combined, (
            f"MCP spy did not capture breadcrumb with fragment ids. "
            f"Captured: {mcp_captured!r}"
        )

        # --direct style call
        direct_captured: list[str] = []
        direct_spy = MagicMock()
        direct_spy.progress.side_effect = lambda msg: direct_captured.append(msg)
        direct_trace: list = []
        self._call_seed_direct_style(direct_spy, direct_trace)
        direct_combined = "\n".join(direct_captured)
        assert "__root__" in direct_combined or "__subquery_0__" in direct_combined, (
            f"--direct spy did not capture breadcrumb with fragment ids. "
            f"Captured: {direct_captured!r}"
        )


# ---------------------------------------------------------------------------
# v50 Fix 1: _produce_decompose_candidate carries sql in StepEvent detail
# ---------------------------------------------------------------------------

class TestFragmentSqlInEventDetail:
    """v50 — Test 5: ev_detail from _produce_decompose_candidate has 'sql' key."""

    def test_should_carry_fragment_sql_in_event_detail(self):
        """Test 5: per-fragment StepEvents emitted by _produce_decompose_candidate have 'sql'."""
        from unittest.mock import patch

        from genie.skills.mcp_trino.research import _produce_decompose_candidate

        SUBQUERY_SQL = "SELECT 1 FROM refunds r WHERE r.order_id = o.id"
        ROOT_SQL = "SELECT * FROM orders o WHERE o.amount > 100 AND EXISTS (__subquery_0__)"

        # Build fake fragment objects
        @dataclasses.dataclass
        class FakeFragment:
            fragment_id: str
            sql: str
            is_monster: bool
            role: str = "query"
            monster_rank: int = 0

        fake_frags = [
            FakeFragment("__root__", ROOT_SQL, is_monster=False),
            FakeFragment("__subquery_0__", SUBQUERY_SQL, is_monster=False),
        ]

        # Stub decompose to return our fake fragments
        with patch(
            "genie.skills.mcp_trino.trino_optimize.decompose",
            return_value=fake_frags,
        ), patch(
            "genie.skills.mcp_trino.trino_optimize.optimize",
        ) as mock_optimize, patch(
            "genie.skills.mcp_trino.trino_optimize.recompose",
        ) as mock_recompose, patch(
            "genie.skills.trino_query.detection_scan.scan_sql",
            return_value=MagicMock(),
        ), patch(
            "genie.skills.mcp_trino.write_analysis._column_safe_candidates",
            side_effect=lambda cands: (cands, []),
        ), patch(
            "genie.skills.mcp_trino.write_analysis._semantic_safe_candidates",
            side_effect=lambda cands: (cands, []),
        ):
            # optimize returns an unchanged RewriteCandidate for each fragment
            from genie.skills.mcp_trino.trino_optimize import RewriteCandidate
            def fake_optimize(fr, llm_fn):
                return RewriteCandidate(
                    fragment_id=fr.fragment_id,
                    original_sql=fr.sql,
                    rewritten_sql=fr.sql,
                    action="unchanged",
                    changed=False,
                    admitted=True,
                    rationale="test passthrough",
                )
            mock_optimize.side_effect = fake_optimize

            from genie.skills.mcp_trino.trino_optimize import RecomposeResult, RecomposeStatus
            mock_recompose.return_value = MagicMock(
                sql=ROOT_SQL,
                status=RecomposeStatus.OK,
                reverted_fragments=[],
                scan_ok_confident=True,
            )

            step_trace: list = []
            _produce_decompose_candidate(
                sql="SELECT * FROM orders o WHERE o.amount > 100",
                llm_fn=None,
                cost_reader_fn=None,
                run_static_gates=False,
                step_trace=step_trace,
            )

        # All fragment_* events must have 'sql' in detail
        frag_events = [ev for ev in step_trace if ev.step_id.startswith("fragment_")]
        assert len(frag_events) >= 1, "Expected at least one fragment_* StepEvent"
        for ev in frag_events:
            assert "sql" in ev.detail, (
                f"fragment event {ev.step_id} missing 'sql' in detail: {ev.detail!r}"
            )

    def test_should_carry_original_and_rewritten_sql_when_optimized(self):
        """Test 5b: optimized fragment (changed=True, admitted=True) has original_sql + rewritten_sql."""
        import dataclasses as _dc
        from unittest.mock import patch

        from genie.skills.mcp_trino.research import _produce_decompose_candidate
        from genie.skills.mcp_trino.trino_optimize import RewriteCandidate, RecomposeStatus

        ORIG = "SELECT * FROM big_table"
        REWRITTEN = "SELECT id, val FROM big_table"

        @_dc.dataclass
        class FakeFragment:
            fragment_id: str
            sql: str
            is_monster: bool
            role: str = "query"
            monster_rank: int = 1  # monster rank so it gets optimized

        fake_frag = FakeFragment("__root__", ORIG, is_monster=True)

        with patch(
            "genie.skills.mcp_trino.trino_optimize.decompose",
            return_value=[fake_frag],
        ), patch(
            "genie.skills.mcp_trino.trino_optimize.optimize",
            return_value=RewriteCandidate(
                fragment_id="__root__",
                original_sql=ORIG,
                rewritten_sql=REWRITTEN,
                action="optimized",
                changed=True,
                admitted=True,
                rationale="column pruning",
            ),
        ), patch(
            "genie.skills.mcp_trino.trino_optimize.recompose",
            return_value=MagicMock(
                sql=REWRITTEN,
                status=RecomposeStatus.OK,
                reverted_fragments=[],
                scan_ok_confident=True,
            ),
        ), patch(
            "genie.skills.trino_query.detection_scan.scan_sql",
            return_value=MagicMock(),
        ), patch(
            "genie.skills.mcp_trino.write_analysis._column_safe_candidates",
            side_effect=lambda cands: (cands, []),
        ), patch(
            "genie.skills.mcp_trino.write_analysis._semantic_safe_candidates",
            side_effect=lambda cands: (cands, []),
        ):
            step_trace: list = []
            _produce_decompose_candidate(
                sql=ORIG,
                llm_fn=None,
                cost_reader_fn=None,
                run_static_gates=False,
                step_trace=step_trace,
            )

        frag_events = [ev for ev in step_trace if ev.step_id.startswith("fragment_")]
        assert len(frag_events) == 1
        d = frag_events[0].detail
        assert d.get("original_sql") == ORIG
        assert d.get("rewritten_sql") == REWRITTEN
