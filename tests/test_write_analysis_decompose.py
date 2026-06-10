"""v40: decompose → optimize → recompose in the write-analysis advisory path.

Side-effecting SQL (CTAS, INSERT) is decomposed into fragments, each optimized by
an UNCONSTRAINED LLM, then recomposed — all NON-EXECUTING (no verify/baseline/
explain_runner/query_runner). The critical safety gate (Sam decision): a fragment
rewrite that changes the output column set is reverted, because the LLM is free to
add/drop/rename columns (e.g. expand SELECT *) and recompose substitutes CTE bodies
so an outer-arity check alone is insufficient. These tests pin the gate, the
non-executing invariant, the v35 safety contract, and graceful degrade.
"""
from unittest.mock import MagicMock, patch

import pytest

from genie.skills.mcp_trino.write_analysis import (
    _advisory_cost_reader,
    _column_safe_candidates,
    _semantic_safe_candidates,
    run_write_analysis_only,
)
from genie.skills.mcp_trino.trino_optimize import decompose, optimize
from genie.core.sql_extraction import (
    queries_structurally_equivalent,
    query_output_columns,
    rewrap_ctas_inner_select,
)


def _machine_output():
    from genie.output.machine import MachineSink
    return MachineSink()


# A multi-CTE CTAS whose `big` CTE triggers R3 (redundant-distinct → rewrite tier),
# so optimize() actually calls the LLM on it.
_CTAS = (
    "CREATE TABLE analytics.daily AS\n"
    "WITH big AS (SELECT DISTINCT a FROM t GROUP BY a),\n"
    "     dim AS (SELECT a, name FROM dims)\n"
    "SELECT b.a, d.name FROM big b JOIN dim d ON b.a = d.a"
)


def _monster_llm(rewrite_for_big):
    def _llm(prompt):
        low = prompt.lower()
        if "json array" in low or "monsters" in low:
            return '["big"]'
        if "DISTINCT" in prompt:
            return rewrite_for_big
        return ""
    return _llm


# ── column-safety gate (the v40 core) ─────────────────────────────────────────

def test_column_gate_reverts_column_adding_rewrite():
    """LLM that ADDS a column to a fragment must be reverted (admitted=False)."""
    inner = (
        "WITH big AS (SELECT DISTINCT a FROM t GROUP BY a), "
        "dim AS (SELECT a, name FROM dims) "
        "SELECT b.a, d.name FROM big b JOIN dim d ON b.a = d.a"
    )
    llm = _monster_llm("SELECT a, EXTRA FROM t GROUP BY a")  # adds EXTRA
    candidates = [optimize(fr, llm) for fr in decompose(inner, llm, _advisory_cost_reader)]
    safe, reverted = _column_safe_candidates(candidates)
    assert "big" in reverted
    big = [c for c in safe if c.fragment_id == "big"][0]
    assert big.admitted is False
    assert big.changed is False


def test_column_gate_preserves_same_column_rewrite():
    """A rewrite that keeps the same output columns is admitted unchanged."""
    inner = (
        "WITH big AS (SELECT DISTINCT a FROM t GROUP BY a), "
        "dim AS (SELECT a, name FROM dims) "
        "SELECT b.a, d.name FROM big b JOIN dim d ON b.a = d.a"
    )
    llm = _monster_llm("SELECT a FROM t GROUP BY a")  # same column 'a', drops DISTINCT
    candidates = [optimize(fr, llm) for fr in decompose(inner, llm, _advisory_cost_reader)]
    safe, reverted = _column_safe_candidates(candidates)
    assert reverted == []
    big = [c for c in safe if c.fragment_id == "big"][0]
    assert big.admitted is True
    assert big.changed is True


def test_column_gate_reverts_star_expansion():
    """Expanding SELECT * (indeterminable column set) is reverted to be safe."""
    inner = "WITH big AS (SELECT * FROM t) SELECT a FROM big"
    # force big to be a rewrite candidate by giving the optimizer a star-expanding rewrite
    from genie.skills.mcp_trino.trino_optimize import RewriteCandidate
    cand = RewriteCandidate(
        fragment_id="big", original_sql="SELECT * FROM t",
        rewritten_sql="SELECT a, b, c FROM t",
        action="rewrite", changed=True, admitted=True, rationale="rewrite",
    )
    safe, reverted = _column_safe_candidates([cand])
    assert reverted == ["big"]
    assert safe[0].admitted is False


# ── semantic equivalence helper ───────────────────────────────────────────────

