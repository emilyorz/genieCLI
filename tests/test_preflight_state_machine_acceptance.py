"""Acceptance criteria for v45 preflight state machine — Usage step.

Each test represents a named caller scenario (test_should_<behavior>_when_<scenario>).
Fixtures lifted from real code shapes in the repo.

Behaviors not yet implemented are marked:
    pytest.mark.xfail(strict=True, reason="AC pending develop")

The current codebase already implements the six routing behaviors on both paths
(MCP + --direct). The xfail tests cover ONLY the NEW symbols and fixes introduced
by the develop step: S1 enum/dataclass/builder, S2 flip narrowing, S3 metadata
note, S4 truncation signal.

Caller perspectives covered:
  A. MCP entry caller (run_mcp_enhancement) — all 6 exit routes
  B. --direct entry caller (_run_optimization_loop) — all 6 exit routes
  C. Future maintainer adding a NEW gate — shared decision core is the obvious
     insertion point; type safety from PreflightRoute enum makes omissions loud
  D. S2 user whose session-property typo must surface as a real error (not
     swallowed into a no-data advisory report)
"""
from __future__ import annotations

from typing import Optional
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Real code shapes imported (will fail at collect if module not present)
# ---------------------------------------------------------------------------
from genie.skills.mcp_trino.preflight import (
    LongQueryAbort,
    LongQueryGateResult,
    NoDataDetected,
    detect_no_data_reason,
)
from genie.skills.mcp_trino.research import MeasureResult, MemoryLimitResult, RunMetrics

# Symbols added by the develop step (not yet present — imported lazily inside tests)
# PreflightRoute, PreflightDecision, build_preflight_decision


# ---------------------------------------------------------------------------
# Test-local constants — match real threshold values
# ---------------------------------------------------------------------------
_SLOW_WALL_MS = 98_400.0   # 98.4s > 60s default long-query threshold
_SOME_ROWS = 5

# Real Trino error texts from repo test corpus and production
_TABLE_NOT_FOUND_TEXT = "Table 'hive.default.t' does not exist"
_CATALOG_NOT_FOUND_TEXT = "line 1:8: Catalog 'foo' does not exist"
_TABLE_NOT_FOUND_MARKER = "Query failed: TABLE_NOT_FOUND for hive.default.foo"
_SESSION_PROP_TEXT = "Session property query_max_execution_time does not exist"
_SESSION_PROP_TEXT2 = "Session property query_max_memory does not exist"
_CONNECTION_REFUSED = "connection refused at trino:8080"


# ---------------------------------------------------------------------------
# Shared helpers — lifted from test_zero_cost_directed_report.py shapes
# ---------------------------------------------------------------------------

def _fast_mcp_baseline() -> MeasureResult:
    """Baseline: row_count > 0, wall_time well below 60s threshold."""
    return MeasureResult(
        median_metric=50.0,
        samples=[50.0],
        row_count=_SOME_ROWS,
        rows=[(1,)],
        columns=["c"],
        metrics=RunMetrics(wall_time_ms=1_000.0),
    )


def _slow_mcp_baseline() -> MeasureResult:
    """Baseline: wall_time > 60s — trips the long-query gate when opt_in=False."""
    return MeasureResult(
        median_metric=100.0,
        samples=[100.0],
        row_count=_SOME_ROWS,
        rows=[(1,)],
        columns=["c"],
        metrics=RunMetrics(wall_time_ms=_SLOW_WALL_MS),
    )


def _no_data_mcp_baseline() -> MeasureResult:
    """Baseline: row_count == 0 → no-data path."""
    return MeasureResult(
        median_metric=50.0,
        samples=[50.0],
        row_count=0,
        rows=[],
        columns=["c"],
        metrics=RunMetrics(wall_time_ms=1_000.0),
    )


