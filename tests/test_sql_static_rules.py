"""Tests for genie/skills/trino_query/sql_static rule pack (R1-R8).

Each rule has at least one positive (rule fires) and one negative (rule silent) case.
"""
from __future__ import annotations

import pytest

from genie.skills.trino_query.sql_static import analyze


def _rule_ids(sql: str) -> list[str]:
    return [f.rule_id for f in analyze(sql).findings]


# ── R1 cartesian-join ─────────────────────────────────────────────────────────

def test_r1_explicit_cross_join_fires():
    assert "cartesian-join" in _rule_ids("SELECT a.x FROM a CROSS JOIN b")


def test_r1_comma_join_fires():
    assert "cartesian-join" in _rule_ids("SELECT a.x FROM a, b WHERE a.id = b.id")


def test_r1_join_without_on_fires():
    assert "cartesian-join" in _rule_ids("SELECT a.x FROM a JOIN b")


def test_r1_proper_inner_join_silent():
    assert "cartesian-join" not in _rule_ids(
        "SELECT a.x FROM a JOIN b ON a.id = b.id"
    )


# ── R2 select-star ────────────────────────────────────────────────────────────

def test_r2_select_star_fires():
    assert "select-star" in _rule_ids("SELECT * FROM t")


def test_r2_count_star_silent():
    assert "select-star" not in _rule_ids("SELECT COUNT(*) FROM t")


def test_r2_explicit_columns_silent():
    assert "select-star" not in _rule_ids("SELECT a, b, c FROM t")


# ── R3 redundant-distinct-after-group-by ──────────────────────────────────────

def test_r3_distinct_with_matching_group_by_fires():
    assert "redundant-distinct-after-group-by" in _rule_ids(
        "SELECT DISTINCT a, b FROM t GROUP BY a, b"
    )


def test_r3_distinct_without_group_by_silent():
    assert "redundant-distinct-after-group-by" not in _rule_ids(
        "SELECT DISTINCT a FROM t"
    )


def test_r3_group_by_without_distinct_silent():
    assert "redundant-distinct-after-group-by" not in _rule_ids(
        "SELECT a, COUNT(*) FROM t GROUP BY a"
    )


# ── R4 unnecessary-order-by-in-subquery ───────────────────────────────────────

def test_r4_order_in_subquery_fires():
    assert "unnecessary-order-by-in-subquery" in _rule_ids(
        "SELECT * FROM (SELECT x FROM t ORDER BY x) sub"
    )


def test_r4_order_in_cte_fires():
    assert "unnecessary-order-by-in-subquery" in _rule_ids(
        "WITH c AS (SELECT x FROM t ORDER BY x) SELECT x FROM c"
    )


def test_r4_order_with_limit_silent():
    """LIMIT inside subquery makes ORDER BY meaningful (top-N)."""
    assert "unnecessary-order-by-in-subquery" not in _rule_ids(
        "SELECT * FROM (SELECT x FROM t ORDER BY x LIMIT 10) sub"
    )


def test_r4_top_level_order_silent():
    assert "unnecessary-order-by-in-subquery" not in _rule_ids(
        "SELECT x FROM t ORDER BY x"
    )


# ── R5 subquery-in-select-pushable-to-join ────────────────────────────────────

def test_r5_scalar_subquery_in_projection_fires():
    assert "subquery-in-select-pushable-to-join" in _rule_ids(
        "SELECT x, (SELECT MAX(y) FROM b) AS m FROM a"
    )


def test_r5_subquery_in_from_silent():
    """Subquery in FROM is a derived table — not the pattern this rule targets."""
    assert "subquery-in-select-pushable-to-join" not in _rule_ids(
        "SELECT x FROM (SELECT x FROM t) sub"
    )


# ── R6 predicate-not-pushed-to-cte ────────────────────────────────────────────

def test_r6_outer_predicate_pushable_into_cte_fires():
    assert "predicate-not-pushed-to-cte" in _rule_ids(
        "WITH c AS (SELECT x FROM t) SELECT x FROM c WHERE c.x = 1"
    )


def test_r6_predicate_already_in_cte_silent():
    assert "predicate-not-pushed-to-cte" not in _rule_ids(
        "WITH c AS (SELECT x FROM t WHERE x = 1) SELECT x FROM c"
    )


def test_r6_no_cte_or_subquery_silent():
    assert "predicate-not-pushed-to-cte" not in _rule_ids(
        "SELECT x FROM t WHERE x = 1"
    )


# ── R7 null-unsafe-equals ─────────────────────────────────────────────────────

def test_r7_eq_null_fires():
    assert "null-unsafe-equals" in _rule_ids(
        "SELECT x FROM t WHERE col = NULL"
    )


def test_r7_neq_null_fires():
    assert "null-unsafe-equals" in _rule_ids(
        "SELECT x FROM t WHERE col <> NULL"
    )


def test_r7_is_null_silent():
    assert "null-unsafe-equals" not in _rule_ids(
        "SELECT x FROM t WHERE col IS NULL"
    )


# ── R8 redundant-cast-chain ───────────────────────────────────────────────────

def test_r8_nested_cast_same_type_fires():
    assert "redundant-cast-chain" in _rule_ids(
        "SELECT CAST(CAST(x AS VARCHAR) AS VARCHAR) FROM t"
    )


def test_r8_nested_cast_different_type_fires():
    assert "redundant-cast-chain" in _rule_ids(
        "SELECT CAST(CAST(x AS BIGINT) AS VARCHAR) FROM t"
    )


def test_r8_single_cast_silent():
    assert "redundant-cast-chain" not in _rule_ids(
        "SELECT CAST(x AS VARCHAR) FROM t"
    )
