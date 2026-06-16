"""test_cost_weights.py — Contract + ratio invariant tests for cost_weights.py (v52).

§9 property tests assert RATIO invariants, never absolute values.
RULE_WEIGHT keys must exactly match ALL_RULE_IDS (contract test mirrors test_rule_id_contract.py).
"""
from __future__ import annotations

import pytest

from genie.skills.mcp_trino.cost_weights import (
    AGGREGATE_REDUCER,
    CROSS_JOIN_BLOWUP,
    DISTINCT_REDUCER,
    EQUI_JOIN_FACTOR,
    LIMIT_REDUCER,
    NON_EQUI_FACTOR,
    RULE_WEIGHT,
    node_penalty,
    severity_multiplier,
)
from genie.skills.trino_query.sql_static.rule_ids import ALL_RULE_IDS


# ---------------------------------------------------------------------------
# Contract tests
# ---------------------------------------------------------------------------

def test_should_cover_exactly_all_rule_ids_when_checking_rule_weight_keys():
    """RULE_WEIGHT must be keyed on exactly ALL_RULE_IDS — no gaps, no extras."""
    assert set(RULE_WEIGHT.keys()) == set(ALL_RULE_IDS), (
        f"RULE_WEIGHT keys drifted from ALL_RULE_IDS.\n"
        f"  Missing from RULE_WEIGHT: {set(ALL_RULE_IDS) - set(RULE_WEIGHT.keys())}\n"
        f"  Extra in RULE_WEIGHT:     {set(RULE_WEIGHT.keys()) - set(ALL_RULE_IDS)}"
    )


def test_should_have_positive_weights_for_all_rule_ids():
    """Every rule must carry a positive non-zero weight."""
    for rule_id, weight in RULE_WEIGHT.items():
        assert weight > 0, f"RULE_WEIGHT[{rule_id!r}] = {weight} is not positive"


# ---------------------------------------------------------------------------
# §5 Ratio invariant property tests
# ---------------------------------------------------------------------------

def test_should_satisfy_cross_join_ge_3x_non_equi_ratio():
    """§5: cross_join_blowup ≥ 3 × non_equi_factor (hard ratio invariant)."""
    assert CROSS_JOIN_BLOWUP >= 3 * NON_EQUI_FACTOR, (
        f"Ratio invariant violated: CROSS_JOIN_BLOWUP({CROSS_JOIN_BLOWUP}) "
        f"< 3 × NON_EQUI_FACTOR({NON_EQUI_FACTOR})"
    )


def test_should_satisfy_non_equi_ge_3x_equi_ratio():
    """§5: non_equi_factor ≥ 3 × equi_join_factor (hard ratio invariant)."""
    assert NON_EQUI_FACTOR >= 3 * EQUI_JOIN_FACTOR, (
        f"Ratio invariant violated: NON_EQUI_FACTOR({NON_EQUI_FACTOR}) "
        f"< 3 × EQUI_JOIN_FACTOR({EQUI_JOIN_FACTOR})"
    )


def test_should_have_aggregate_reducer_strictly_less_than_one():
    """§5: reducers must strictly lower magnitude (multiplier < 1.0)."""
    assert AGGREGATE_REDUCER < 1.0, (
        f"AGGREGATE_REDUCER({AGGREGATE_REDUCER}) must be < 1.0"
    )


def test_should_have_distinct_reducer_strictly_less_than_one():
    """§5: DISTINCT reducer must strictly lower magnitude."""
    assert DISTINCT_REDUCER < 1.0, (
        f"DISTINCT_REDUCER({DISTINCT_REDUCER}) must be < 1.0"
    )


def test_should_have_limit_reducer_strictly_less_than_one():
    """§5: LIMIT reducer must strictly lower magnitude."""
    assert LIMIT_REDUCER < 1.0, (
        f"LIMIT_REDUCER({LIMIT_REDUCER}) must be < 1.0"
    )


# ---------------------------------------------------------------------------
# severity_multiplier tests
# ---------------------------------------------------------------------------

def test_should_return_3_for_high_severity():
    assert severity_multiplier("high") == 3.0


def test_should_return_2_for_medium_severity():
    assert severity_multiplier("medium") == 2.0


def test_should_return_1_for_low_severity():
    assert severity_multiplier("low") == 1.0


def test_should_return_1_for_unknown_severity():
    assert severity_multiplier("unknown_level") == 1.0


def test_should_be_case_insensitive_for_severity():
    assert severity_multiplier("HIGH") == 3.0
    assert severity_multiplier("Medium") == 2.0


# ---------------------------------------------------------------------------
# node_penalty tests
# ---------------------------------------------------------------------------

def test_should_return_zero_for_unknown_rule_id():
    penalty = node_penalty("non-existent-rule", "high")
    assert penalty == 0.0


def test_should_scale_penalty_by_severity_multiplier():
    """node_penalty(rule_id, sev) = RULE_WEIGHT[rule_id] × severity_multiplier(sev)."""
    from genie.skills.trino_query.sql_static.rule_ids import RULE_CARTESIAN_JOIN
    base = RULE_WEIGHT[RULE_CARTESIAN_JOIN]
    assert node_penalty(RULE_CARTESIAN_JOIN, "high") == base * 3.0
    assert node_penalty(RULE_CARTESIAN_JOIN, "medium") == base * 2.0
    assert node_penalty(RULE_CARTESIAN_JOIN, "low") == base * 1.0


def test_should_produce_positive_penalties_for_all_rule_ids():
    """Every rule_id with a known severity must yield a positive penalty."""
    for rule_id in ALL_RULE_IDS:
        penalty = node_penalty(rule_id, "medium")
        assert penalty > 0, f"node_penalty({rule_id!r}, 'medium') returned {penalty}"


# ---------------------------------------------------------------------------
# Ordering sanity tests (ratios, not absolute values)
# ---------------------------------------------------------------------------

def test_should_rank_cross_join_blowup_strictly_above_non_equi():
    """Cross join must be structurally heavier than non-equi by ratio ≥ 3×."""
    assert CROSS_JOIN_BLOWUP / NON_EQUI_FACTOR >= 3.0


def test_should_rank_non_equi_strictly_above_equi():
    """Non-equi join must be structurally heavier than equi by ratio ≥ 3×."""
    assert NON_EQUI_FACTOR / EQUI_JOIN_FACTOR >= 3.0