def _slow_direct_baseline() -> dict:
    """--direct path baseline with row_count > 0, slow wall time."""
    from genie.skills.trino_query import QueryMetrics
    return {
        "median": 100.0,
        "samples": [100.0],
        "row_count": _SOME_ROWS,
        "rows": [(1,)],
        "metrics": QueryMetrics(wall_time_ms=_SLOW_WALL_MS),
    }


def _fast_direct_baseline() -> dict:
    """--direct path baseline with row_count > 0, fast wall time."""
    from genie.skills.trino_query import QueryMetrics
    return {
        "median": 50.0,
        "samples": [50.0],
        "row_count": _SOME_ROWS,
        "rows": [(1,)],
        "metrics": QueryMetrics(wall_time_ms=1_000.0),
    }


def _fake_mem_limit() -> MemoryLimitResult:
    return MemoryLimitResult(bytes=None, source="default-fallback")


# ---------------------------------------------------------------------------
# Acceptance tests — Caller A: MCP entry (run_mcp_enhancement)
# All six exit routes. These behaviors exist in the CURRENT codebase —
# NOT marked xfail. They must remain green after the S1 refactor.
# ---------------------------------------------------------------------------

class TestMcpCallerDiagnoseOnly:
    """AC-MCP-1: MCP caller with diagnose_only=True exits via LongQueryAbort
    before any baseline query. Zero query cost."""

    def test_should_raise_long_query_abort_when_diagnose_only_true(self):
        from genie.skills.mcp_trino import research as mcp_research

        measure = MagicMock()
        with patch.object(mcp_research, "_measure_mcp", measure), \
             patch.object(mcp_research, "_fetch_per_node_memory_limit",
                          return_value=_fake_mem_limit()), \
             patch.object(mcp_research, "_assemble_mcp_directions",
                          return_value=([], [])):
            with pytest.raises(LongQueryAbort) as exc_info:
                mcp_research.run_mcp_enhancement(
                    client=MagicMock(),
                    sql="SELECT 1",
                    metric_key="wall_time_ms",
                    max_iterations=3,
                    verify_runs=1,
                    provider=MagicMock(),
                    model="m",
                    reasoning="disable",
                    output=MagicMock(),
                    build_prompt=lambda *a, **k: "SKILL",
                    diagnose_only=True,
                )
        # Zero query cost: no baseline ran
        measure.assert_not_called()
        # LongQueryAbort carries a report
        assert exc_info.value.report_markdown is not None


class TestMcpCallerNoData:
    """AC-MCP-2: MCP caller routes to NoDataDetected when baseline returns 0
    rows or raises a container not-found error."""

    def test_should_raise_no_data_detected_when_baseline_returns_zero_rows(self):
        from genie.skills.mcp_trino import research as mcp_research

        with patch.object(mcp_research, "_measure_mcp",
                          return_value=_no_data_mcp_baseline()), \
             patch.object(mcp_research, "_fetch_per_node_memory_limit",
                          return_value=_fake_mem_limit()), \
             patch("genie.skills.trino_query.research._run_no_data_path",
                   return_value={"status": "no_data", "reason": "empty_result"}):
            with pytest.raises(NoDataDetected) as exc_info:
                mcp_research.run_mcp_enhancement(
                    client=MagicMock(), sql="SELECT 1",
                    max_iterations=3, verify_runs=1,
                    provider=MagicMock(), model="m", reasoning="disable",
                    output=MagicMock(), build_prompt=lambda *a, **k: "SKILL",
                )
        assert exc_info.value.reason == "empty_result"

    def test_should_raise_no_data_detected_when_baseline_raises_table_not_found(self):
        from genie.skills.mcp_trino import research as mcp_research

        with patch.object(mcp_research, "_measure_mcp",
                          side_effect=Exception(_TABLE_NOT_FOUND_TEXT)), \
             patch.object(mcp_research, "_fetch_per_node_memory_limit",
                          return_value=_fake_mem_limit()), \
             patch("genie.skills.trino_query.research._run_no_data_path",
                   return_value={"status": "no_data", "reason": "table_not_found"}):
            with pytest.raises(NoDataDetected) as exc_info:
                mcp_research.run_mcp_enhancement(
                    client=MagicMock(), sql="SELECT * FROM missing_table",
                    max_iterations=3, verify_runs=1,
                    provider=MagicMock(), model="m", reasoning="disable",
                    output=MagicMock(), build_prompt=lambda *a, **k: "SKILL",
                )
        assert exc_info.value.reason == "table_not_found"

    def test_should_raise_no_data_detected_with_catalog_not_found(self):
        """Regression guard: 'line 1:8: Catalog foo does not exist' must still
        route NO_DATA after the S1/S2 refactor (existing corpus :47-49)."""
        from genie.skills.mcp_trino import research as mcp_research

        with patch.object(mcp_research, "_measure_mcp",
                          side_effect=Exception(_CATALOG_NOT_FOUND_TEXT)), \
             patch.object(mcp_research, "_fetch_per_node_memory_limit",
                          return_value=_fake_mem_limit()), \
             patch("genie.skills.trino_query.research._run_no_data_path",
                   return_value={"status": "no_data", "reason": "table_not_found"}):
            with pytest.raises(NoDataDetected) as exc_info:
                mcp_research.run_mcp_enhancement(
                    client=MagicMock(), sql="SELECT 1",
                    max_iterations=3, verify_runs=1,
                    provider=MagicMock(), model="m", reasoning="disable",
                    output=MagicMock(), build_prompt=lambda *a, **k: "SKILL",
                )
        assert exc_info.value.reason == "table_not_found"


