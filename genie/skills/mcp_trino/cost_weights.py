"""cost_weights.py — Pure weight table + helpers for v52 offline cost model.

No I/O, no imports from research/optimize (no circular dependency risk).
Exports NODE_BASE_WEIGHT, RULE_WEIGHT (keyed on rule_ids constants),
severity_multiplier, and node_penalty.
"""
from __future__ import annotations

from genie.skills.trino_query.sql_static.rule_ids import (
    RULE_CARTESIAN_JOIN,
    RULE_SELECT_STAR,
    RULE_REDUNDANT_DISTINCT_AFTER_GROUP_BY,
    RULE_UNNECESSARY_ORDER_BY_IN_SUBQUERY,
    RULE_SUBQUERY_IN_SELECT_PUSHABLE_TO_JOIN,
    RULE_PREDICATE_NOT_PUSHED_TO_CTE,
    RULE_NULL_UNSAFE_EQUALS,
    RULE_REDUNDANT_CAST_CHAIN,
    RULE_JOIN_FIRST_FILTER_LATE,
    RULE_JOIN_KEY_COMPUTED,
)

# ---------------------------------------------------------------------------
# §2 op → base weight (node self-cost multiplied by row_magnitude)
# ---------------------------------------------------------------------------

NODE_BASE_WEIGHT: dict[str, float] = {
    "root":                1.0,
    "scan":                1.0,
    "filter":              1.0,
    "project":             1.0,
    "join_inner":          5.0,
    "join_left":           5.0,
    "join_right":          5.0,
    "join_full":           5.0,
    "join_cross":        100.0,   # ratio invariant: cross ≥ 3× non-equi
    "join_non_equi":      30.0,   # ratio invariant: non-equi ≥ 3× equi
    "aggregate":           2.0,
    "sort":                5.0,
    "window":             10.0,
    "distinct":            2.0,
    "limit":               1.0,   # §4 reducer — LIMIT_REDUCER applied to row_magnitude
    "setop":               3.0,
    "correlated_subquery": 50.0,
    "cte":                 1.0,
}

# ---------------------------------------------------------------------------
# §5 Blowup / reducer factors used by compute_costs magnitude propagation.
# These MUST satisfy the §5 ratio invariants (asserted by test_cost_weights.py):
#   CROSS_JOIN_BLOWUP ≥ 3 × NON_EQUI_FACTOR
#   NON_EQUI_FACTOR ≥ 3 × EQUI_JOIN_FACTOR
# ---------------------------------------------------------------------------

CROSS_JOIN_BLOWUP: float = 100.0
NON_EQUI_FACTOR: float = 30.0
EQUI_JOIN_FACTOR: float = 5.0
CORRELATED_SUBQUERY_FACTOR: float = 3.0   # multiplier > 1 (raises parent magnitude)
AGGREGATE_REDUCER: float = 0.5            # strictly < 1 (lowers magnitude)
DISTINCT_REDUCER: float = 0.5
LIMIT_REDUCER: float = 0.1

# ---------------------------------------------------------------------------
# §6 R-label → RULE_WEIGHT (keyed on rule_ids constants)
# ---------------------------------------------------------------------------

RULE_WEIGHT: dict[str, float] = {
    RULE_CARTESIAN_JOIN:                    50.0,
    RULE_SELECT_STAR:                       15.0,
    RULE_REDUNDANT_DISTINCT_AFTER_GROUP_BY:  5.0,
    RULE_UNNECESSARY_ORDER_BY_IN_SUBQUERY:  10.0,
    RULE_SUBQUERY_IN_SELECT_PUSHABLE_TO_JOIN: 20.0,
    RULE_PREDICATE_NOT_PUSHED_TO_CTE:       12.0,
    RULE_NULL_UNSAFE_EQUALS:                 3.0,
    RULE_REDUNDANT_CAST_CHAIN:               3.0,
    RULE_JOIN_FIRST_FILTER_LATE:            12.0,
    RULE_JOIN_KEY_COMPUTED:                 15.0,
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def severity_multiplier(severity: str) -> float:
    """Return numeric multiplier for a severity string.

    high/medium/low → 3/2/1. Any unknown value → 1 (fail-safe).
    """
    return {"high": 3.0, "medium": 2.0, "low": 1.0}.get(severity.lower(), 1.0)


def node_penalty(rule_id: str, severity: str) -> float:
    """Return the additive penalty for a rule finding on a node.

    penalty = RULE_WEIGHT[rule_id] × severity_multiplier(severity).
    Returns 0.0 for unknown rule_ids (fail-safe, no crash).
    """
    base = RULE_WEIGHT.get(rule_id, 0.0)
    return base * severity_multiplier(severity)


__all__ = [
    "NODE_BASE_WEIGHT",
    "CROSS_JOIN_BLOWUP",
    "NON_EQUI_FACTOR",
    "EQUI_JOIN_FACTOR",
    "CORRELATED_SUBQUERY_FACTOR",
    "AGGREGATE_REDUCER",
    "DISTINCT_REDUCER",
    "LIMIT_REDUCER",
    "RULE_WEIGHT",
    "severity_multiplier",
    "node_penalty",
]
