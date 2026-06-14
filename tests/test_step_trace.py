"""tests/test_step_trace.py — Unit tests for genie.output.step_trace (T9, v48)."""
from __future__ import annotations

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