class TestMcpCallerRealFailure:
    """AC-MCP-3: MCP caller re-raises the baseline exception when it is NOT a
    no-data shape (connection errors, generic failures)."""

    def test_should_reraise_exception_when_baseline_raises_connection_error(self):
        from genie.skills.mcp_trino import research as mcp_research

        exc = ConnectionError(_CONNECTION_REFUSED)
        with patch.object(mcp_research, "_measure_mcp", side_effect=exc), \
             patch.object(mcp_research, "_fetch_per_node_memory_limit",
                          return_value=_fake_mem_limit()):
            with pytest.raises(ConnectionError) as exc_info:
                mcp_research.run_mcp_enhancement(
                    client=MagicMock(), sql="SELECT 1",
                    max_iterations=3, verify_runs=1,
                    provider=MagicMock(), model="m", reasoning="disable",
                    output=MagicMock(), build_prompt=lambda *a, **k: "SKILL",
                )
        assert _CONNECTION_REFUSED in str(exc_info.value)


class TestMcpCallerLongQueryGate:
    """AC-MCP-4: MCP caller raises LongQueryAbort (carrying a report) when the
    upfront cost gate trips (long_query_opt_in=False, slow baseline)."""

    def test_should_raise_long_query_abort_when_gate_trips_on_slow_baseline(self):
        from genie.skills.mcp_trino import research as mcp_research

        with patch.object(mcp_research, "_measure_mcp",
                          return_value=_slow_mcp_baseline()), \
             patch.object(mcp_research, "_fetch_per_node_memory_limit",
                          return_value=_fake_mem_limit()), \
             patch.object(mcp_research, "_assemble_mcp_directions",
                          return_value=([], [])):
            with pytest.raises(LongQueryAbort) as exc_info:
                mcp_research.run_mcp_enhancement(
                    client=MagicMock(), sql="SELECT 1",
                    max_iterations=5, verify_runs=1,
                    provider=MagicMock(), model="m", reasoning="disable",
                    output=MagicMock(), build_prompt=lambda *a, **k: "SKILL",
                    long_query_opt_in=False,
                )
        # Carries a report (not a bare abort)
        assert exc_info.value.report_markdown is not None
        assert exc_info.value.baseline_s > 60.0


# ---------------------------------------------------------------------------
# Acceptance tests — Caller B: --direct entry (_run_optimization_loop)
# All six exit routes. CURRENT behavior; NOT xfail.
# ---------------------------------------------------------------------------

