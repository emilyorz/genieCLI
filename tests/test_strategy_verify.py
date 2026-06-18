"""Tests for v56 offline structural verification + per-strategy checklists.

Pure-AST checks (no cluster). The iron rule under test: PROVEN_NO_FANOUT is only
reached when the rewrite genuinely pre-aggregates by the correlation key; every
ambiguous or wrong shape degrades to KEY_MISMATCH / CANNOT_VERIFY, never a false green.
"""
from __future__ import annotations

from genie.skills.mcp_trino.strategy_verify import (
    FanoutVerdict,
    render_advisory_verification,
    strategy_checklist,
    verify_p9_fanout,
)

# --- fixtures ---------------------------------------------------------------

# Original: a correlated EXISTS, inner table aliased (the realistic shape).
ORIGINAL_CORRELATED = """
SELECT b.id, b.val
FROM base b
WHERE EXISTS (SELECT 1 FROM spec s WHERE s.fk = b.id AND s.param = 'x')
"""

# Correct P9: pre-aggregate spec by fk (the correlation key), LEFT JOIN, COALESCE.
REWRITE_PROVEN = """
WITH spec_agg AS (SELECT fk, array_agg(param) AS params FROM spec GROUP BY fk)
SELECT b.id, b.val
FROM base b
LEFT JOIN spec_agg sa ON sa.fk = b.id
WHERE contains(coalesce(sa.params, ARRAY[]), 'x')
"""

# Wrong-grain P9: joined back on the correlation key fk, but grouped FINER (fk, param)
# → many rows per fk → fans out.
REWRITE_KEY_MISMATCH = """
WITH spec_agg AS (SELECT fk, param, array_agg(param) AS ps FROM spec GROUP BY fk, param)
SELECT b.id
FROM base b
LEFT JOIN spec_agg sa ON sa.fk = b.id
"""

# EXISTS removed but no pre-aggregation CTE — shape we can't prove offline.
REWRITE_NO_PREAGG = """
SELECT b.id
FROM base b
LEFT JOIN spec s ON s.fk = b.id
"""

ORIGINAL_NO_CORRELATION = "SELECT id FROM base WHERE val > 0"


# --- verify_p9_fanout -------------------------------------------------------

def test_should_prove_no_fanout_when_preagg_groups_by_correlation_key():
    r = verify_p9_fanout(ORIGINAL_CORRELATED, REWRITE_PROVEN)
    assert r.verdict is FanoutVerdict.PROVEN_NO_FANOUT
    assert "fk" in r.correlation_keys
    assert "fk" in r.groupby_keys


def test_should_flag_key_mismatch_when_preagg_groups_on_wrong_key():
    r = verify_p9_fanout(ORIGINAL_CORRELATED, REWRITE_KEY_MISMATCH)
    assert r.verdict is FanoutVerdict.KEY_MISMATCH
    assert "fk" in r.correlation_keys
    # grain is finer than the join key (extra 'param' column) → fan-out
    assert "param" in r.groupby_keys


def test_should_report_not_applied_when_rewrite_keeps_correlated_exists():
    # Qwen left the EXISTS in place (the conservative AST-2 outcome).
    r = verify_p9_fanout(ORIGINAL_CORRELATED, ORIGINAL_CORRELATED)
    assert r.verdict is FanoutVerdict.NOT_APPLIED


def test_should_report_not_applied_when_original_has_no_correlated_exists():
    r = verify_p9_fanout(ORIGINAL_NO_CORRELATION, ORIGINAL_NO_CORRELATION)
    assert r.verdict is FanoutVerdict.NOT_APPLIED


def test_should_not_verify_when_exists_removed_but_no_preagg_found():
    r = verify_p9_fanout(ORIGINAL_CORRELATED, REWRITE_NO_PREAGG)
    assert r.verdict is FanoutVerdict.CANNOT_VERIFY


def test_should_not_verify_on_empty_rewrite():
    r = verify_p9_fanout(ORIGINAL_CORRELATED, "")
    assert r.verdict is FanoutVerdict.CANNOT_VERIFY


def test_should_not_prove_when_groupby_matches_but_no_join_back_on_key():
    # Coincidental GROUP BY on a same-named column with no LEFT JOIN tying it back
    # must NOT earn PROVEN — guards against a name-collision false green.
    rewrite_no_joinback = """
    WITH spec_agg AS (SELECT fk, array_agg(param) AS params FROM spec GROUP BY fk)
    SELECT b.id FROM base b CROSS JOIN spec_agg sa
    """
    r = verify_p9_fanout(ORIGINAL_CORRELATED, rewrite_no_joinback)
    assert r.verdict is not FanoutVerdict.PROVEN_NO_FANOUT


