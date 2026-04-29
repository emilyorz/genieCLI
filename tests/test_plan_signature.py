"""Tests for genie/skills/trino_query/plan_signature.py — T3 of v28."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from genie.skills.trino_query.plan_signature import (
    plan_signature,
    structural_equivalent,
)


FIXTURE_DIR = Path(__file__).parent / "fixtures" / "explain_plans"


def _load(name: str):
    return json.loads((FIXTURE_DIR / name).read_text())


# ── Basic signature stability ─────────────────────────────────────────────────

def test_signature_is_deterministic():
    plan = _load("simple_select.json")
    assert plan_signature(plan) == plan_signature(plan)


def test_signature_handles_string_input():
    raw = (FIXTURE_DIR / "simple_select.json").read_text()
    assert plan_signature(raw) == plan_signature(_load("simple_select.json"))


def test_signature_returns_none_for_unparseable_string():
    assert plan_signature("not valid json @@@") is None


def test_signature_returns_none_for_none_input():
    assert plan_signature(None) is None


def test_signature_returns_none_for_unsupported_type():
    assert plan_signature(42) is None


# ── Volatile fields are dropped ───────────────────────────────────────────────

def test_estimates_do_not_affect_signature():
    """Two plans differing only in row-count estimates must hash identically."""
    a = plan_signature(_load("simple_select.json"))
    b = plan_signature(_load("simple_select_equiv.json"))
    assert a == b
    assert structural_equivalent(
        _load("simple_select.json"),
        _load("simple_select_equiv.json"),
    )


# ── Structural equivalence — true positives ───────────────────────────────────

def test_join_input_order_does_not_matter():
    """JOIN is commutative — swapping inputs must not break equivalence."""
    assert structural_equivalent(
        _load("with_join.json"),
        _load("with_join_swapped.json"),
    )


# ── Structural divergence — true negatives ────────────────────────────────────

def test_inner_vs_left_join_diverges():
    """LEFT JOIN vs INNER JOIN is a semantic change — must not be equivalent."""
    assert not structural_equivalent(
        _load("with_join.json"),
        _load("with_join_left.json"),
    )


def test_count_vs_approx_distinct_diverges():
    """COUNT vs APPROX_DISTINCT change function set — must diverge."""
    assert not structural_equivalent(
        _load("with_aggregate.json"),
        _load("with_aggregate_approx.json"),
    )


def test_join_vs_simple_scan_diverges():
    assert not structural_equivalent(
        _load("with_join.json"),
        _load("simple_select.json"),
    )


def test_aggregate_vs_filter_diverges():
    assert not structural_equivalent(
        _load("with_aggregate.json"),
        _load("with_cte.json"),
    )


# ── structural_equivalent edge cases ──────────────────────────────────────────

def test_structural_equivalent_returns_false_when_either_is_none():
    plan = _load("simple_select.json")
    assert not structural_equivalent(plan, None)
    assert not structural_equivalent(None, plan)
    assert not structural_equivalent(None, None)


def test_signature_handles_list_root():
    """Trino sometimes wraps the plan in a list of fragments."""
    sig = plan_signature([_load("simple_select.json")])
    assert sig is not None
    assert sig[0] == "Root"


def test_signature_table_is_lowercased():
    plan = {
        "name": "TableScan[HIVE.DEFAULT.T]",
        "descriptor": {"table": "HIVE.DEFAULT.T"},
        "children": [],
    }
    sig = plan_signature(plan)
    assert sig is not None
    # Walk into key_attrs tuple: (op, key_attrs, children)
    assert sig[1] == ("table", "hive.default.t")