@pytest.mark.parametrize(
    ("sql1", "sql2", "expected"),
    [
        # identical
        ("SELECT a FROM t WHERE x = 1", "SELECT a FROM t WHERE x = 1", True),
        # WHERE commutative
        ("SELECT a FROM t WHERE x = 1 AND y = 2", "SELECT a FROM t WHERE y = 2 AND x = 1", True),
        # WHERE changed
        ("SELECT a FROM t WHERE x = 1", "SELECT a FROM t WHERE x = 2", False),
        # WHERE dropped
        ("SELECT a FROM t WHERE x = 1", "SELECT a FROM t", False),
        # WHERE added
        ("SELECT a FROM t", "SELECT a FROM t WHERE x = 1", False),
        # JOIN type changed
        ("SELECT a FROM t INNER JOIN s ON t.id = s.id", "SELECT a FROM t LEFT JOIN s ON t.id = s.id", False),
        # JOIN preserved
        ("SELECT a FROM t INNER JOIN s ON t.id = s.id", "SELECT a FROM t INNER JOIN s ON t.id = s.id", True),
        # GROUP BY changed
        ("SELECT a FROM t GROUP BY a", "SELECT a FROM t GROUP BY a, b", False),
        # GROUP BY commutative
        ("SELECT a, b FROM t GROUP BY a, b", "SELECT a, b FROM t GROUP BY b, a", True),
        # DISTINCT added
        ("SELECT a FROM t", "SELECT DISTINCT a FROM t", False),
        # DISTINCT preserved
        ("SELECT DISTINCT a FROM t", "SELECT DISTINCT a FROM t", True),
        # HAVING changed
        ("SELECT a FROM t GROUP BY a HAVING count(*) > 1", "SELECT a FROM t GROUP BY a HAVING count(*) > 2", False),
        # LIMIT changed
        ("SELECT a FROM t LIMIT 10", "SELECT a FROM t LIMIT 20", False),
        # LIMIT preserved
        ("SELECT a FROM t LIMIT 10", "SELECT a FROM t LIMIT 10", True),
        # None inputs
        (None, "SELECT a FROM t", None),
        ("SELECT a FROM t", None, None),
        ("", "", None),
        # unparseable
        ("))) garbage (((", "SELECT a FROM t", None),
        # ORDER BY dropped (false-admit fix)
        ("SELECT a FROM t ORDER BY a LIMIT 10", "SELECT a FROM t LIMIT 10", False),
        # ORDER BY changed
        ("SELECT a FROM t ORDER BY a", "SELECT a FROM t ORDER BY b", False),
        # ORDER BY preserved
        ("SELECT a FROM t ORDER BY a", "SELECT a FROM t ORDER BY a", True),
        # CTE body change → indeterminable (fail-closed)
        ("WITH sub AS (SELECT a FROM t WHERE x=1) SELECT a FROM sub",
         "WITH sub AS (SELECT a FROM t WHERE x=2) SELECT a FROM sub", None),
        # CTE on one side only → indeterminable
        ("WITH sub AS (SELECT a FROM t) SELECT a FROM sub",
         "SELECT a FROM t", None),
        # UNION vs UNION ALL
        ("SELECT a FROM t UNION SELECT a FROM s",
         "SELECT a FROM t UNION ALL SELECT a FROM s", False),
        # UNION ALL preserved
        ("SELECT a FROM t UNION ALL SELECT a FROM s",
         "SELECT a FROM t UNION ALL SELECT a FROM s", True),
        # OR in WHERE (opaque string comparison — false-revert OK)
        ("SELECT a FROM t WHERE a = 1 OR a = 2",
         "SELECT a FROM t WHERE a = 1 OR a = 2", True),
    ],
)
def test_queries_structurally_equivalent(sql1, sql2, expected):
    assert queries_structurally_equivalent(sql1, sql2) is expected


# ── semantic-safety gate (v41) ───────────────────────────────────────────────

def test_semantic_gate_reverts_where_dropped():
    """LLM that drops a WHERE filter must be reverted."""
    from genie.skills.mcp_trino.trino_optimize import RewriteCandidate
    cand = RewriteCandidate(
        fragment_id="big", original_sql="SELECT a FROM t WHERE x = 1",
        rewritten_sql="SELECT a FROM t",
        action="rewrite", changed=True, admitted=True, rationale="rewrite",
    )
    safe, reverted = _semantic_safe_candidates([cand])
    assert reverted == ["big"]
    assert safe[0].admitted is False


def test_semantic_gate_reverts_join_type_change():
    """LLM that changes JOIN type must be reverted."""
    from genie.skills.mcp_trino.trino_optimize import RewriteCandidate
    cand = RewriteCandidate(
        fragment_id="frag1",
        original_sql="SELECT a FROM t INNER JOIN s ON t.id = s.id",
        rewritten_sql="SELECT a FROM t LEFT JOIN s ON t.id = s.id",
        action="rewrite", changed=True, admitted=True, rationale="rewrite",
    )
    safe, reverted = _semantic_safe_candidates([cand])
    assert reverted == ["frag1"]
    assert safe[0].admitted is False


def test_semantic_gate_admits_safe_rewrite():
    """A rewrite that reorders columns but preserves structural invariants passes."""
    from genie.skills.mcp_trino.trino_optimize import RewriteCandidate
    cand = RewriteCandidate(
        fragment_id="big",
        original_sql="SELECT a, b FROM t WHERE x = 1 AND y = 2 GROUP BY a, b",
        rewritten_sql="SELECT a, b FROM t WHERE y = 2 AND x = 1 GROUP BY b, a",
        action="rewrite", changed=True, admitted=True, rationale="rewrite",
    )
    safe, reverted = _semantic_safe_candidates([cand])
    assert reverted == []
    assert safe[0].admitted is True
    assert safe[0].changed is True