# --- IRON-RULE regression: reviewer's confirmed false-PROVEN constructions (D/C/F) ---

def test_iron_rule_finer_grain_groupby_is_not_proven():
    # CASE D: GROUP BY (fk, param) is FINER than the join key fk → fans out.
    rewrite = """
    WITH spec_agg AS (SELECT fk, param, count(*) c FROM spec GROUP BY fk, param)
    SELECT b.id FROM base b LEFT JOIN spec_agg sa ON sa.fk = b.id
    """
    r = verify_p9_fanout(ORIGINAL_CORRELATED, rewrite)
    assert r.verdict is FanoutVerdict.KEY_MISMATCH


def test_iron_rule_wrong_source_table_is_not_proven():
    # CASE C: the pre-agg aggregates an UNRELATED table (predicate silently dropped).
    rewrite = """
    WITH other_agg AS (SELECT fk, count(*) c FROM unrelated_table GROUP BY fk)
    SELECT b.id FROM base b LEFT JOIN other_agg oa ON oa.fk = b.id
    """
    r = verify_p9_fanout(ORIGINAL_CORRELATED, rewrite)
    assert r.verdict is not FanoutVerdict.PROVEN_NO_FANOUT


def test_iron_rule_unreferenced_preagg_plus_unrelated_join_is_not_proven():
    # CASE F: a correct-looking pre-agg CTE is never referenced; the LEFT JOIN goes
    # to an unrelated table on a same-named column.
    rewrite = """
    WITH noise AS (SELECT fk, count(*) c FROM spec GROUP BY fk)
    SELECT b.id FROM base b LEFT JOIN dim d ON d.fk = b.id
    """
    r = verify_p9_fanout(ORIGINAL_CORRELATED, rewrite)
    assert r.verdict is not FanoutVerdict.PROVEN_NO_FANOUT


ORIGINAL_TWO_EXISTS = """
SELECT b.id FROM base b
WHERE EXISTS (SELECT 1 FROM spec s WHERE s.fk = b.id AND s.param = 'x')
  AND EXISTS (SELECT 1 FROM qty q WHERE q.fk = b.id AND q.n > 0)
"""


def test_iron_rule_partial_decorrelation_with_raw_join_is_not_proven():
    # BLOCKER 1 (round 2): spec decorrelated correctly, qty turned into a raw JOIN that
    # fans out. PROVEN must require ALL correlations safe — not the first clean bind.
    rewrite = """
    WITH spec_agg AS (SELECT fk, array_agg(param) p FROM spec GROUP BY fk)
    SELECT b.id FROM base b
    LEFT JOIN spec_agg sa ON sa.fk = b.id
    JOIN qty q ON q.fk = b.id
    WHERE contains(coalesce(sa.p, ARRAY[]), 'x') AND q.n > 0
    """
    r = verify_p9_fanout(ORIGINAL_TWO_EXISTS, rewrite)
    assert r.verdict is not FanoutVerdict.PROVEN_NO_FANOUT


def test_iron_rule_second_non_equi_join_to_same_cte_is_not_proven():
    # BLOCKER 2 (round 2): correct equi chain PLUS a second range LEFT JOIN to the SAME
    # CTE → fan-out. The proof must hold for ALL joins to the bound CTE.
    rewrite = """
    WITH spec_agg AS (SELECT fk, array_agg(param) p FROM spec GROUP BY fk)
    SELECT b.id FROM base b
    LEFT JOIN spec_agg sa  ON sa.fk = b.id
    LEFT JOIN spec_agg sa2 ON sa2.fk < b.id
    WHERE contains(coalesce(sa.p, ARRAY[]), 'x')
    """
    r = verify_p9_fanout(ORIGINAL_CORRELATED, rewrite)
    assert r.verdict is not FanoutVerdict.PROVEN_NO_FANOUT


def test_iron_rule_range_join_to_second_preagg_cte_is_not_proven():
    # B3 (round 3): one CTE binds cleanly, a SECOND pre-agg CTE (same table/key) is joined
    # by a range predicate → fan-out. The global join pass must catch it regardless of bind.
    rewrite = """
    WITH ca AS (SELECT fk, array_agg(param) p FROM spec GROUP BY fk),
         cb AS (SELECT fk, count(*) c FROM spec GROUP BY fk)
    SELECT b.id FROM base b
    LEFT JOIN ca ON ca.fk = b.id
    LEFT JOIN cb ON cb.fk < b.id
    WHERE contains(coalesce(ca.p, ARRAY[]), 'x')
    """
    r = verify_p9_fanout(ORIGINAL_CORRELATED, rewrite)
    assert r.verdict is not FanoutVerdict.PROVEN_NO_FANOUT