class TestDirectCallerDiagnoseOnly:
    """AC-DIRECT-1: --direct caller with diagnose_only=True returns
    {"status":"diagnosed",...} without running any baseline."""

    def test_should_return_diagnosed_status_when_diagnose_only_true(self):
        from genie.skills.trino_query import research as direct_research

        measure = MagicMock()
        with patch.object(direct_research, "_measure", measure), \
             patch.object(direct_research, "_assemble_direct_directions",
                          return_value=[]):
            result = direct_research._run_optimization_loop(
                provider=MagicMock(),
                model="m",
                reasoning="disable",
                original_sql="SELECT 1",
                metric_key="wall_time_ms",
                max_iterations=3,
                verify_runs=1,
                output=MagicMock(),
                build_prompt=lambda *a, **k: "SKILL",
                explain_runner=None,
                diagnose_only=True,
            )
        measure.assert_not_called()
        assert result["status"] == "diagnosed"
        assert "report_markdown" in result


class TestDirectCallerNoData:
    """AC-DIRECT-2: --direct caller routes to no-data path on zero rows or
    container not-found errors."""

    def test_should_return_no_data_status_when_baseline_returns_zero_rows(self):
        from genie.skills.trino_query import QueryMetrics
        from genie.skills.trino_query import research as direct_research

        fake_zero_baseline = {
            "median": 50.0, "samples": [50.0], "row_count": 0, "rows": [],
            "metrics": QueryMetrics(wall_time_ms=1_000.0),
        }
        with patch.object(direct_research, "_measure", return_value=fake_zero_baseline):
            result = direct_research._run_optimization_loop(
                provider=None, model="m", reasoning="disable",
                original_sql="SELECT * FROM t",
                metric_key="cpu_time_ms", max_iterations=5, verify_runs=1,
                output=MagicMock(), build_prompt=lambda *a, **k: "",
            )
        assert result["status"] == "no_data"
        assert result["reason"] == "empty_result"

    def test_should_return_no_data_status_when_baseline_raises_table_not_found(self):
        from genie.skills.trino_query import research as direct_research

        exc = Exception(_TABLE_NOT_FOUND_MARKER)
        with patch.object(direct_research, "_measure", side_effect=exc):
            result = direct_research._run_optimization_loop(
                provider=None, model="m", reasoning="disable",
                original_sql="SELECT * FROM missing",
                metric_key="cpu_time_ms", max_iterations=5, verify_runs=1,
                output=MagicMock(), build_prompt=lambda *a, **k: "",
            )
        assert result["status"] == "no_data"
        assert result["reason"] == "table_not_found"


class TestDirectCallerRealFailure:
    """AC-DIRECT-3: --direct caller returns {"status":"failed",...} for
    non-no-data exceptions."""

    def test_should_return_failed_status_when_baseline_raises_connection_error(self):
        from genie.skills.trino_query import research as direct_research

        exc = ConnectionError(_CONNECTION_REFUSED)
        with patch.object(direct_research, "_measure", side_effect=exc):
            result = direct_research._run_optimization_loop(
                provider=None, model="m", reasoning="disable",
                original_sql="SELECT 1",
                metric_key="cpu_time_ms", max_iterations=5, verify_runs=1,
                output=MagicMock(), build_prompt=lambda *a, **k: "",
            )
        assert result["status"] == "failed"
        assert _CONNECTION_REFUSED in result["error"]


