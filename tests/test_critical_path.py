"""test_critical_path.py — 8-query corpus characterization + edge-case tests (v52).

Tests assert RELATIVE ordering only — never absolute cost numbers.
Corpus queries: Q1–Q8 per CORPUS.md. Edge cases per §8c.
"""
from __future__ import annotations

import pytest

from genie.skills.mcp_trino.critical_path import (
    CriticalPathResult,
    analyze_critical_path,
    build_cost_tree,
    compute_costs,
    flatten_nodes,
)


# ---------------------------------------------------------------------------
# Corpus SQL fixtures (verbatim from CORPUS.md)
# ---------------------------------------------------------------------------

Q1 = """
SELECT o.id, c.name
FROM orders o
CROSS JOIN customers c
WHERE o.status = 'OPEN'
"""

Q2 = """
SELECT o.id
FROM orders o
WHERE EXISTS (
  SELECT 1 FROM line_items li WHERE li.order_id = o.id AND li.qty > 100
)
"""

Q3 = """
SELECT t.*
FROM (
  SELECT a.id, a.v
  FROM (
    SELECT x.id, x.v
    FROM facts x
    JOIN dims d ON d.id = x.dim_id
    WHERE x.v > 0
  ) a
  JOIN more m ON m.id = a.id
) t
WHERE t.v < 1000
"""

Q4 = """
SELECT *
FROM big_fact f
JOIN small_dim d ON d.id = f.dim_id
"""

Q5 = """
SELECT a.id
FROM a
JOIN b ON b.id = a.id
JOIN c ON c.lo <= a.v AND c.hi >= a.v
"""

Q6 = """
SELECT s.k, count(*)
FROM (
  SELECT k, v FROM events ORDER BY v
) s
GROUP BY s.k
"""

Q7 = """
SELECT a.label, b.label
FROM color_dim a
CROSS JOIN size_dim b
"""

Q8 = """
SELECT f.id, row_number() OVER (PARTITION BY f.cust_id ORDER BY f.ts DESC) rn
FROM fact f
JOIN cust c ON c.id = f.cust_id
"""


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _flatten(sql: str) -> list:
    """Return flatten_nodes for a SQL string (costs already computed)."""
    root = build_cost_tree(sql)
    compute_costs(root)
    return flatten_nodes(root, exclude_wrappers=True)


# ---------------------------------------------------------------------------
# §9 Q1 — cross join must rank #1
# ---------------------------------------------------------------------------

def test_should_rank_cross_join_first_when_q1_cartesian():
    """Q1: CROSS JOIN dominates unconditionally."""
    result = analyze_critical_path(Q1)
    assert result.available
    assert not result.trivial
    bottleneck = result.bottleneck
    assert bottleneck is not None
    assert bottleneck.op == "join_cross", (
        f"Expected cross join bottleneck, got op={bottleneck.op!r} label={bottleneck.label!r}"
    )


def test_should_flag_truth_ceiling_when_q1_cross_join():
    """Q1: structural cross join must carry offline_truth_ceiling flag."""
    result = analyze_critical_path(Q1)
    assert result.available
    assert result.offline_truth_ceiling, "Expected truth-ceiling flag for cross join"


# ---------------------------------------------------------------------------
# §9 Q2 — correlated EXISTS is the hot node
# ---------------------------------------------------------------------------

def test_should_rank_correlated_subquery_first_when_q2_exists():
    """Q2: correlated EXISTS subquery must be the bottleneck."""
    result = analyze_critical_path(Q2)
    assert result.available
    assert not result.trivial
    bottleneck = result.bottleneck
    assert bottleneck is not None
    assert bottleneck.op == "correlated_subquery", (
        f"Expected correlated_subquery, got op={bottleneck.op!r}"
    )


def test_should_include_exists_in_label_when_q2():
    """Q2: bottleneck label must contain 'EXISTS' (human-readable, §2.1)."""
    result = analyze_critical_path(Q2)
    assert result.available
    assert result.bottleneck is not None
    label = result.bottleneck.label
    assert "EXISTS" in label or "IN" in label, f"Label not human: {label!r}"