def test_iron_rule_non_key_equi_join_to_bound_cte_is_not_proven():
    # B6 (round 3): the bound CTE is also joined a SECOND time on a non-group-key column
    # (param, not the group key fk) → not unique on that join → fan-out.
    rewrite = """
    WITH spec_agg AS (SELECT fk, array_agg(param) p FROM spec GROUP BY fk)
    SELECT b.id FROM base b
    LEFT JOIN spec_agg sa  ON sa.fk = b.id
    LEFT JOIN spec_agg sa2 ON sa2.param = b.cat
    WHERE contains(coalesce(sa.p, ARRAY[]), 'x')
    """
    r = verify_p9_fanout(ORIGINAL_CORRELATED, rewrite)
    assert r.verdict is not FanoutVerdict.PROVEN_NO_FANOUT


def test_iron_rule_expr_group_cte_with_range_join_is_not_proven():
    # C4 (round 4): a 2nd CTE grouped by an EXPRESSION (date_trunc) joined by a range →
    # fan-out. The expr-group skip must not buy it a free pass.
    rewrite = """
    WITH ca AS (SELECT fk, array_agg(param) p FROM spec GROUP BY fk),
         cz AS (SELECT date_trunc('day', ts) d, count(*) c FROM spec GROUP BY date_trunc('day', ts))
    SELECT b.id FROM base b
    LEFT JOIN ca ON ca.fk = b.id
    LEFT JOIN cz ON cz.d < b.day
    WHERE contains(coalesce(ca.p, ARRAY[]), 'x')
    """
    r = verify_p9_fanout(ORIGINAL_CORRELATED, rewrite)
    assert r.verdict is not FanoutVerdict.PROVEN_NO_FANOUT


def test_iron_rule_extra_raw_dimension_join_is_not_proven():
    # A clean decorrelation PLUS an extra raw-table JOIN that can fan out → not closed.
    rewrite = """
    WITH ca AS (SELECT fk, array_agg(param) p FROM spec GROUP BY fk)
    SELECT b.id FROM base b
    LEFT JOIN ca ON ca.fk = b.id
    JOIN huge_dim d ON d.x = b.x
    WHERE contains(coalesce(ca.p, ARRAY[]), 'x')
    """
    r = verify_p9_fanout(ORIGINAL_CORRELATED, rewrite)
    assert r.verdict is FanoutVerdict.CANNOT_VERIFY


def test_iron_rule_comma_join_is_not_proven():
    rewrite = """
    WITH ca AS (SELECT fk, array_agg(param) p FROM spec GROUP BY fk)
    SELECT b.id FROM base b, dim d
    LEFT JOIN ca ON ca.fk = b.id
    WHERE contains(coalesce(ca.p, ARRAY[]), 'x')
    """
    r = verify_p9_fanout(ORIGINAL_CORRELATED, rewrite)
    assert r.verdict is FanoutVerdict.CANNOT_VERIFY


def test_nested_base_cte_is_not_proven_conservatively():
    # base is itself a CTE → we do not recurse offline → conservative CANNOT_VERIFY.
    rewrite = """
    WITH ca AS (SELECT fk, array_agg(param) p FROM spec GROUP BY fk),
         benr AS (SELECT id, fk FROM base)
    SELECT benr.id FROM benr
    LEFT JOIN ca ON ca.fk = benr.fk
    WHERE contains(coalesce(ca.p, ARRAY[]), 'x')
    """
    r = verify_p9_fanout(ORIGINAL_CORRELATED, rewrite)
    assert r.verdict is FanoutVerdict.CANNOT_VERIFY


def test_iron_rule_right_join_to_preagg_is_not_proven():
    # D1 (round 5): RIGHT JOIN emits unmatched CTE-side rows → row set grows.
    rewrite = """
    WITH spec_agg AS (SELECT fk, array_agg(param) p FROM spec GROUP BY fk)
    SELECT b.id FROM base b RIGHT JOIN spec_agg sa ON sa.fk = b.id
    WHERE contains(coalesce(sa.p, ARRAY[]), 'x')
    """
    r = verify_p9_fanout(ORIGINAL_CORRELATED, rewrite)
    assert r.verdict is not FanoutVerdict.PROVEN_NO_FANOUT