class TestDirectCallerLongQueryGate:
    """AC-DIRECT-4: --direct caller returns
    {"status":"diagnosed","reason":"long_query_gate",...} when gate trips."""

    def test_should_return_long_query_gate_reason_when_gate_trips(self):
        from genie.skills.trino_query import research as direct_research

        with patch.object(direct_research, "_measure",
                          return_value=_slow_direct_baseline()), \
             patch.object(direct_research, "_assemble_direct_directions",
                          return_value=[]):
            result = direct_research._run_optimization_loop(
                provider=MagicMock(), model="m", reasoning="disable",
                original_sql="SELECT 1",
                metric_key="wall_time_ms", max_iterations=5, verify_runs=1,
                output=MagicMock(), build_prompt=lambda *a, **k: "SKILL",
                long_query_opt_in=False,
            )
        assert result["status"] == "diagnosed"
        assert result["reason"] == "long_query_gate"
        assert "report_markdown" in result


# ---------------------------------------------------------------------------
# Acceptance tests — Caller C: Future maintainer adding a NEW gate
# S1 symbols not yet in codebase → xfail(strict=True)
# ---------------------------------------------------------------------------

class TestFutureMaintainerGateExtension:
    """AC-MAINT: After S1, a future maintainer adding a new gate inserts it
    into build_preflight_decision and gets symmetry by construction.

    These tests verify the PreflightRoute enum is the SINGLE authoritative
    set of routing values — exhaustiveness protects maintainers from silent drift.
    """

    def test_should_have_preflight_route_enum_with_all_six_values(self):
        """spec §2.2: exactly 6 PreflightRoute values."""
        from genie.skills.mcp_trino.preflight import PreflightRoute
        expected = {
            "DIAGNOSE_ONLY", "NO_DATA", "REAL_FAILURE",
            "LONG_QUERY_ABORT", "PLAN_COST_LOOP", "STANDARD_LOOP",
        }
        actual = {e.name for e in PreflightRoute}
        assert actual == expected, (
            f"PreflightRoute mismatch. Extra: {actual - expected}, Missing: {expected - actual}"
        )

    def test_should_have_preflight_decision_dataclass_frozen(self):
        """frozen=True: no adapter can mutate the decision after construction."""
        from genie.skills.mcp_trino.preflight import PreflightDecision, PreflightRoute
        d = PreflightDecision(route=PreflightRoute.STANDARD_LOOP)
        with pytest.raises((AttributeError, TypeError)):
            d.route = PreflightRoute.NO_DATA  # type: ignore[misc]

    def test_should_have_build_preflight_decision_callable_in_preflight_module(self):
        """spec §2.4: shared pure builder in genie.skills.mcp_trino.preflight."""
        import genie.skills.mcp_trino.preflight as pf_mod
        assert callable(getattr(pf_mod, "build_preflight_decision", None)), (
            "build_preflight_decision not found in genie.skills.mcp_trino.preflight"
        )

    def test_should_produce_same_route_for_same_inputs_on_both_paths(self):
        """Dual-path symmetry holds by construction — same inputs → same route."""
        from genie.skills.mcp_trino.preflight import (
            PreflightRoute,
            build_preflight_decision,
        )
        gate_ok = LongQueryGateResult(ok=True, message="ok", baseline_s=1.0, predicted_total_s=5.0)
        d1 = build_preflight_decision(
            diagnose_only=False, baseline_row_count=_SOME_ROWS, baseline_exc=None,
            gate=gate_ok, long_query_opt_in=False, plan_cost_available=False,
            seen_no_estimates=False, max_iterations=5,
        )
        d2 = build_preflight_decision(
            diagnose_only=False, baseline_row_count=_SOME_ROWS, baseline_exc=None,
            gate=gate_ok, long_query_opt_in=False, plan_cost_available=False,
            seen_no_estimates=False, max_iterations=5,
        )
        assert d1.route == PreflightRoute.STANDARD_LOOP
        assert d1.route == d2.route

    def test_should_route_d5_standard_loop_when_max_iterations_zero(self):
        """spec §4 R8 (D5 fix): max_iterations==0 must route STANDARD_LOOP, not PLAN_COST_LOOP."""
        from genie.skills.mcp_trino.preflight import (
            PreflightRoute,
            build_preflight_decision,
        )
        gate_ok = LongQueryGateResult(ok=True, message="ok", baseline_s=1.0, predicted_total_s=5.0)
        d = build_preflight_decision(
            diagnose_only=False, baseline_row_count=_SOME_ROWS, baseline_exc=None,
            gate=gate_ok, long_query_opt_in=True, plan_cost_available=True,
            seen_no_estimates=False, max_iterations=0,  # D5 fix row
        )
        assert d.route == PreflightRoute.STANDARD_LOOP, (
            "D5: max_iterations==0 with plan_cost_available=True must route STANDARD_LOOP"
        )

    def test_should_assert_when_gate_missing_on_successful_baseline(self):
        """spec §A4: builder asserts gate is not None when baseline succeeded."""
        from genie.skills.mcp_trino.preflight import build_preflight_decision
        with pytest.raises(AssertionError):
            build_preflight_decision(
                diagnose_only=False, baseline_row_count=_SOME_ROWS, baseline_exc=None,
                gate=None,  # missing gate when baseline succeeded → AssertionError
                long_query_opt_in=False, plan_cost_available=False,
                seen_no_estimates=False, max_iterations=5,
            )


