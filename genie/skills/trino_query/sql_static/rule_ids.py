"""Canonical ``rule_id`` constants for the sqlglot static-analysis engine.

Single source of truth shared by the rule producers (``rules/r1..r8``), the
pre-execution-diagnosis kind map, and the rule-gate action map. Before v32 the
producers emitted ids like ``redundant-distinct-after-group-by`` while the two
consumer maps keyed on stale guesses (``distinct-after-group-by`` …); every
mismatched rule silently fell through to a generic ``advise`` and the gate's
``REWRITE`` class never fired in production. Importing these constants in both
producers and consumers makes such drift a ``NameError`` instead of a silent
mis-classification.
"""
from __future__ import annotations

RULE_CARTESIAN_JOIN = "cartesian-join"
RULE_SELECT_STAR = "select-star"
RULE_REDUNDANT_DISTINCT_AFTER_GROUP_BY = "redundant-distinct-after-group-by"
RULE_UNNECESSARY_ORDER_BY_IN_SUBQUERY = "unnecessary-order-by-in-subquery"
RULE_SUBQUERY_IN_SELECT_PUSHABLE_TO_JOIN = "subquery-in-select-pushable-to-join"
RULE_PREDICATE_NOT_PUSHED_TO_CTE = "predicate-not-pushed-to-cte"
RULE_NULL_UNSAFE_EQUALS = "null-unsafe-equals"
RULE_REDUNDANT_CAST_CHAIN = "redundant-cast-chain"
RULE_JOIN_FIRST_FILTER_LATE = "join-first-filter-late"
RULE_JOIN_KEY_COMPUTED = "join-key-computed"

ALL_RULE_IDS = frozenset(
    {
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
    }
)

__all__ = [
    "RULE_CARTESIAN_JOIN",
    "RULE_SELECT_STAR",
    "RULE_REDUNDANT_DISTINCT_AFTER_GROUP_BY",
    "RULE_UNNECESSARY_ORDER_BY_IN_SUBQUERY",
    "RULE_SUBQUERY_IN_SELECT_PUSHABLE_TO_JOIN",
    "RULE_PREDICATE_NOT_PUSHED_TO_CTE",
    "RULE_NULL_UNSAFE_EQUALS",
    "RULE_REDUNDANT_CAST_CHAIN",
    "RULE_JOIN_FIRST_FILTER_LATE",
    "RULE_JOIN_KEY_COMPUTED",
    "ALL_RULE_IDS",
]
