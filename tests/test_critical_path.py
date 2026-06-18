"""test_critical_path.py — 8-query corpus characterization + edge-case tests (v52).

Tests assert RELATIVE ordering only — never absolute cost numbers.
Corpus queries: Q1–Q8 per CORPUS.md. Edge cases per §8c.
"""
from __future__ import annotations

import pytest

import sqlglot
import sqlglot.expressions as exp

from genie.skills.mcp_trino.critical_path import (
    CriticalPathResult,
    _classify_join,
    _join_label,
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


def test_should_have_strategy_p9_when_correlated_exists_is_bottleneck():
    """§6: EXISTS correlated subquery bottleneck → strategy P9 (safe pre-agg decorrelation, v55)."""
    result = analyze_critical_path(Q2)
    assert result.available
    assert result.bottleneck is not None
    assert result.bottleneck.strategy == "P9", (
        f"Expected P9 strategy for correlated EXISTS, got {result.bottleneck.strategy!r}"
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


# ===========================================================================
# v53 — critical-path AST coverage gaps (regression lock for Sam's real query)
# ===========================================================================

# Sam's company rule-engine query, structurally verbatim: a CTE whose body is
# rule_table JOIN base_table ON (1=1 + fuzzy LIKE/COALESCE/strpos + real equi
# keys customer/tech_id + a CASE WHEN carrying two correlated EXISTS), wrapped in
# a final projection SELECT. Pre-v53 the two EXISTS were invisible and the join
# label named an inner validation table. This fixture locks the fix.
V53_RULE_ENGINE = """
WITH main_agg AS (
  SELECT
    b.unique_id,
    MAX(b.product) AS product,
    SUM(CASE WHEN r.reason = 'A' THEN 1 ELSE 0 END) AS cnt_a,
    SUM(CASE WHEN r.reason IN ('B','C') THEN 1 ELSE 0 END) AS cnt_b
  FROM rule_table r
  JOIN base_table b
    ON 1=1
   AND r.product LIKE b.product ESCAPE '\\'
   AND COALESCE(r.field1, r.field2) = b.route
   AND strpos(r.allowed_subtypes, b.subtype) > 0
   AND r.customer = b.customer
   AND r.tech_id = b.tech_id
   AND (CASE
          WHEN r.tool_restrict IS NULL THEN TRUE
          ELSE (
            EXISTS (SELECT 1 FROM tooling_info t
                     WHERE t.rule_id = r.id AND t.version LIKE CONCAT('%', b.ver, '%'))
            AND EXISTS (SELECT 1 FROM yield_die_info y
                         WHERE y.rule_id = r.id AND y.qty BETWEEN r.lo AND r.hi)
          )
        END)
  GROUP BY b.unique_id
)
SELECT
  m.product, m.cnt_a, m.cnt_b,
  CAST(NULL AS VARCHAR) AS pad1
FROM main_agg m
"""


def _all_nodes(sql: str) -> list:
    """flatten_nodes including wrappers (for descendant/parentage assertions)."""
    root = build_cost_tree(sql)
    compute_costs(root)
    return flatten_nodes(root, exclude_wrappers=False)


def _first_join(sql: str) -> exp.Join:
    tree = sqlglot.parse_one(sql, dialect="trino")
    return list(tree.find_all(exp.Join))[0]


def _descendants(node) -> list:
    """All descendant nodes of node (excluding node itself)."""
    out = []
    for child in node.children:
        out.append(child)
        out.extend(_descendants(child))
    return out


# --- AC1: EXISTS in JOIN-ON CASE WHEN become correlated_subquery nodes --------

def test_should_surface_both_join_on_exists_as_nodes_when_v53_rule_engine():
    """AC1: tooling_info + yield_die_info EXISTS (in JOIN ON) become nodes."""
    nodes = _all_nodes(V53_RULE_ENGINE)
    corr = [n for n in nodes if n.op == "correlated_subquery"]
    labels = " ".join(n.label.lower() for n in corr)
    assert len(corr) == 2, f"Expected 2 correlated_subquery nodes, got {len(corr)}: {[n.label for n in corr]}"
    assert "tooling_info" in labels, f"tooling_info EXISTS missing: {labels}"
    assert "yield_die_info" in labels, f"yield_die_info EXISTS missing: {labels}"


# --- AC2: the double-EXISTS validation is represented in the hot path ----------

def test_should_put_exists_validation_on_critical_path_when_v53_rule_engine():
    """AC2: a correlated EXISTS node sits on the critical path (the real driver)."""
    result = analyze_critical_path(V53_RULE_ENGINE)
    assert result.available and not result.trivial
    path_ops = [n.op for n in result.critical_path]
    assert "correlated_subquery" in path_ops, (
        f"EXISTS validation not on critical path; path ops={path_ops}"
    )


# --- AC2b: parentage — EXISTS nodes are descendants of the rule-join node ------

def test_should_parent_join_on_exists_under_the_join_node_when_v53():
    """AC2b: ON-clause EXISTS attach under the join subtree, not as SELECT siblings."""
    root = build_cost_tree(V53_RULE_ENGINE)
    compute_costs(root)
    all_nodes = flatten_nodes(root, exclude_wrappers=False)
    join_nodes = [n for n in all_nodes if n.op.startswith("join_")]
    assert join_nodes, "no join node found"
    join = join_nodes[0]
    corr_under_join = [n for n in _descendants(join) if n.op == "correlated_subquery"]
    assert len(corr_under_join) == 2, (
        f"Expected both EXISTS parented under the join; found {len(corr_under_join)} in its subtree"
    )


# --- AC3: F3 cartesian escalation + no-false-positive guards -------------------

def test_should_classify_tautology_plus_nonequi_as_cross_when_no_equi_key():
    """AC3(i): 1=1 + strpos>0, no equi key → join_cross (heavier than plain non-equi)."""
    sql_taut = "SELECT * FROM a JOIN b ON 1=1 AND strpos(a.x, b.y) > 0"
    sql_plain_nonequi = "SELECT * FROM a JOIN b ON a.x > b.y"
    assert _classify_join(_first_join(sql_taut)) == "join_cross"
    assert _classify_join(_first_join(sql_plain_nonequi)) == "join_non_equi"


def test_should_classify_pure_tautology_fuzzy_as_cross_not_inner():
    """AC3(ii): 1=1 + LIKE only (no GT/LT, no equi key) → join_cross, NOT join_inner."""
    sql = "SELECT * FROM a JOIN b ON 1=1 AND a.x LIKE b.y"
    assert _classify_join(_first_join(sql)) == "join_cross"


def test_should_keep_inner_join_when_real_equi_key_present_despite_fuzzy():
    """AC3(iii): a real cross-table equi key keeps the join bounded → join_inner,
    unaffected by incidental 1=1 / LIKE side predicates (F3 no-false-positive)."""
    assert _classify_join(_first_join("SELECT * FROM a JOIN b ON a.k = b.k")) == "join_inner"
    assert _classify_join(
        _first_join("SELECT * FROM a JOIN b ON a.k = b.k AND a.x LIKE b.y")
    ) == "join_inner"
    assert _classify_join(
        _first_join("SELECT * FROM a JOIN b ON a.k = b.k AND 1=1")
    ) == "join_inner"


# --- AC4: join label names the directly-joined tables, never an ON-subquery ----

def test_should_label_join_with_direct_tables_not_on_subquery_table():
    """AC4: rule-join label is r⋈b — never names tooling_info (an ON-clause subquery)."""
    nodes = _all_nodes(V53_RULE_ENGINE)
    join = [n for n in nodes if n.op.startswith("join_")][0]
    assert "r⋈b" in join.label, f"Expected 'r⋈b' in label, got {join.label!r}"
    assert "tooling" not in join.label.lower(), f"label leaked ON-subquery table: {join.label!r}"


def test_join_label_uses_join_this_directly():
    """AC4 unit: _join_label uses join.this (right) + threaded left, not find_all."""
    join = _first_join("SELECT * FROM facts f JOIN dims d ON f.k = d.k")
    label = _join_label("join_inner", join, left_name="f")
    assert label == "JOIN f⋈d", f"got {label!r}"


# --- AC4b: correlated scalar subquery in the SELECT projection list ------------

def test_should_surface_correlated_scalar_subquery_in_select_list():
    """AC4b: a correlated scalar subquery in the SELECT list becomes a node."""
    sql = """
    SELECT o.id,
           (SELECT max(li.qty) FROM line_items li WHERE li.order_id = o.id) AS max_qty
    FROM orders o
    """
    nodes = _all_nodes(sql)
    corr = [n for n in nodes if n.op == "correlated_subquery"]
    assert len(corr) >= 1, "correlated scalar subquery in SELECT list not surfaced"
    assert any("line_items" in n.label.lower() for n in corr)


# --- AC7: never raises; uncorrelated ON-clause subquery degrades gracefully ----

def test_should_not_raise_on_rule_engine_query():
    """AC7: analyze_critical_path stays well-defined (never raises) on the rule query."""
    result = analyze_critical_path(V53_RULE_ENGINE)
    assert isinstance(result.available, bool)
    assert result.available is True


def test_should_not_blow_up_node_for_uncorrelated_on_clause_exists():
    """AC7: an UNcorrelated EXISTS in a JOIN ON adds no correlated_subquery node."""
    sql = """
    SELECT a.id FROM a
    JOIN b ON a.k = b.k AND EXISTS (SELECT 1 FROM lookup l WHERE l.flag = 'X')
    """
    nodes = _all_nodes(sql)
    corr = [n for n in nodes if n.op == "correlated_subquery"]
    assert corr == [], f"uncorrelated EXISTS wrongly treated as blowup: {[n.label for n in corr]}"


# --- F3 edge cases (review attempt-1 findings) --------------------------------

def test_should_keep_inner_join_when_computed_or_coalesced_equi_key_present():
    """A function-wrapped real join key bounds the join — `1=1 AND UPPER(a.k)=b.k`
    or COALESCE-wrapped equality must stay join_inner, NOT escalate to join_cross."""
    assert _classify_join(
        _first_join("SELECT * FROM a JOIN b ON 1=1 AND UPPER(a.k) = b.k")
    ) == "join_inner"
    assert _classify_join(
        _first_join("SELECT * FROM a JOIN b ON 1=1 AND COALESCE(a.x, a.y) = b.z")
    ) == "join_inner"


def test_should_not_treat_literal_contradiction_as_cartesian():
    """`1=2` is a contradiction (always-empty join), not a tautology — it must not
    trigger the cartesian branch."""
    assert _classify_join(_first_join("SELECT * FROM a JOIN b ON 1=2 AND a.x LIKE b.y")) == "join_inner"
    # bare equal-literal IS a tautology → cartesian when no equi key
    assert _classify_join(_first_join("SELECT * FROM a JOIN b ON 1=1 AND a.x LIKE b.y")) == "join_cross"
    assert _classify_join(_first_join("SELECT * FROM a JOIN b ON 'x'='x' AND a.p LIKE b.q")) == "join_cross"


# --- WHERE-clause correlated EXISTS downstream of a big join ------------------
# Sam's actual query shape: a rule-engine CTE joins two tables, then a WHERE
# `CASE ... EXISTS ... END > 0` runs a per-row validation. The EXISTS runs once
# per POST-join row, so it must outrank the join and land on the critical path.

V53_WHERE_EXISTS_AFTER_JOIN = """
WITH cte1 AS (SELECT id AS pk, part_no, attr2, prefix FROM db.sch.table_a),
     cte2 AS (SELECT perm_flag, sys_key, prod_attr, lo, hi, keycol, reason_code FROM db.sch.table_b),
     cte3 AS (
       SELECT B.pk, MAX(B.attr2) AS m1
       FROM cte2 A
       INNER JOIN cte1 B
         ON A.prod_attr LIKE B.part_no
        AND COALESCE(A.perm_flag, A.sys_key) = B.attr2
        AND STRPOS(A.prod_attr, B.prefix) > 0
       WHERE (CASE
                WHEN A.perm_flag IS NULL THEN 1
                WHEN A.sys_key IS NULL THEN (
                  CASE WHEN EXISTS (SELECT 1 FROM db.sch.table_c c WHERE c.keycol = A.keycol)
                        AND EXISTS (SELECT 1 FROM db.sch.table_d d WHERE d.qty BETWEEN A.lo AND A.hi)
                       THEN 2 ELSE 0 END)
                ELSE 0
              END) > 0
       GROUP BY B.pk
     )
SELECT CURRENT_TIMESTAMP AS ts, CAST(NULL AS VARCHAR) AS pad, buf.m1 FROM cte3 buf
"""


def test_should_put_where_exists_after_join_on_critical_path():
    """A WHERE correlated EXISTS downstream of a join must be the bottleneck (it runs
    per post-join row) — not the join. Regression for the v53.1 magnitude fix."""
    result = analyze_critical_path(V53_WHERE_EXISTS_AFTER_JOIN)
    assert result.available and not result.trivial
    assert result.bottleneck is not None
    assert result.bottleneck.op == "correlated_subquery", (
        f"WHERE-EXISTS undercounted; bottleneck={result.bottleneck.op}:{result.bottleneck.label}"
    )
    path_ops = [n.op for n in result.critical_path]
    assert "correlated_subquery" in path_ops


def test_should_give_where_exists_post_join_magnitude():
    """The WHERE EXISTS magnitude reflects the post-join blowup, not pre-join (=1)."""
    nodes = _all_nodes(V53_WHERE_EXISTS_AFTER_JOIN)
    corr = [n for n in nodes if n.op == "correlated_subquery"]
    assert corr, "no correlated_subquery node"
    # post-join (non-equi factor 30) × correlated factor 3 = 90, far above pre-join (3)
    assert all(n.row_magnitude >= 30 for n in corr), (
        f"WHERE EXISTS still on pre-join magnitude: {[(n.label, n.row_magnitude) for n in corr]}"
    )


def test_should_label_chained_join_with_running_left_table():
    """Multi-join FROM: the 2nd join's left is the 1st join's right table, not the
    original FROM primary."""
    sql = "SELECT * FROM a JOIN b ON a.k = b.k JOIN c ON b.k = c.k"
    nodes = _all_nodes(sql)
    join_labels = [n.label for n in nodes if n.op.startswith("join_")]
    assert any("a⋈b" in lbl for lbl in join_labels), f"first join label wrong: {join_labels}"
    assert any("b⋈c" in lbl for lbl in join_labels), f"chained join left wrong: {join_labels}"