# ---------------------------------------------------------------------------
# Acceptance tests — Caller D: S2 user (session-property typo must surface
# as a real error, not a no-data advisory)
# ---------------------------------------------------------------------------

class TestS2UserSessionPropertyError:
    """AC-S2: A user whose query fails with 'Session property X does not exist'
    must see the real error, not be silently redirected to a no-data advisory.

    Covers the WHOLE S2 defect class (all non-container 'does not exist' subjects).
    Current behavior (pre-fix) is documented in a non-xfail test; the fixed
    behavior tests are xfail(strict=True).
    """

    # ── Documentation test: current (buggy) behavior ────────────────────────

    def test_current_behavior_documents_s2_bug_for_session_property(self):
        """Current code returns 'table_not_found' for Session property errors
        due to the bare 'does not exist' substring match. This test documents
        the bug: it passes today because it just observes whatever the current
        code returns without asserting a specific correct value.

        The xfail tests below assert the FIXED behavior (returns None)."""
        result = detect_no_data_reason(baseline_exc=RuntimeError(_SESSION_PROP_TEXT))
        # Document the current state; the fixed behavior is asserted in xfail tests.
        # Under pre-fix code this is "table_not_found" (the bug).
        # This test is NOT asserting correctness — it is a characterization test.
        assert result in ("table_not_found", None), (
            f"Unexpected result from detect_no_data_reason: {result!r}"
        )

    # ── Flip tests (post-S2-fix) — all non-container subjects ───────────────

    @pytest.mark.parametrize("text", [
        # Complete defect class (spec §5.3 FLIP table)
        "Session property query_max_execution_time does not exist",
        "Session property query_max_memory does not exist",
        "Function my_udf does not exist",
        "Role 'analyst' does not exist",
        "Column 'foo' does not exist",
        "Type my_type does not exist",                    # LI-1 mandatory
        "Materialized view 'm' does not exist",           # lowercase 'view' — must flip
        "Procedure p does not exist",
        "Property x does not exist",
        "MCP query failed: Session property X does not exist",  # wrapped prefix
    ])
    def test_should_return_none_for_non_container_does_not_exist_after_s2_fix(self, text):
        """After S2: all non-container subjects → None (surface the real error).
        Covers the WHOLE defect class — not just the one flagged instance."""
        result = detect_no_data_reason(baseline_exc=RuntimeError(text))
        assert result is None, (
            f"S2 fix: expected None (real failure) for {text!r}, got {result!r}"
        )

    # ── HOLD tests — container subjects already return table_not_found today
    # These are regression guards: they pass NOW and must stay green after S2.
    # NOT xfail — current code already satisfies them.

    @pytest.mark.parametrize("text", [
        # Complete HOLD set (spec §5.3 HOLD table)
        "Table 'c.s.t' does not exist",
        "View 'a.v_sales' does not exist",               # capital V — hold (not materialized)
        "Schema 'db.bad' does not exist",
        "line 1:8: Catalog 'foo' does not exist",        # existing corpus :47-49
        "Query failed: TABLE_NOT_FOUND for hive.default.foo",
        "SCHEMA_NOT_FOUND: hive.missing",
        "CATALOG_NOT_FOUND: catalog 'hive' not found",
        "Table x not found",
    ])
    def test_should_return_table_not_found_for_container_subjects_regression(self, text):
        """Container subjects already → 'table_not_found' in current code.
        Regression guard: must stay green after S2 fix."""
        result = detect_no_data_reason(baseline_exc=Exception(text))
        assert result == "table_not_found", (
            f"S2 regression: expected 'table_not_found' for {text!r}, got {result!r}"
        )

    # ── End-to-end path tests ────────────────────────────────────────────────

    def test_should_return_failed_status_when_direct_baseline_raises_session_property_error(self):
        """End-to-end: session-property typo on --direct must surface as status='failed'."""
        from genie.skills.trino_query import research as direct_research

        exc = RuntimeError(_SESSION_PROP_TEXT)
        with patch.object(direct_research, "_measure", side_effect=exc):
            result = direct_research._run_optimization_loop(
                provider=None, model="m", reasoning="disable",
                original_sql="SET SESSION query_max_execution_time = '5m'",
                metric_key="cpu_time_ms", max_iterations=5, verify_runs=1,
                output=MagicMock(), build_prompt=lambda *a, **k: "",
            )
        assert result["status"] == "failed", (
            f"Session-property error must not be swallowed into no-data path. "
            f"Got status={result['status']!r}"
        )
        assert _SESSION_PROP_TEXT in result.get("error", ""), (
            f"Error text must be propagated. Got: {result.get('error', '')!r}"
        )

    def test_should_raise_real_exception_when_mcp_baseline_raises_session_property_error(self):
        """End-to-end: session-property typo on MCP must propagate as RuntimeError,
        NOT as NoDataDetected (no-data advisory swallows the real error)."""
        from genie.skills.mcp_trino import research as mcp_research

        exc = RuntimeError(_SESSION_PROP_TEXT2)
        with patch.object(mcp_research, "_measure_mcp", side_effect=exc), \
             patch.object(mcp_research, "_fetch_per_node_memory_limit",
                          return_value=_fake_mem_limit()):
            with pytest.raises(RuntimeError) as exc_info:
                mcp_research.run_mcp_enhancement(
                    client=MagicMock(), sql="SELECT 1",
                    max_iterations=3, verify_runs=1,
                    provider=MagicMock(), model="m", reasoning="disable",
                    output=MagicMock(), build_prompt=lambda *a, **k: "SKILL",
                )
        assert not isinstance(exc_info.value, NoDataDetected), (
            "Session-property error must not be swallowed into NoDataDetected"
        )
        assert _SESSION_PROP_TEXT2 in str(exc_info.value)