def test_iron_rule_full_join_to_preagg_is_not_proven():
    # D2 (round 5): FULL JOIN emits unmatched rows from both sides.
    rewrite = """
    WITH spec_agg AS (SELECT fk, array_agg(param) p FROM spec GROUP BY fk)
    SELECT b.id FROM base b FULL JOIN spec_agg sa ON sa.fk = b.id
    WHERE contains(coalesce(sa.p, ARRAY[]), 'x')
    """
    r = verify_p9_fanout(ORIGINAL_CORRELATED, rewrite)
    assert r.verdict is not FanoutVerdict.PROVEN_NO_FANOUT


def test_iron_rule_inner_join_to_preagg_is_not_proven():
    # D3 (round 5): INNER JOIN drops non-matching base rows → changes the row set; the
    # verdict would falsely claim "LEFT JOINed back". Only LEFT reaches PROVEN.
    rewrite = """
    WITH spec_agg AS (SELECT fk, array_agg(param) p FROM spec GROUP BY fk)
    SELECT b.id FROM base b JOIN spec_agg sa ON sa.fk = b.id
    WHERE contains(coalesce(sa.p, ARRAY[]), 'x')
    """
    r = verify_p9_fanout(ORIGINAL_CORRELATED, rewrite)
    assert r.verdict is not FanoutVerdict.PROVEN_NO_FANOUT


def test_two_exists_both_decorrelated_is_proven():
    # The positive: both EXISTS pre-aggregated into bound CTEs → genuinely no fan-out.
    rewrite = """
    WITH spec_agg AS (SELECT fk, array_agg(param) p FROM spec GROUP BY fk),
         qty_agg  AS (SELECT fk, max(n) mx FROM qty GROUP BY fk)
    SELECT b.id FROM base b
    LEFT JOIN spec_agg sa ON sa.fk = b.id
    LEFT JOIN qty_agg  qa ON qa.fk = b.id
    WHERE contains(coalesce(sa.p, ARRAY[]), 'x') AND coalesce(qa.mx, 0) > 0
    """
    r = verify_p9_fanout(ORIGINAL_TWO_EXISTS, rewrite)
    assert r.verdict is FanoutVerdict.PROVEN_NO_FANOUT


def test_iron_rule_dropped_truth_filter_still_count_safe():
    # CASE E: pre-agg by fk, joined on fk, but the EXISTS truth filter is dropped.
    # Row-COUNT is genuinely safe (this is what PROVEN claims) — the dropped filter is a
    # row-VALUE issue the checklist must catch, not the fan-out check. Document the boundary.
    rewrite = """
    WITH spec_agg AS (SELECT fk, array_agg(param) AS params FROM spec GROUP BY fk)
    SELECT b.id FROM base b LEFT JOIN spec_agg sa ON sa.fk = b.id
    """
    r = verify_p9_fanout(ORIGINAL_CORRELATED, rewrite)
    assert r.verdict is FanoutVerdict.PROVEN_NO_FANOUT  # count-safe; value-safety is on the checklist


def test_never_false_green_on_garbage_rewrite():
    # Iron rule: a rewrite that does not clearly decorrelate must never come back PROVEN.
    r = verify_p9_fanout(ORIGINAL_CORRELATED, "this is not valid sql ;;;")
    assert r.verdict is not FanoutVerdict.PROVEN_NO_FANOUT


# --- strategy_checklist -----------------------------------------------------

def test_p9_checklist_is_non_empty_and_covers_key_and_coalesce():
    items = strategy_checklist("P9")
    assert items
    joined = " ".join(items).lower()
    assert "group by" in joined
    assert "coalesce" in joined


def test_safe_strategy_has_no_checklist():
    # P1 (function-pushup) is SAFE — no verification checklist.
    assert strategy_checklist("P1") == ()


def test_unknown_strategy_returns_empty_checklist():
    assert strategy_checklist("P99") == ()


# --- render_advisory_verification -------------------------------------------

def test_render_returns_empty_when_no_correlated_exists():
    assert render_advisory_verification(ORIGINAL_NO_CORRELATION, ORIGINAL_NO_CORRELATION) == []


def test_render_emits_verdict_and_checklist_for_proven_rewrite():
    lines = render_advisory_verification(ORIGINAL_CORRELATED, REWRITE_PROVEN)
    block = "\n".join(lines)
    assert "Offline strategy verification" in block
    assert "PROVEN" in block
    assert "- [ ]" in block  # checklist items rendered as task boxes


def test_render_flags_key_mismatch_loudly():
    lines = render_advisory_verification(ORIGINAL_CORRELATED, REWRITE_KEY_MISMATCH)
    block = "\n".join(lines)
    assert "LIKELY WRONG" in block
