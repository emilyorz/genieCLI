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
    run_write_analysis_only,
)
from genie.skills.mcp_trino.trino_optimize import decompose, optimize
from genie.core.sql_extraction import (
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


# ── output-column extraction helper ───────────────────────────────────────────

@pytest.mark.parametrize(
    ("sql", "expected"),
    [
        ("SELECT a, b FROM t", ("a", "b")),
        ("SELECT a AS x, b FROM t", ("x", "b")),
        ("WITH c AS (SELECT 1) SELECT p, q FROM c", ("p", "q")),
        ("SELECT * FROM t", None),            # indeterminable
        ("))) not sql (((", None),            # unparseable
        ("", None),
    ],
)
def test_query_output_columns(sql, expected):
    assert query_output_columns(sql) == expected


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
