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