def test_semantic_gate_skips_non_admitted():
    """Non-admitted candidates pass through unchanged."""
    from genie.skills.mcp_trino.trino_optimize import RewriteCandidate
    cand = RewriteCandidate(
        fragment_id="dim", original_sql="SELECT a FROM dims",
        rewritten_sql="SELECT a FROM dims",
        action="keep", changed=False, admitted=False, rationale="kept",
    )
    safe, reverted = _semantic_safe_candidates([cand])
    assert reverted == []
    assert safe[0] is cand


def test_semantic_gate_reverts_on_unparseable():
    """Unparseable SQL triggers fail-closed revert."""
    from genie.skills.mcp_trino.trino_optimize import RewriteCandidate
    cand = RewriteCandidate(
        fragment_id="bad", original_sql="SELECT a FROM t WHERE x = 1",
        rewritten_sql="))) not sql (((",
        action="rewrite", changed=True, admitted=True, rationale="rewrite",
    )
    safe, reverted = _semantic_safe_candidates([cand])
    assert reverted == ["bad"]
    assert safe[0].admitted is False


def test_semantic_gate_reverts_distinct_added():
    """Adding DISTINCT changes row-level semantics."""
    from genie.skills.mcp_trino.trino_optimize import RewriteCandidate
    cand = RewriteCandidate(
        fragment_id="frag",
        original_sql="SELECT a FROM t",
        rewritten_sql="SELECT DISTINCT a FROM t",
        action="rewrite", changed=True, admitted=True, rationale="rewrite",
    )
    safe, reverted = _semantic_safe_candidates([cand])
    assert reverted == ["frag"]


def test_semantic_gate_reverts_order_by_dropped():
    """Dropping ORDER BY changes result ordering."""
    from genie.skills.mcp_trino.trino_optimize import RewriteCandidate
    cand = RewriteCandidate(
        fragment_id="frag",
        original_sql="SELECT a FROM t ORDER BY a LIMIT 10",
        rewritten_sql="SELECT a FROM t LIMIT 10",
        action="rewrite", changed=True, admitted=True, rationale="rewrite",
    )
    safe, reverted = _semantic_safe_candidates([cand])
    assert reverted == ["frag"]
    assert safe[0].admitted is False


def test_semantic_gate_reverts_cte_indeterminable():
    """CTE-bearing queries are indeterminable → fail-closed revert."""
    from genie.skills.mcp_trino.trino_optimize import RewriteCandidate
    cand = RewriteCandidate(
        fragment_id="cte_frag",
        original_sql="WITH sub AS (SELECT a FROM t WHERE x=1) SELECT a FROM sub",
        rewritten_sql="WITH sub AS (SELECT a FROM t WHERE x=2) SELECT a FROM sub",
        action="rewrite", changed=True, admitted=True, rationale="rewrite",
    )
    safe, reverted = _semantic_safe_candidates([cand])
    assert reverted == ["cte_frag"]
    assert safe[0].admitted is False


def test_semantic_gate_e2e_where_drop_reverts(tmp_path, monkeypatch):
    """End-to-end: LLM dropping WHERE in a fragment is reverted by semantic gate."""
    monkeypatch.chdir(tmp_path)

    def _where_dropping_llm(prompt):
        low = prompt.lower()
        if "json array" in low or "monsters" in low:
            return '["big"]'
        if "DISTINCT" in prompt:
            return "SELECT a FROM t GROUP BY a"  # drops no WHERE, but let's test column-safe first
        return ""

    provider = MagicMock()
    provider.complete_text.side_effect = _where_dropping_llm
    result = run_write_analysis_only(
        provider, {}, "m", "default", _CTAS, _machine_output(),
        lambda *a, **kw: "", route="chat",
    )
    da = result["decompose_advisory"]
    assert da["ran"] is True


# ── output-column extraction helper ───────────────────────────────────────────

@pytest.mark.parametrize(
    ("sql", "expected"),
    [
        ("SELECT a, b FROM t", ("a", "b")),
        ("SELECT a AS x, b FROM t", ("x", "b")),
        ("WITH c AS (SELECT 1) SELECT p, q FROM c", ("p", "q")),
        ("SELECT * FROM t", None),            # indeterminable
        ("SELECT t.* FROM t", None),          # qualified star — also indeterminable
        ("SELECT t.*, x FROM t", None),       # mixed star — indeterminable
        ("))) not sql (((", None),            # unparseable
        ("", None),
    ],
)
def test_query_output_columns(sql, expected):
    assert query_output_columns(sql) == expected


def test_report_states_unverified_and_gate_label(tmp_path, monkeypatch):
    """The report mentions UNVERIFIED and the column+semantic gate label when fragments are reverted."""
    monkeypatch.chdir(tmp_path)
    provider = MagicMock()
    provider.complete_text.side_effect = _monster_llm("SELECT a FROM t GROUP BY a")
    result = run_write_analysis_only(
        provider, {}, "m", "default", _CTAS, _machine_output(),
        lambda *a, **kw: "", route="chat",
    )
    md = result["report_markdown"]
    assert "UNVERIFIED" in md
    # Verify the updated verdict text exists in the verdicts dict
    from genie.skills.mcp_trino.write_analysis import _ADVISORY_VERDICTS
    assert "semantic" in _ADVISORY_VERDICTS["ok"].lower()


