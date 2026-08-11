"""Unit tests for D1 oracle matcher."""
from __future__ import annotations

from genie.skills.mcp_trino.d1_eval.oracle_match import Finding, match_findings
from genie.skills.mcp_trino.d1_eval.taxonomy import normalize_object


def test_normalize_strips_schema_and_case():
    assert normalize_object("Hive.Orders") == "orders"
    assert normalize_object('"DIM"') == "dim"


def test_exact_match():
    o = [Finding("NON_SARGABLE", "join")]
    s = [Finding("NON_SARGABLE", "join", note="P1")]
    m = match_findings(o, s)
    assert m.tp == 1 and m.fn == 0 and m.fp == 0
    assert m.recall == 1.0 and m.precision == 1.0


def test_category_mismatch_is_miss_and_spurious():
    o = [Finding("NON_SARGABLE", "join")]
    s = [Finding("CORRELATED_SUBQUERY", "join")]
    m = match_findings(o, s)
    assert m.tp == 0 and m.fn == 1 and m.fp == 1


def test_object_mismatch():
    o = [Finding("NON_SARGABLE", "orders")]
    s = [Finding("NON_SARGABLE", "customers")]
    m = match_findings(o, s)
    assert m.fn == 1 and m.fp == 1


def test_column_overlap_required_when_oracle_lists_cols():
    o = [Finding("NON_SARGABLE", "join", columns=("cust_id",))]
    s_ok = [Finding("NON_SARGABLE", "join", columns=("cust_id", "x"))]
    s_bad = [Finding("NON_SARGABLE", "join", columns=("other",))]
    assert match_findings(o, s_ok).tp == 1
    assert match_findings(o, s_bad).tp == 0


def test_oracle_without_columns_allows_object_match():
    o = [Finding("NON_SARGABLE", "join")]
    s = [Finding("NON_SARGABLE", "join", columns=("a",))]
    assert match_findings(o, s).tp == 1


def test_greedy_one_to_one():
    o = [
        Finding("NON_SARGABLE", "join"),
        Finding("NON_SARGABLE", "join"),
    ]
    s = [Finding("NON_SARGABLE", "join")]
    m = match_findings(o, s)
    assert m.tp == 1 and m.fn == 1 and m.fp == 0


def test_spurious_only():
    o = []
    s = [Finding("SELECT_STAR_WIDE", "select")]
    m = match_findings(o, s)
    assert m.precision == 0.0 and m.fp == 1


def test_empty_both_zero_metrics():
    m = match_findings([], [])
    assert m.recall == 0.0 and m.precision == 0.0


def test_from_dict_roundtrip():
    f = Finding.from_dict(
        {"category": "CORRELATED_SUBQUERY", "object": "Hive.X", "columns": ["A"], "note": "n"}
    )
    assert f.object == "x"
    assert f.columns == ("a",)


def test_alias_normalized_object():
    assert normalize_object("ast:join_on_coalesce") in {
        "join_on_coalesce",
        "ast:join_on_coalesce".split(":")[-1],
    }


def test_precision_floor_scenario():
    # 2 tp, 1 fp, 0 fn → recall 1, precision 0.666
    o = [Finding("A", "x"), Finding("B", "y")]
    s = [Finding("A", "x"), Finding("B", "y"), Finding("C", "z")]
    # categories must match enum-like strings used as plain
    o = [Finding("NON_SARGABLE", "x"), Finding("CORRELATED_SUBQUERY", "y")]
    s = [
        Finding("NON_SARGABLE", "x"),
        Finding("CORRELATED_SUBQUERY", "y"),
        Finding("SELECT_STAR_WIDE", "z"),
    ]
    m = match_findings(o, s)
    assert m.tp == 2 and m.fp == 1
    assert abs(m.precision - 2 / 3) < 1e-9