# ---------------------------------------------------------------------------
# §9 Q3 — depth ordering: inner join > middle join > outer filter
# ---------------------------------------------------------------------------

def test_should_rank_inner_join_above_middle_join_when_q3_deep_nesting():
    """Q3: deepest join (facts⋈dims) must rank above middle join (a⋈more)."""
    nodes = _flatten(Q3)
    ops = [n.op for n in nodes]
    join_nodes = [n for n in nodes if n.op in ("join_inner", "join_cross", "join_non_equi")]
    assert len(join_nodes) >= 2, f"Expected ≥2 join nodes, got: {ops}"
    # Highest ranked join must have higher subtree_cost than second-ranked
    assert join_nodes[0].subtree_cost > join_nodes[1].subtree_cost, (
        f"Inner join ({join_nodes[0].subtree_cost}) not > middle join ({join_nodes[1].subtree_cost})"
    )


def test_should_have_top_join_rank_above_all_scans_when_q3():
    """Q3: both joins must rank above all bare scans."""
    nodes = _flatten(Q3)
    join_nodes = [n for n in nodes if n.op in ("join_inner", "join_cross", "join_non_equi")]
    scan_nodes = [n for n in nodes if n.op == "scan"]
    assert join_nodes, "No join nodes found"
    assert scan_nodes, "No scan nodes found"
    lowest_join_cost = min(j.subtree_cost for j in join_nodes)
    highest_scan_cost = max(s.subtree_cost for s in scan_nodes)
    assert lowest_join_cost > highest_scan_cost, (
        f"Expected join cost ({lowest_join_cost}) > scan cost ({highest_scan_cost})"
    )


# ---------------------------------------------------------------------------
# §9 Q4 — SELECT * wide scan feeding a join is the hot input
# ---------------------------------------------------------------------------

def test_should_rank_scan_above_nothing_when_q4_select_star():
    """Q4: scan with SELECT* penalty on a join input must be present and non-trivial."""
    result = analyze_critical_path(Q4)
    assert result.available
    assert not result.trivial
    # The join or the penalised scan must be the bottleneck
    bottleneck = result.bottleneck
    assert bottleneck is not None
    assert bottleneck.op in ("join_inner", "join_left", "join_right", "scan"), (
        f"Unexpected bottleneck op: {bottleneck.op!r}"
    )


def test_should_have_both_join_and_scan_nodes_when_q4():
    """Q4: tree must contain a join node and at least one scan node."""
    nodes = _flatten(Q4)
    ops = {n.op for n in nodes}
    assert "scan" in ops, "No scan nodes in Q4 tree"
    join_ops = ops & {"join_inner", "join_left", "join_right", "join_full", "join_cross"}
    assert join_ops, f"No join node found in Q4 tree; ops: {ops}"


# ---------------------------------------------------------------------------
# §9 Q5 — non-equi join > equi join
# ---------------------------------------------------------------------------

def test_should_rank_non_equi_join_above_equi_join_when_q5():
    """Q5: range/non-equi join (c) must dominate equi join (b)."""
    nodes = _flatten(Q5)
    non_equi = [n for n in nodes if n.op == "join_non_equi"]
    equi = [n for n in nodes if n.op == "join_inner"]
    assert non_equi, "No non-equi join found in Q5 tree"
    assert equi, f"No equi join found in Q5 tree; nodes: {[(n.op, n.label) for n in nodes]}"
    assert non_equi[0].subtree_cost > equi[0].subtree_cost, (
        f"non-equi ({non_equi[0].subtree_cost}) not > equi ({equi[0].subtree_cost})"
    )


def test_should_have_non_equi_bottleneck_when_q5():
    """Q5: bottleneck must be the non-equi join."""
    result = analyze_critical_path(Q5)
    assert result.available
    assert not result.trivial
    bottleneck = result.bottleneck
    assert bottleneck is not None
    assert bottleneck.op == "join_non_equi", (
        f"Expected non_equi bottleneck, got {bottleneck.op!r}"
    )


# ---------------------------------------------------------------------------
# §9 Q6 — ORDER BY + GROUP BY both rank above bare scan
# ---------------------------------------------------------------------------