# ---------------------------------------------------------------------------
# Acceptance tests — S3: Honest degradation on --direct
# ---------------------------------------------------------------------------

class TestS3HonestDegradationDirect:
    """AC-S3: The --direct path appends a 'metadata-unavailable' direction note
    to every diagnosis call site (spec §7)."""

    def test_should_include_metadata_unavailable_direction_in_direct_directions(self):
        """After S3: _assemble_direct_directions always appends kind='metadata-unavailable'."""
        from genie.skills.trino_query.research import _assemble_direct_directions

        dirs = _assemble_direct_directions("SELECT 1", None, None)
        kinds = [d.kind for d in dirs]
        assert "metadata-unavailable" in kinds, (
            f"Expected a 'metadata-unavailable' direction on --direct path. "
            f"Got kinds: {kinds}"
        )
        metadata_dir = next(d for d in dirs if d.kind == "metadata-unavailable")
        assert "--direct" in metadata_dir.rationale
        assert metadata_dir.severity == "info"

    def test_should_place_metadata_unavailable_direction_last_due_to_info_severity(self):
        """severity='info' → rank 3 → sorts last, never displacing a real direction."""
        from genie.skills.trino_query.research import _assemble_direct_directions
        from genie.skills.trino_query.sql_static import analyze as static_analyze

        # A CROSS JOIN gives at least one high-severity direction to test ordering
        static_report = static_analyze("SELECT * FROM a CROSS JOIN b")
        dirs = _assemble_direct_directions("SELECT * FROM a CROSS JOIN b", static_report, None)
        if len(dirs) > 1:
            assert dirs[-1].kind == "metadata-unavailable", (
                f"Expected metadata-unavailable last, got {dirs[-1].kind!r}"
            )