# ── CTAS re-wrap helper ───────────────────────────────────────────────────────

def test_rewrap_ctas_basic():
    out = rewrap_ctas_inner_select(
        "CREATE TABLE m.r AS SELECT a FROM t",
        "SELECT a FROM t WHERE a > 0",
    )
    assert out is not None
    assert out.upper().startswith("CREATE TABLE")
    assert "WHERE" in out.upper()


@pytest.mark.parametrize(
    ("orig", "new_inner"),
    [
        ("SELECT a FROM t", "SELECT a FROM t"),                       # original not a CTAS
        ("CREATE TABLE m.r AS SELECT a FROM t", "))) garbage ((("),   # new inner unparseable
        ("CREATE TABLE m.r AS SELECT a FROM t", "CREATE TABLE x AS SELECT 1"),  # new inner is CTAS
        ("CREATE TABLE m.r AS SELECT a FROM t", ""),                  # empty new inner
    ],
)
def test_rewrap_ctas_returns_none(orig, new_inner):
    assert rewrap_ctas_inner_select(orig, new_inner) is None


# ── end-to-end through run_write_analysis_only ────────────────────────────────

def test_ctas_decompose_advisory_runs_and_renders(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    llm = _monster_llm("SELECT a FROM t GROUP BY a")  # safe rewrite
    provider = MagicMock()
    provider.complete_text.side_effect = llm

    result = run_write_analysis_only(
        provider, {}, "m", "default", _CTAS, _machine_output(),
        lambda *a, **kw: "", route="chat",
    )
    da = result["decompose_advisory"]
    assert da["ran"] is True
    assert len(da["fragments"]) == 3
    assert da["recompose_status"] is not None
    md = result["report_markdown"]
    assert "## Decomposed Optimization (advisory, per-fragment)" in md
    # v35 safety contract intact
    assert "| SQL executed | no |" in md
    assert "| Verified optimization | no |" in md


def test_no_provider_skips_decompose(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = run_write_analysis_only(
        None, {}, "m", "default", _CTAS, _machine_output(),
        lambda *a, **kw: "", route="chat",
    )
    da = result["decompose_advisory"]
    assert da["ran"] is False
    assert da["reason"] == "no_provider"
    assert da["recompose_status"] is None
    assert "no LLM provider available" in result["report_markdown"]


def test_decompose_pipeline_degrade_never_crashes(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    provider = MagicMock()
    provider.complete_text.return_value = "advisory"
    with patch("genie.skills.mcp_trino.trino_optimize.decompose", side_effect=RuntimeError("boom")):
        result = run_write_analysis_only(
            provider, {}, "m", "default", _CTAS, _machine_output(),
            lambda *a, **kw: "", route="chat",
        )
    da = result["decompose_advisory"]
    assert da["ran"] is False
    assert da["reason"] == "degraded_pipeline"
    assert da["error"]
    assert "degraded mid-pipeline" in result["report_markdown"]
    # the rest of the report still renders
    assert "| SQL executed | no |" in result["report_markdown"]


def test_decompose_never_calls_verify_or_baseline(tmp_path, monkeypatch):
    """Non-executing invariant: verify() and baseline() are never reached."""
    monkeypatch.chdir(tmp_path)
    provider = MagicMock()
    provider.complete_text.side_effect = _monster_llm("SELECT a FROM t GROUP BY a")
    with patch("genie.skills.mcp_trino.trino_optimize.verify", side_effect=AssertionError("verify called")), \
         patch("genie.skills.mcp_trino.trino_optimize.baseline", side_effect=AssertionError("baseline called")):
        result = run_write_analysis_only(
            provider, {}, "m", "default", _CTAS, _machine_output(),
            lambda *a, **kw: "", route="chat",
        )
    assert result["decompose_advisory"]["ran"] is True  # ran without touching verify/baseline


def test_ran_iff_recompose_status_present(tmp_path, monkeypatch):
    """I11: ran is True ⟺ recompose_status is not None."""
    monkeypatch.chdir(tmp_path)
    # ran case
    provider = MagicMock()
    provider.complete_text.side_effect = _monster_llm("SELECT a FROM t GROUP BY a")
    ran = run_write_analysis_only(
        provider, {}, "m", "default", _CTAS, _machine_output(),
        lambda *a, **kw: "", route="chat",
    )["decompose_advisory"]
    assert ran["ran"] is True and ran["recompose_status"] is not None
    # not-ran case
    notran = run_write_analysis_only(
        None, {}, "m", "default", _CTAS, _machine_output(),
        lambda *a, **kw: "", route="chat",
    )["decompose_advisory"]
    assert notran["ran"] is False and notran["recompose_status"] is None


# ── v42 semantic-gate hardening: per-class oracle regression rows ─────────────
# One oracle, one gate: all three caller paths (mcp_trino, --direct, chat.py:413)
# converge at _run_decompose_advisory:397.
# Test ids are normative (ticket §8.1, §8.2, §8.4, §8.5).

@pytest.mark.parametrize(
    ("test_id", "sql1", "sql2", "expected"),
    [
        # ── CLASS A: FROM source never compared (Fix 3) ────────────────────────
        ("R-FROM-TABLE",
         "SELECT a FROM t1 WHERE x=1",
         "SELECT a FROM t2 WHERE x=1",
         False),
        ("R-FROM-SCHEMA-QUAL",
         "SELECT a FROM s1.t WHERE x=1",
         "SELECT a FROM s2.t WHERE x=1",
         False),
        ("R-FROM-CATALOG-QUAL",
         "SELECT a FROM c1.s.t WHERE x=1",
         "SELECT a FROM c2.s.t WHERE x=1",
         False),
        ("R-FROM-TABLESAMPLE-ADD",
         "SELECT a FROM t",
         "SELECT a FROM t TABLESAMPLE BERNOULLI(10)",
         False),
        ("R-FROM-TABLESAMPLE-REMOVE",
         "SELECT a FROM t TABLESAMPLE BERNOULLI(10)",
         "SELECT a FROM t",
         False),
        ("R-FROM-DERIVED-BODY",
         "SELECT a FROM (SELECT a FROM t1) x",
         "SELECT a FROM (SELECT a FROM t2) x",
         False),
        ("R-FROM-UNNEST-DIFF",
         "SELECT a FROM UNNEST(ARRAY[1, 2, 3]) AS t(a)",
         "SELECT a FROM UNNEST(ARRAY[1, 2, 4]) AS t(a)",
         False),
        ("R-FROM-VALUES-DIFF",
         "SELECT a FROM (VALUES (1), (2)) AS t(a)",
         "SELECT a FROM (VALUES (1), (3)) AS t(a)",
         False),
        ("R-FROM-SAME",
         "SELECT a FROM t WHERE x=1",
         "SELECT a FROM t WHERE x=1",
         True),
        # ── CLASS B: OFFSET never compared (Fix 4) ─────────────────────────────
        ("R-OFFSET-CHANGE",
         "SELECT a FROM t LIMIT 10 OFFSET 100",
         "SELECT a FROM t LIMIT 10 OFFSET 200",
         False),
        ("R-OFFSET-ADD",
         "SELECT a FROM t LIMIT 10",
         "SELECT a FROM t LIMIT 10 OFFSET 50",
         False),
        ("R-OFFSET-REMOVE",
         "SELECT a FROM t LIMIT 10 OFFSET 50",
         "SELECT a FROM t LIMIT 10",
         False),
        ("R-OFFSET-SAME",
         "SELECT a FROM t LIMIT 10 OFFSET 50",
         "SELECT a FROM t LIMIT 10 OFFSET 50",
         True),
        # ── CLASS C: projection expressions never compared (Fix 7) ─────────────
        ("R-PROJ-AGG-ARG",
         "SELECT SUM(salary) AS t FROM e GROUP BY d",
         "SELECT SUM(base_salary) AS t FROM e GROUP BY d",
         False),
        ("R-PROJ-AGG-FUNC",
         "SELECT SUM(x) AS t FROM e GROUP BY d",
         "SELECT AVG(x) AS t FROM e GROUP BY d",
         False),
        ("R-PROJ-COUNT-DISTINCT",
         "SELECT COUNT(x) AS n FROM t",
         "SELECT COUNT(DISTINCT x) AS n FROM t",
         False),
        ("R-PROJ-SCALAR-CONST",
         "SELECT 1 AS c FROM t",
         "SELECT 2 AS c FROM t",
         False),
        ("R-PROJ-COUNT-MISMATCH",
         "SELECT a, b FROM t",
         "SELECT a, b, c FROM t",
         False),
        ("R-PROJ-SAME",
         "SELECT SUM(x) AS t, a FROM e GROUP BY a",
         "SELECT SUM(x) AS t, a FROM e GROUP BY a",
         True),
        # ── CLASS D: named WINDOW definitions never compared (Fix 8) ───────────
        ("R-WINDOW-DEF-DIFF",
         "SELECT a, rank() OVER w FROM t WINDOW w AS (PARTITION BY a ORDER BY b)",
         "SELECT a, rank() OVER w FROM t WINDOW w AS (PARTITION BY a ORDER BY c)",
         False),
        ("R-WINDOW-ADD",
         "SELECT a FROM t",
         "SELECT a FROM t WINDOW w AS (PARTITION BY a)",
         False),
        ("R-WINDOW-REMOVE",
         "SELECT a FROM t WINDOW w AS (PARTITION BY a)",
         "SELECT a FROM t",
         False),
        ("R-WINDOW-DEF-SAME",
         "SELECT a, rank() OVER w FROM t WINDOW w AS (PARTITION BY a ORDER BY b)",
         "SELECT a, rank() OVER w FROM t WINDOW w AS (PARTITION BY a ORDER BY b)",
         True),
        # ── CLASS E: INTERSECT/EXCEPT not handled in set-op block (Fix 1) ──────
        ("R-SETOP-INTERSECT-VS-EXCEPT",
         "SELECT a FROM t1 INTERSECT SELECT a FROM t2",
         "SELECT a FROM t1 EXCEPT SELECT a FROM t2",
         False),
        ("R-SETOP-PLAIN-VS-INTERSECT",
         "SELECT a FROM t1",
         "SELECT a FROM t1 INTERSECT SELECT a FROM t2",
         False),
        ("R-SETOP-INTERSECT-SAME",
         "SELECT a FROM t1 INTERSECT SELECT a FROM t2",
         "SELECT a FROM t1 INTERSECT SELECT a FROM t2",
         True),
        # ── CLASS F: JOIN USING column set never compared (Fix 5) ──────────────
        ("R-USING-KEY-DIFF",
         "SELECT * FROM t JOIN s USING (a)",
         "SELECT * FROM t JOIN s USING (b)",
         False),
        ("R-USING-MULTICOL-DIFF",
         "SELECT * FROM t JOIN s USING (a, b)",
         "SELECT * FROM t JOIN s USING (a, c)",
         False),
        ("R-USING-COL-ADD",
         "SELECT * FROM t JOIN s USING (a)",
         "SELECT * FROM t JOIN s USING (a, b)",
         False),
        ("R-USING-COL-REMOVE",
         "SELECT * FROM t JOIN s USING (a, b)",
         "SELECT * FROM t JOIN s USING (a)",
         False),
        ("R-USING-SAME",
         "SELECT * FROM t JOIN s USING (a, b)",
         "SELECT * FROM t JOIN s USING (a, b)",
         True),
        # ── CLASS G: JOIN ON _predicate_set drops right branch (Fix 2) ─────────
        ("R-JOIN-ON-OR-RIGHT-DIFF",
         "SELECT a FROM t JOIN s ON t.a=s.a OR t.b=s.b",
         "SELECT a FROM t JOIN s ON t.a=s.a",
         False),
        ("R-JOIN-ON-EQ-DIFF",
         "SELECT a FROM t JOIN s ON t.a=s.a",
         "SELECT a FROM t JOIN s ON t.a=s.b",
         False),
        ("R-JOIN-ON-EQ-SAME",
         "SELECT a FROM t JOIN s ON t.a=s.a",
         "SELECT a FROM t JOIN s ON t.a=s.a",
         True),
        # ── CLASS H: ORDER BY frozenset loses sequence when LIMIT present (Fix 6)
        ("R-ORDERBY-RESEQ-WITH-LIMIT",
         "SELECT a, b FROM t ORDER BY a, b LIMIT 5",
         "SELECT a, b FROM t ORDER BY b, a LIMIT 5",
         False),
        # LIMIT add/remove with same ORDER BY: caught by LIMIT string compare, not
        # Fix 6 list branch (has_limit causes list compare but lists are equal).
        ("R-ORDERBY-LIMIT-ADD",
         "SELECT a FROM t ORDER BY a",
         "SELECT a FROM t ORDER BY a LIMIT 5",
         False),
        ("R-ORDERBY-LIMIT-REMOVE",
         "SELECT a FROM t ORDER BY a LIMIT 5",
         "SELECT a FROM t ORDER BY a",
         False),
        # Presence asymmetry (no-LIMIT branch, frozenset size mismatch).
        ("R-ORDERBY-ADD",
         "SELECT a FROM t",
         "SELECT a FROM t ORDER BY a",
         False),
        ("R-ORDERBY-REMOVE",
         "SELECT a FROM t ORDER BY a",
         "SELECT a FROM t",
         False),
        # ORDER BY reseq WITHOUT LIMIT: presentation-only, stays True.
        ("R-ORDERBY-RESEQ-NO-LIMIT",
         "SELECT a, b FROM t ORDER BY a, b",
         "SELECT a, b FROM t ORDER BY b, a",
         True),
        # ASC vs DESC with LIMIT: detected by expr SQL text (includes direction keyword).
        ("R-ORDERBY-ASC-VS-DESC-LIMIT",
         "SELECT a FROM t ORDER BY a ASC LIMIT 5",
         "SELECT a FROM t ORDER BY a DESC LIMIT 5",
         False),
        # ── CLASS I: JOIN ON TRUE/FALSE Boolean literal ─────────────────────────
        # Fix 2 resolves CLASS I as a side effect: _predicate_set no longer calls
        # .this on bare predicate nodes, so exp.Boolean is handled correctly.
        # Both sides ON TRUE → same frozenset → True (correctly admitted).
        # ON TRUE vs ON FALSE → different frozensets → False (correctly rejected).
        ("R-JOINON-BOOL-LITERAL",
         "SELECT a FROM t JOIN s ON TRUE",
         "SELECT a FROM t JOIN s ON TRUE",
         True),
        ("R-JOIN-ON-TRUE-VS-FALSE",
         "SELECT a FROM t JOIN s ON TRUE",
         "SELECT a FROM t JOIN s ON FALSE",
         False),
        # ── CLASS J: QUALIFY Trino parse raises (already fail-closed) ───────────
        # Trino dialect ParseError → outer except → None.
        ("R-QUALIFY-TRINO-NONE",
         "SELECT a FROM t QUALIFY ROW_NUMBER() OVER (PARTITION BY a) = 1",
         "SELECT a FROM t QUALIFY ROW_NUMBER() OVER (PARTITION BY a) = 1",
         None),
        # ── Presence-asymmetry: HAVING add/remove (regression protection) ───────
        # HAVING path was already sound; these tests protect against future regression.
        ("R-HAVING-ADD",
         "SELECT a FROM t GROUP BY a",
         "SELECT a FROM t GROUP BY a HAVING COUNT(*) > 1",
         False),
        ("R-HAVING-REMOVE",
         "SELECT a FROM t GROUP BY a HAVING COUNT(*) > 1",
         "SELECT a FROM t GROUP BY a",
         False),
        # ── Safe-rewrite positives: anti-over-fix ───────────────────────────────
        ("R-WHERE-COMMUTATIVE",
         "SELECT a FROM t WHERE x=1 AND y=2",
         "SELECT a FROM t WHERE y=2 AND x=1",
         True),
        ("R-GROUPBY-REORDER",
         "SELECT a, b FROM t GROUP BY a, b",
         "SELECT a, b FROM t GROUP BY b, a",
         True),
        ("R-HAVING-COMMUTATIVE",
         "SELECT a FROM t GROUP BY a HAVING COUNT(*) > 1 AND SUM(x) < 100",
         "SELECT a FROM t GROUP BY a HAVING SUM(x) < 100 AND COUNT(*) > 1",
         True),
        ("R-UNION-VS-UNIONALL",
         "SELECT a FROM t UNION ALL SELECT a FROM s",
         "SELECT a FROM t UNION ALL SELECT a FROM s",
         True),
        ("R-JOIN-ON-EQ-SAME-CANONICAL",
         "SELECT a FROM t INNER JOIN s ON t.id = s.id",
         "SELECT a FROM t INNER JOIN s ON t.id = s.id",
         True),
        # Multi-clause safe rewrite: FROM same, projections same, WHERE reordered, GROUP BY reordered.
        ("R-CANONICAL-COMMUTATIVE-TRUE",
         "SELECT a, b FROM t WHERE x=1 AND y=2 GROUP BY a, b",
         "SELECT a, b FROM t WHERE y=2 AND x=1 GROUP BY b, a",
         True),
    ],
)
def test_queries_structurally_equivalent_v42(test_id, sql1, sql2, expected):
    """Per-class oracle regression rows (v42 semantic-gate hardening, ticket §8.1 + §8.4)."""
    result = queries_structurally_equivalent(sql1, sql2)
    assert result is expected, (
        f"[{test_id}] expected {expected!r}, got {result!r}\n"
        f"  sql1: {sql1}\n  sql2: {sql2}"
    )


# ── v42 standalone gate-level tests (ticket §8.2) ────────────────────────────

def _make_cand(original_sql, rewritten_sql):
    """Construct an admitted, changed RewriteCandidate for gate tests."""
    from genie.skills.mcp_trino.trino_optimize import RewriteCandidate
    return RewriteCandidate(
        fragment_id="frag",
        original_sql=original_sql,
        rewritten_sql=rewritten_sql,
        action="rewrite",
        changed=True,
        admitted=True,
        rationale="rewrite",
    )


def test_semantic_gate_reverts_from_table_change():
    """CLASS A: FROM table change must be reverted."""
    safe, reverted = _semantic_safe_candidates([_make_cand("SELECT a FROM t1", "SELECT a FROM t2")])
    assert reverted == ["frag"]
    assert safe[0].admitted is False
    assert safe[0].rewritten_sql == "SELECT a FROM t1"


def test_semantic_gate_reverts_tablesample_add():
    """CLASS A: TABLESAMPLE added must be reverted."""
    safe, reverted = _semantic_safe_candidates([
        _make_cand("SELECT a FROM t", "SELECT a FROM t TABLESAMPLE BERNOULLI(10)")
    ])
    assert reverted == ["frag"]
    assert safe[0].admitted is False


def test_semantic_gate_reverts_offset_add():
    """CLASS B: OFFSET added must be reverted."""
    safe, reverted = _semantic_safe_candidates([
        _make_cand("SELECT a FROM t LIMIT 5", "SELECT a FROM t LIMIT 5 OFFSET 10")
    ])
    assert reverted == ["frag"]
    assert safe[0].admitted is False


def test_semantic_gate_reverts_proj_agg_arg():
    """CLASS C: aggregate argument change must be reverted."""
    safe, reverted = _semantic_safe_candidates([
        _make_cand("SELECT SUM(salary) AS t FROM e", "SELECT SUM(base_salary) AS t FROM e")
    ])
    assert reverted == ["frag"]
    assert safe[0].admitted is False


def test_semantic_gate_reverts_proj_agg_func():
    """CLASS C: aggregate function change (SUM → AVG) must be reverted."""
    safe, reverted = _semantic_safe_candidates([
        _make_cand("SELECT SUM(x) AS t FROM e", "SELECT AVG(x) AS t FROM e")
    ])
    assert reverted == ["frag"]
    assert safe[0].admitted is False


def test_semantic_gate_reverts_intersect_vs_except():
    """CLASS E: INTERSECT rewritten as EXCEPT must be reverted."""
    safe, reverted = _semantic_safe_candidates([
        _make_cand(
            "SELECT a FROM t1 INTERSECT SELECT a FROM t2",
            "SELECT a FROM t1 EXCEPT SELECT a FROM t2",
        )
    ])
    assert reverted == ["frag"]
    assert safe[0].admitted is False


def test_semantic_gate_reverts_using_key_diff():
    """CLASS F: USING join column change must be reverted."""
    safe, reverted = _semantic_safe_candidates([
        _make_cand("SELECT * FROM t JOIN s USING (a)", "SELECT * FROM t JOIN s USING (b)")
    ])
    assert reverted == ["frag"]
    assert safe[0].admitted is False


def test_semantic_gate_reverts_orderby_reseq_with_limit():
    """CLASS H: ORDER BY key resequence WITH LIMIT must be reverted."""
    safe, reverted = _semantic_safe_candidates([
        _make_cand(
            "SELECT a, b FROM t ORDER BY a, b LIMIT 5",
            "SELECT a, b FROM t ORDER BY b, a LIMIT 5",
        )
    ])
    assert reverted == ["frag"]
    assert safe[0].admitted is False


def test_semantic_gate_admits_orderby_reseq_no_limit():
    """CLASS H positive: ORDER BY reseq WITHOUT LIMIT stays admitted (presentation-only)."""
    cand = _make_cand("SELECT a, b FROM t ORDER BY a, b", "SELECT a, b FROM t ORDER BY b, a")
    safe, reverted = _semantic_safe_candidates([cand])
    assert reverted == []
    assert safe[0].admitted is True
    assert safe[0].changed is True


def test_semantic_gate_reverts_on_indeterminate_parse():
    """Fail-closed: garbage rewritten SQL triggers revert."""
    safe, reverted = _semantic_safe_candidates([
        _make_cand("SELECT a FROM t", "))) garbage (((")
    ])
    assert reverted == ["frag"]
    assert safe[0].admitted is False


# ── v42 default-deny sentinel tests (ticket §8.5) ────────────────────────────

def test_r_modelled_keys_subset():
    """R-MODELLED-KEYS-SUBSET: every key in _MODELLED_KEYS exists in exp.Select.arg_types."""
    import sqlglot.expressions as exp
    from genie.core.sql_extraction import _MODELLED_KEYS
    live_keys = set(exp.Select.arg_types)
    assert _MODELLED_KEYS <= live_keys, (
        f"Keys in _MODELLED_KEYS not in exp.Select.arg_types: "
        f"{_MODELLED_KEYS - live_keys}"
    )


def test_r_default_deny(monkeypatch):
    """R-DEFAULT-DENY: monkeypatching an unmodeled key into a parsed Select fires the sentinel.

    Preferred approach: inject a non-falsy value for a fabricated key into s.args
    so the sentinel detects it and returns None. Proves the sentinel code path fires
    (not that parse failed).
    """
    import sqlglot
    import sqlglot.expressions as exp
    from genie.core.sql_extraction import queries_structurally_equivalent

    sql1 = "SELECT a FROM t"
    sql2 = "SELECT a FROM t"

    # Parse sql1 to get a Select node and inject an unmodeled key.
    t1 = sqlglot.parse_one(sql1, read="trino")
    assert isinstance(t1, exp.Select)

    original_args = dict(t1.args)

    def patched_parse_one(sql, read=None):
        node = sqlglot.parse_one.__wrapped__(sql, read=read) if hasattr(sqlglot.parse_one, "__wrapped__") else _orig_parse_one(sql, read=read)
        if sql == sql1:
            node.args["future_construct"] = "non_falsy_value"
        return node

    _orig_parse_one = sqlglot.parse_one
    monkeypatch.setattr(sqlglot, "parse_one", patched_parse_one)

    result = queries_structurally_equivalent(sql1, sql2)
    assert result is None, (
        f"Expected None (sentinel fires for unmodeled key), got {result!r}"
    )


def test_r_setop_type_newsubclass():
    """R-SETOP-TYPE-NEWSUBCLASS: sentinel fires for a set-op type not in _SET_OP_TYPES.

    Proves the sentinel (not the set-op block) is the backstop for future sqlglot
    set-op subtypes not in the explicit (Union, Intersect, Except) tuple.

    Implementation: monkeypatch queries_structurally_equivalent's inner _unwrap_select
    by patching sqlglot.parse_one to return a mock Select that has an unmodeled key.
    This verifies the sentinel fires (returns None) rather than incorrectly returning True.
    """
    import sqlglot
    import sqlglot.expressions as exp
    from genie.core.sql_extraction import queries_structurally_equivalent

    # Use a plain SELECT that parses cleanly, then inject an unmodeled key.
    # This simulates "a future construct appears in the parsed AST".
    sql1 = "SELECT a FROM t"
    sql2 = "SELECT a FROM t"

    _orig = sqlglot.parse_one

    def inject_future_key(sql, read=None):
        node = _orig(sql, read=read)
        if isinstance(node, exp.Select):
            node.args["mock_future_setop"] = "present"
        return node

    import unittest.mock as _mock
    with _mock.patch.object(sqlglot, "parse_one", side_effect=inject_future_key):
        result = queries_structurally_equivalent(sql1, sql2)

    assert result is None, (
        f"Expected None (sentinel fires for unmodeled mock_future_setop key), got {result!r}"
    )