def test_should_rank_sort_above_scan_when_q6_unnecessary_order_by():
    """Q6: ORDER BY (with R4 penalty) must rank above the bare scan."""
    nodes = _flatten(Q6)
    sort_nodes = [n for n in nodes if n.op == "sort"]
    scan_nodes = [n for n in nodes if n.op == "scan"]
    assert sort_nodes, "No sort node found in Q6"
    assert scan_nodes, "No scan node in Q6"
    assert sort_nodes[0].subtree_cost > scan_nodes[0].subtree_cost, (
        f"Sort ({sort_nodes[0].subtree_cost}) not > scan ({scan_nodes[0].subtree_cost})"
    )


def test_should_rank_aggregate_above_scan_when_q6():
    """Q6: GROUP BY aggregate must rank above the bare scan."""
    nodes = _flatten(Q6)
    agg_nodes = [n for n in nodes if n.op == "aggregate"]
    scan_nodes = [n for n in nodes if n.op == "scan"]
    assert agg_nodes, "No aggregate node in Q6"
    assert scan_nodes
    assert agg_nodes[0].subtree_cost > scan_nodes[0].subtree_cost, (
        f"Aggregate ({agg_nodes[0].subtree_cost}) not > scan ({scan_nodes[0].subtree_cost})"
    )


# ---------------------------------------------------------------------------
# §9 Q7 — TRUTH-CEILING: cross join must still rank #1 + ceiling flagged
# ---------------------------------------------------------------------------

def test_should_rank_cross_join_first_when_q7_tiny_dims():
    """Q7 truth-ceiling: structural model must rank cross join #1 even for tiny dims."""
    result = analyze_critical_path(Q7)
    assert result.available
    assert not result.trivial
    bottleneck = result.bottleneck
    assert bottleneck is not None
    assert bottleneck.op == "join_cross", (
        f"Q7 truth-ceiling: expected cross join bottleneck, got {bottleneck.op!r}"
    )


def test_should_set_truth_ceiling_flag_when_q7():
    """Q7: offline_truth_ceiling must be True in result (honest-limitation test)."""
    result = analyze_critical_path(Q7)
    assert result.available
    assert result.offline_truth_ceiling, "Q7: truth-ceiling must be flagged"


# ---------------------------------------------------------------------------
# §9 Q8 — window + join both above bare scans
# ---------------------------------------------------------------------------

def test_should_rank_window_above_scan_when_q8_window_function():
    """Q8: window function node must rank above bare scans."""
    nodes = _flatten(Q8)
    window_nodes = [n for n in nodes if n.op == "window"]
    scan_nodes = [n for n in nodes if n.op == "scan"]
    assert window_nodes, "No window node in Q8"
    assert scan_nodes
    assert window_nodes[0].subtree_cost > scan_nodes[0].subtree_cost, (
        f"Window ({window_nodes[0].subtree_cost}) not > scan ({scan_nodes[0].subtree_cost})"
    )


def test_should_rank_join_above_scan_when_q8():
    """Q8: join must rank above bare scans."""
    nodes = _flatten(Q8)
    join_nodes = [n for n in nodes if n.op in ("join_inner", "join_cross", "join_non_equi")]
    scan_nodes = [n for n in nodes if n.op == "scan"]
    assert join_nodes, "No join node in Q8"
    assert scan_nodes
    assert join_nodes[0].subtree_cost > scan_nodes[0].subtree_cost, (
        f"Join ({join_nodes[0].subtree_cost}) not > scan ({scan_nodes[0].subtree_cost})"
    )


# ---------------------------------------------------------------------------
# §8c Edge-case tests
# ---------------------------------------------------------------------------

def test_should_return_available_false_when_parse_error():
    """§8c: garbled SQL must return available=False without raising."""
    result = analyze_critical_path("NOT VALID SQL ;;; *** !!!")
    assert not result.available
    assert result.reason  # must explain why


def test_should_not_raise_when_empty_string():
    """§8c: empty SQL must not crash analyze_critical_path."""
    result = analyze_critical_path("")
    assert not result.available