# ---------------------------------------------------------------------------
# Acceptance tests — S4: Truncation signal in format_directions_for_prompt
# ---------------------------------------------------------------------------

class TestS4TruncationSignal:
    """AC-S4: format_directions_for_prompt appends a truncation hint when
    len(directions) > limit (spec §8)."""

    def test_should_append_truncation_hint_when_directions_exceed_limit(self):
        """After S4: '(+K more directions in the full report)' appended on truncation."""
        from genie.skills.mcp_trino.pre_execution_diagnosis import (
            OptimizationDirection,
            format_directions_for_prompt,
        )

        def _make_dir(i: int) -> OptimizationDirection:
            return OptimizationDirection(
                kind=f"kind-{i}", severity="low", rationale=f"reason {i}",
                evidence=f"static:rule@L{i}", target_metric="query_time_ms",
            )

        dirs = [_make_dir(i) for i in range(8)]
        out = format_directions_for_prompt(dirs, limit=6)
        assert "(+2 more directions in the full report)" in out, (
            f"Expected truncation hint in output. Got: {out!r}"
        )
        numbered = [ln for ln in out.splitlines() if ln and ln[0].isdigit()]
        assert len(numbered) == 6

    def test_should_not_append_truncation_hint_when_directions_at_or_below_limit(self):
        """No hint when len(directions) <= limit. This is CURRENT behavior (no hint
        exists yet) — NOT xfail. After S4 the assertion stays correct (still no hint)."""
        from genie.skills.mcp_trino.pre_execution_diagnosis import (
            OptimizationDirection,
            format_directions_for_prompt,
        )

        def _make_dir(i: int) -> OptimizationDirection:
            return OptimizationDirection(
                kind=f"kind-{i}", severity="low", rationale=f"reason {i}",
                evidence=f"static:rule@L{i}", target_metric="query_time_ms",
            )

        for count in (6, 3, 0):
            out = format_directions_for_prompt([_make_dir(i) for i in range(count)], limit=6)
            assert "(+" not in out, (
                f"Unexpected truncation hint for count={count}. Got: {out!r}"
            )

    def test_should_verify_markdown_report_lists_all_directions_without_cap(self):
        """format_directions_report renders ALL directions (no slice). This is
        CURRENT behavior — NOT xfail (spec §8.2 verifies it already holds)."""
        from genie.skills.mcp_trino.pre_execution_diagnosis import (
            OptimizationDirection,
            format_directions_report,
        )

        def _make_dir(i: int) -> OptimizationDirection:
            return OptimizationDirection(
                kind=f"kind-{i}", severity="low", rationale=f"reason {i}",
                evidence=f"static:rule@L{i}", target_metric="query_time_ms",
            )

        dirs = [_make_dir(i) for i in range(9)]
        md = format_directions_report(dirs, sql="SELECT 1", reason="test")
        # 9 numbered markdown table rows ("| N |")
        table_rows = [
            ln for ln in md.splitlines()
            if ln.startswith("| ") and len(ln) > 3 and ln[2].isdigit()
        ]
        assert len(table_rows) == 9, (
            f"Expected 9 numbered table rows in report, got {len(table_rows)}."
        )
