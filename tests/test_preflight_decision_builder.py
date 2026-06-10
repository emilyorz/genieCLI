"""Unit tests for build_preflight_decision — one test per decision-table row (R1–R8)
plus the S2 non-container defect class.

These tests CHARACTERIZE the current routing behavior and serve as the
regression suite for Step 4/5 (path adaptation). They are committed before
the path adaptation so they can be verified green against the post-S1 builder.
"""
from __future__ import annotations

import pytest

from genie.skills.mcp_trino.preflight import (
    LongQueryGateResult,
    PreflightDecision,
    PreflightRoute,
    build_preflight_decision,
)

GATE_OK   = LongQueryGateResult(ok=True,  message="ok",       baseline_s=1.0,   predicted_total_s=5.0)
GATE_FAIL = LongQueryGateResult(ok=False, message="too slow", baseline_s=120.0, predicted_total_s=720.0)


def _build(**kw):
    defaults = dict(
        diagnose_only=False, baseline_row_count=None, baseline_exc=None,
        gate=None, long_query_opt_in=False, plan_cost_available=False,
        seen_no_estimates=False, max_iterations=5,
    )
    defaults.update(kw)
    return build_preflight_decision(**defaults)


# ── R1: diagnose_only → DIAGNOSE_ONLY ──────────────────────────────────────────

def test_diagnose_only_routes_diagnose_only():
    assert _build(diagnose_only=True).route == PreflightRoute.DIAGNOSE_ONLY


# ── R2: table/schema/catalog exc → NO_DATA ─────────────────────────────────────

def test_table_exc_routes_no_data():
    d = _build(baseline_exc=RuntimeError("Table 'foo' does not exist"))
    assert d.route == PreflightRoute.NO_DATA
    assert d.no_data_reason == "table_not_found"


def test_catalog_exc_routes_no_data_regression():
    """Mirrors existing test at test_run_loop_mode_dispatch.py:47-49."""
    d = _build(baseline_exc=Exception("line 1:8: Catalog 'foo' does not exist"))
    assert d.route == PreflightRoute.NO_DATA
    assert d.no_data_reason == "table_not_found"


# ── R2: zero rows → NO_DATA (empty_result) ─────────────────────────────────────

def test_zero_rows_routes_no_data_empty_result():
    d = _build(baseline_row_count=0, gate=GATE_OK)
    assert d.route == PreflightRoute.NO_DATA
    assert d.no_data_reason == "empty_result"


# ── R3 + S2 defect class: non-container "does not exist" → REAL_FAILURE ────────
# Defect class: ANY "<subject> does not exist" where subject is NOT a data
# container (Table/View/Schema/Catalog). All siblings enumerated and pinned.

@pytest.mark.parametrize("text", [
    "Session property query_max_execution_time does not exist",
    "Session property query_max_memory does not exist",
    "Function my_udf does not exist",
    "Role 'analyst' does not exist",
    "Column 'foo' does not exist",
    "Type my_type does not exist",                       # LI-1 (mandatory from spec)
    "Materialized view 'm' does not exist",              # case-sensitive guard
    "Procedure p does not exist",
    "Property x does not exist",
    "MCP query failed: Session property X does not exist",
    "boom: unexpected failure",
])
def test_non_container_does_not_exist_routes_real_failure(text):
    d = _build(baseline_exc=RuntimeError(text))
    assert d.route == PreflightRoute.REAL_FAILURE
    assert d.baseline_exc is not None


def test_connection_error_routes_real_failure():
    d = _build(baseline_exc=ConnectionError("connection refused at trino:8080"))
    assert d.route == PreflightRoute.REAL_FAILURE


# ── R4: gate.ok==False → LONG_QUERY_ABORT ──────────────────────────────────────

def test_gate_fail_routes_long_query_abort():
    d = _build(baseline_row_count=10, gate=GATE_FAIL)
    assert d.route == PreflightRoute.LONG_QUERY_ABORT
    assert d.gate_result is GATE_FAIL


# ── R5: opt-in + estimates + max_iter>0 → PLAN_COST_LOOP ───────────────────────

def test_opt_in_with_estimates_routes_plan_cost_loop():
    d = _build(baseline_row_count=10, gate=GATE_OK, long_query_opt_in=True,
               plan_cost_available=True)
    assert d.route == PreflightRoute.PLAN_COST_LOOP
    assert d.plan_cost_available is True


# ── R6: opt-in + no estimates (seen_no_estimates) → STANDARD_LOOP ──────────────

def test_opt_in_no_estimates_routes_standard_loop():
    d = _build(baseline_row_count=10, gate=GATE_OK, long_query_opt_in=True,
               plan_cost_available=False, seen_no_estimates=True)
    assert d.route == PreflightRoute.STANDARD_LOOP
    assert d.seen_no_estimates is True


# ── R7: no opt-in → STANDARD_LOOP even if estimates available ──────────────────

def test_no_opt_in_routes_standard_loop_even_with_estimates():
    d = _build(baseline_row_count=10, gate=GATE_OK, long_query_opt_in=False,
               plan_cost_available=True)
    assert d.route == PreflightRoute.STANDARD_LOOP


# ── R8: D5 fix — max_iterations==0 must NOT enter PLAN_COST_LOOP ───────────────

def test_plan_cost_loop_requires_max_iterations_gt_zero():
    """D5: direct path previously lacked max_iterations>0 guard; builder fixes it."""
    d = _build(baseline_row_count=10, gate=GATE_OK, long_query_opt_in=True,
               plan_cost_available=True, max_iterations=0)
    assert d.route == PreflightRoute.STANDARD_LOOP


# ── Builder invariant ──────────────────────────────────────────────────────────

def test_assert_when_gate_missing_on_baseline_success():
    """Passing gate=None when baseline succeeded is a caller bug; builder asserts."""
    with pytest.raises(AssertionError, match="gate required"):
        _build(baseline_row_count=10, gate=None)


# ── PreflightDecision is frozen (immutable) ────────────────────────────────────

def test_preflight_decision_is_frozen():
    d = _build(diagnose_only=True)
    with pytest.raises((AttributeError, TypeError)):
        d.route = PreflightRoute.STANDARD_LOOP