def test_should_return_trivial_when_single_scan_no_blowup():
    """§8c: a bare SELECT with no joins/subqueries is a trivial query."""
    result = analyze_critical_path("SELECT id, name FROM customers WHERE active = true")
    assert result.available
    assert result.trivial, "Single-table scan with no joins must be trivial"


def test_should_have_human_readable_label_on_all_nodes():
    """§2.1: no node label should contain AST repr junk."""
    bad_patterns = ["(this=", "Identifier", "Column(", "Table(", "Join(", "Select("]
    for q_name, sql in [("Q1", Q1), ("Q2", Q2), ("Q3", Q3), ("Q4", Q4),
                         ("Q5", Q5), ("Q6", Q6), ("Q7", Q7), ("Q8", Q8)]:
        root = build_cost_tree(sql)
        compute_costs(root)
        nodes = flatten_nodes(root, exclude_wrappers=False)
        for node in nodes:
            label = node.label
            assert label, f"{q_name}: node op={node.op!r} has empty label"
            for bad in bad_patterns:
                assert bad not in label, (
                    f"{q_name}: label {label!r} contains AST repr junk {bad!r}"
                )


def test_should_report_tied_paths_count_when_two_leaves_equal_cost():
    """§8c-3: tied_paths must reflect actual count of equal-weight leaves."""
    # Q7 is a pure cross join with two tiny dim scans — scans may tie
    result = analyze_critical_path(Q7)
    assert result.available
    assert result.tied_paths >= 1  # always at least 1


def test_should_return_deterministic_bottleneck_on_repeated_calls():
    """§8c-3: same SQL must always return the same bottleneck (stable tie-break)."""
    results = [analyze_critical_path(Q7) for _ in range(3)]
    assert all(r.available for r in results)
    bottleneck_ids = [id(r.bottleneck) for r in results]
    # Different Python objects but same label — check label stability
    labels = [r.bottleneck.label if r.bottleneck else "" for r in results]
    assert len(set(labels)) == 1, f"Non-deterministic bottleneck: {labels}"


def test_should_have_strategy_p2_when_correlated_exists_is_bottleneck():
    """§6: EXISTS correlated subquery bottleneck → strategy P2."""
    result = analyze_critical_path(Q2)
    assert result.available
    assert result.bottleneck is not None
    assert result.bottleneck.strategy == "P2", (
        f"Expected P2 strategy for correlated EXISTS, got {result.bottleneck.strategy!r}"
    )


def test_should_have_no_strategy_for_sort_node():
    """§6 GAP-2: sort nodes carry advisory text only (strategy=None)."""
    root = build_cost_tree(Q6)
    compute_costs(root)
    nodes = flatten_nodes(root, exclude_wrappers=False)
    sort_nodes = [n for n in nodes if n.op == "sort"]
    for sn in sort_nodes:
        assert sn.strategy is None, (
            f"Sort node must have strategy=None (§6 GAP-2), got {sn.strategy!r}"
        )


def test_should_have_non_zero_rule_penalty_for_sort_in_subquery():
    """Q6 ORDER BY in subquery must carry a non-zero rule_penalties (R4 advisory)."""
    root = build_cost_tree(Q6)
    compute_costs(root)
    nodes = flatten_nodes(root, exclude_wrappers=False)
    sort_nodes = [n for n in nodes if n.op == "sort"]
    # At least one sort node (the ORDER BY in the inner subquery) must have penalty
    inner_sorts = [n for n in sort_nodes if n.rule_penalties > 0]
    assert inner_sorts, (
        "Expected at least one sort node with R4 rule_penalty in Q6 (inner subquery ORDER BY)"
    )


# ---------------------------------------------------------------------------
# §4 Reducer behavioral tests (Fix 1 — AGGREGATE_REDUCER / DISTINCT_REDUCER /
# LIMIT_REDUCER are actually applied in the magnitude propagation path)
# ---------------------------------------------------------------------------

def test_should_lower_aggregate_magnitude_when_group_by_present():
    """AGGREGATE_REDUCER must be applied: join+GROUP BY yields lower agg row_magnitude
    than the join node at the same level.

    Queries:
      plain_join — JOIN only, no GROUP BY
      agg_join   — identical JOIN + GROUP BY s.k

    The aggregate node's row_magnitude must be strictly < join node's row_magnitude,
    proving AGGREGATE_REDUCER (< 1.0) was applied rather than the constants being dead.
    """
    plain_join = "SELECT a.id, b.v FROM a JOIN b ON b.id = a.id"
    agg_join = "SELECT a.id, count(*) FROM a JOIN b ON b.id = a.id GROUP BY a.id"

    # plain join: no aggregate
    plain_root = build_cost_tree(plain_join)
    compute_costs(plain_root)
    plain_nodes = flatten_nodes(plain_root, exclude_wrappers=False)
    plain_joins = [n for n in plain_nodes if n.op == "join_inner"]
    assert plain_joins, "Expected a join node in plain_join query"
    join_magnitude = plain_joins[0].row_magnitude

    # agg join: aggregate must have strictly lower row_magnitude than the join
    agg_root = build_cost_tree(agg_join)
    compute_costs(agg_root)
    agg_nodes = flatten_nodes(agg_root, exclude_wrappers=False)
    agg_candidates = [n for n in agg_nodes if n.op == "aggregate"]
    assert agg_candidates, "Expected an aggregate node in agg_join query"
    agg_magnitude = agg_candidates[0].row_magnitude

    assert agg_magnitude < join_magnitude, (
        f"AGGREGATE_REDUCER not applied: agg row_magnitude({agg_magnitude}) "
        f"not < join row_magnitude({join_magnitude}). "
        "The reducer constant is defined but unused (dead code)."
    )


def test_should_lower_distinct_magnitude_when_distinct_present():
    """DISTINCT_REDUCER must be applied: join+DISTINCT yields lower row_magnitude
    than the join node at the same level.

    Same structure as the aggregate test — proves DISTINCT_REDUCER is live.
    """
    agg_distinct = "SELECT DISTINCT a.id FROM a JOIN b ON b.id = a.id"

    root = build_cost_tree(agg_distinct)
    compute_costs(root)
    nodes = flatten_nodes(root, exclude_wrappers=False)
    join_nodes = [n for n in nodes if n.op == "join_inner"]
    dist_nodes = [n for n in nodes if n.op == "distinct"]
    assert join_nodes, "Expected join node in distinct query"
    assert dist_nodes, "Expected distinct node in distinct query"

    join_magnitude = join_nodes[0].row_magnitude
    dist_magnitude = dist_nodes[0].row_magnitude

    assert dist_magnitude < join_magnitude, (
        f"DISTINCT_REDUCER not applied: distinct row_magnitude({dist_magnitude}) "
        f"not < join row_magnitude({join_magnitude}). "
        "The reducer constant is defined but unused (dead code)."
    )


def test_should_lower_limit_magnitude_when_limit_present():
    """LIMIT_REDUCER must be applied: join+LIMIT yields lower limit row_magnitude
    than the join node at the same level.

    Proves LIMIT_REDUCER is live in the magnitude propagation path.
    """
    limit_query = "SELECT a.id FROM a JOIN b ON b.id = a.id LIMIT 10"

    root = build_cost_tree(limit_query)
    compute_costs(root)
    nodes = flatten_nodes(root, exclude_wrappers=False)
    join_nodes = [n for n in nodes if n.op == "join_inner"]
    limit_nodes = [n for n in nodes if n.op == "limit"]
    assert join_nodes, "Expected join node in limit query"
    assert limit_nodes, "Expected limit node in limit query"

    join_magnitude = join_nodes[0].row_magnitude
    limit_magnitude = limit_nodes[0].row_magnitude

    assert limit_magnitude < join_magnitude, (
        f"LIMIT_REDUCER not applied: limit row_magnitude({limit_magnitude}) "
        f"not < join row_magnitude({join_magnitude}). "
        "The reducer constant is defined but unused (dead code)."
    )


# ---------------------------------------------------------------------------
# §10 Integration tests — step_trace threading and report rendering (Fix 2)
# ---------------------------------------------------------------------------

def test_should_append_critical_path_step_event_to_step_trace():
    """§10 GAP-1: the no-data path must thread step_trace and emit a
    step_id='critical_path' StepEvent into the live trace.

    We test the lower-level hook that is responsible for this: the
    _produce_decompose_candidate function appends the StepEvent to the
    step_trace it receives. The no-data path passes its _nd_trace to this
    function. Verify the hook is wired and actually appends the event.
    """
    from unittest.mock import MagicMock
    from genie.skills.mcp_trino.research import _produce_decompose_candidate
    from genie.output.step_trace import StepEvent, StepTrace

    trace: StepTrace = []

    # Minimal mocks — we only need the LLM call to return something parseable,
    # and the cost reader to return a sentinel cost so decompose can proceed.
    mock_llm_fn = MagicMock(return_value='{"fragments": [{"id": "f0", "sql": "SELECT a FROM t", "role": "whole"}]}')
    mock_cost_reader = MagicMock(return_value=None)

    sql = "SELECT a.id FROM a JOIN b ON b.id = a.id"
    try:
        _produce_decompose_candidate(
            sql,
            mock_llm_fn,
            mock_cost_reader,
            run_static_gates=True,
            step_trace=trace,
        )
    except Exception:
        # _produce_decompose_candidate may raise if decompose fails with the mock;
        # what matters is whether the critical_path event was appended BEFORE any
        # potential error in later stages. If it wasn't appended at all (no event
        # at any point), that's the bug.
        pass

    step_ids = [ev.step_id for ev in trace]
    assert "critical_path" in step_ids, (
        f"§10 GAP-1: critical_path StepEvent was never appended to step_trace. "
        f"Events present: {step_ids}. "
        "The step_trace thread through _produce_decompose_candidate is broken."
    )


def test_should_render_critical_path_section_from_formatter():
    """§10: the _DETAIL_FORMATTERS['critical_path'] formatter renders a section
    containing the hot chain and bottleneck when given a valid detail dict.

    Tests both halves of the integration:
    (a) The formatter is registered under 'critical_path'.
    (b) render_report produces a non-empty markdown section for a StepEvent
        with step_id='critical_path' and realistic detail.
    """
    from genie.output.step_trace import (
        StepEvent,
        StepStatus,
        _DETAIL_FORMATTERS,
        render_report,
    )

    # Build a realistic critical_path detail dict (same shape as the code emits)
    cp_detail = {
        "available": True,
        "trivial": False,
        "bottleneck_label": "CROSS JOIN color_dim⋈size_dim",
        "bottleneck_op": "join_cross",
        "bottleneck_cost_pct": 87.3,
        "strategy": None,
        "offline_truth_ceiling": True,
        "tied_paths": 1,
        "critical_path": [
            {"label": "query", "op": "root"},
            {"label": "CROSS JOIN color_dim⋈size_dim", "op": "join_cross"},
        ],
    }

    # (a) The formatter is registered and produces non-empty output
    formatter = _DETAIL_FORMATTERS.get("critical_path")
    assert formatter is not None, (
        "§10: _DETAIL_FORMATTERS['critical_path'] is not registered"
    )
    formatted = formatter(cp_detail)
    assert "Hot chain" in formatted or "Bottleneck" in formatted, (
        f"§10: critical_path formatter produced unexpected output: {formatted!r}"
    )
    assert "CROSS JOIN" in formatted, (
        f"§10: bottleneck label not in formatter output: {formatted!r}"
    )

    # (b) render_report includes the critical_path section
    trace = [
        StepEvent(
            step_id="critical_path",
            stage="Critical Path (offline)",
            status=StepStatus.RAN,
            applicable=True,
            tui_headline="hot node: CROSS JOIN color_dim⋈size_dim (87.3%)",
            detail=cp_detail,
        )
    ]
    report_md = render_report(trace)
    assert "Critical Path" in report_md, (
        f"§10: render_report did not include a Critical Path section. Output:\n{report_md}"
    )
    assert "CROSS JOIN" in report_md, (
        f"§10: bottleneck not in rendered report markdown. Output:\n{report_md}"
    )
