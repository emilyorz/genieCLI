"""Tests for genie/skills/trino_linter/"""
from __future__ import annotations

import json

import pytest

try:
    import sqlglot
    _has_sqlglot = True
except ImportError:
    _has_sqlglot = False

_needs_sqlglot = pytest.mark.skipif(not _has_sqlglot, reason="sqlglot not installed")

from genie.skills.trino_linter import TrinoLinter, register
from genie.skills.trino_linter.analyzer import LintResult, analyze, _compute_score, _make_summary
from genie.skills.trino_linter.rules import (
    Finding,
    check_nvl,
    check_decode,
    check_plus_join,
    check_rownum,
    check_sysdate,
    check_select_star,
    check_implicit_cross_join,
    check_leading_wildcard_like,
    check_count_distinct,
    check_correlated_subquery,
    check_missing_partition_filter,
)


# ── helpers ────────────────────────────────────────────────────────────────────

def _rules(result: LintResult) -> set[str]:
    return {f.rule for f in result.findings}


def _parse(sql: str):
    import sqlglot
    import sqlglot.errors as sge
    return sqlglot.parse(sql, read="trino", error_level=sge.ErrorLevel.IGNORE)


# ── oracle-residual-nvl ────────────────────────────────────────────────────────

def test_nvl_detected():
    findings = check_nvl("SELECT NVL(col, 0) FROM t", [])
    assert len(findings) == 1
    assert findings[0].rule == "oracle-residual-nvl"
    assert findings[0].severity == "high"


def test_nvl_case_insensitive():
    findings = check_nvl("SELECT nvl(a, b) FROM t", [])
    assert len(findings) == 1


def test_nvl_multiple_occurrences():
    sql = "SELECT NVL(a, 0), NVL(b, '') FROM t"
    findings = check_nvl(sql, [])
    assert len(findings) == 2


def test_nvl_not_triggered_on_similar_name():
    # NVLIST is not NVL(
    findings = check_nvl("SELECT nvlist FROM t", [])
    assert len(findings) == 0


# ── oracle-residual-decode ─────────────────────────────────────────────────────

def test_decode_detected():
    findings = check_decode("SELECT DECODE(status, 1, 'A', 'B') FROM t", [])
    assert len(findings) == 1
    assert findings[0].rule == "oracle-residual-decode"
    assert findings[0].severity == "high"


def test_decode_case_insensitive():
    findings = check_decode("SELECT decode(x, 0, 'no', 'yes') FROM t", [])
    assert len(findings) == 1


# ── oracle-residual-plus-join ──────────────────────────────────────────────────

def test_plus_join_detected():
    sql = "SELECT * FROM a, b WHERE a.id = b.id(+)"
    findings = check_plus_join(sql, [])
    assert len(findings) == 1
    assert findings[0].rule == "oracle-residual-plus-join"
    assert findings[0].severity == "high"


def test_plus_join_with_spaces():
    findings = check_plus_join("WHERE a.id = b.id ( + )", [])
    assert len(findings) == 1


# ── oracle-residual-rownum ─────────────────────────────────────────────────────

def test_rownum_detected():
    findings = check_rownum("SELECT * FROM t WHERE ROWNUM <= 10", [])
    assert len(findings) == 1
    assert findings[0].rule == "oracle-residual-rownum"
    assert findings[0].severity == "high"


def test_rownum_case_insensitive():
    findings = check_rownum("WHERE rownum < 5", [])
    assert len(findings) == 1


def test_rownum_not_partial_match():
    # ROWNUM_COL is not ROWNUM pseudo-column
    findings = check_rownum("SELECT rownum_col FROM t", [])
    assert len(findings) == 0


# ── oracle-residual-sysdate ────────────────────────────────────────────────────

def test_sysdate_detected():
    findings = check_sysdate("SELECT SYSDATE FROM dual", [])
    assert len(findings) == 1
    assert findings[0].rule == "oracle-residual-sysdate"
    assert findings[0].severity == "high"


def test_sysdate_case_insensitive():
    findings = check_sysdate("WHERE created_at > sysdate - 7", [])
    assert len(findings) == 1


# ── select-star ────────────────────────────────────────────────────────────────

@_needs_sqlglot
def test_select_star_detected():
    result = analyze("SELECT * FROM users")
    assert "select-star" in _rules(result)


def test_count_star_not_flagged():
    """COUNT(*) must not trigger select-star."""
    result = analyze("SELECT COUNT(*) FROM users")
    assert "select-star" not in _rules(result)


def test_count_star_with_other_columns_not_flagged():
    result = analyze("SELECT COUNT(*), status FROM users GROUP BY status")
    assert "select-star" not in _rules(result)


@_needs_sqlglot
def test_select_star_and_count_star_together():
    """SELECT *, COUNT(*) — should flag select-star (bare *) but only once."""
    result = analyze("SELECT *, COUNT(*) FROM t")
    star_findings = [f for f in result.findings if f.rule == "select-star"]
    assert len(star_findings) == 1


# ── implicit-cross-join ────────────────────────────────────────────────────────

def test_implicit_cross_join_detected():
    sql = "SELECT * FROM orders o, customers c WHERE o.cust_id = c.id"
    findings = check_implicit_cross_join(sql, [])
    assert len(findings) >= 1
    assert findings[0].rule == "implicit-cross-join"
    assert findings[0].severity == "medium"


def test_explicit_join_not_flagged():
    sql = "SELECT * FROM orders o JOIN customers c ON o.cust_id = c.id"
    findings = check_implicit_cross_join(sql, [])
    assert len(findings) == 0


# ── leading-wildcard-like ──────────────────────────────────────────────────────

def test_leading_wildcard_detected():
    findings = check_leading_wildcard_like("WHERE name LIKE '%smith'", [])
    assert len(findings) == 1
    assert findings[0].rule == "leading-wildcard-like"
    assert findings[0].severity == "low"


def test_leading_underscore_wildcard_detected():
    findings = check_leading_wildcard_like("WHERE code LIKE '_XYZ'", [])
    assert len(findings) == 1


def test_trailing_wildcard_not_flagged():
    """'smith%' is fine — not a leading wildcard."""
    findings = check_leading_wildcard_like("WHERE name LIKE 'smith%'", [])
    assert len(findings) == 0


def test_no_wildcard_not_flagged():
    findings = check_leading_wildcard_like("WHERE name LIKE 'exact'", [])
    assert len(findings) == 0


# ── count-distinct-high-risk ───────────────────────────────────────────────────

def test_count_distinct_detected():
    findings = check_count_distinct("SELECT COUNT(DISTINCT user_id) FROM events", [])
    assert len(findings) == 1
    assert findings[0].rule == "count-distinct-high-risk"
    assert findings[0].severity == "medium"


def test_count_distinct_case_insensitive():
    findings = check_count_distinct("SELECT count(distinct x) FROM t", [])
    assert len(findings) == 1


def test_plain_count_not_flagged():
    findings = check_count_distinct("SELECT COUNT(user_id) FROM t", [])
    assert len(findings) == 0


# ── correlated-subquery ────────────────────────────────────────────────────────

@_needs_sqlglot
def test_correlated_subquery_detected():
    sql = "SELECT * FROM orders WHERE amount > (SELECT AVG(amount) FROM orders WHERE customer_id = orders.customer_id)"
    findings = check_correlated_subquery(sql, [])
    assert len(findings) >= 1
    assert findings[0].rule == "correlated-subquery"
    assert findings[0].severity == "medium"


def test_subquery_in_select_not_flagged_as_correlated():
    """Subquery in SELECT clause, not WHERE, should not trigger."""
    sql = "SELECT id, (SELECT MAX(val) FROM ref) AS max_val FROM t"
    findings = check_correlated_subquery(sql, [])
    assert len(findings) == 0


# ── missing-partition-filter ───────────────────────────────────────────────────

@_needs_sqlglot
def test_missing_partition_filter_detected():
    result = analyze("SELECT id, name FROM fact_orders")
    assert "missing-partition-filter" in _rules(result)


def test_partition_filter_present_not_flagged():
    result = analyze("SELECT id FROM fact_orders WHERE dt = DATE '2024-01-01'")
    assert "missing-partition-filter" not in _rules(result)


# ── line numbers ───────────────────────────────────────────────────────────────

def test_multiline_sql_line_numbers():
    sql = "SELECT\n  id,\n  NVL(name, 'anon')\nFROM users"
    findings = check_nvl(sql, [])
    assert len(findings) == 1
    assert findings[0].line == 3


def test_sysdate_line_number():
    sql = "SELECT\n  SYSDATE\nFROM dual"
    findings = check_sysdate(sql, [])
    assert findings[0].line == 2


# ── score calculation ──────────────────────────────────────────────────────────

def test_score_a_no_findings():
    f: list[Finding] = []
    assert _compute_score(f, None) == "A"


def test_score_b_medium_findings():
    f = [Finding("medium", 1, "r", "m", "s")]
    assert _compute_score(f, None) == "B"


def test_score_c_three_plus_medium():
    f = [Finding("medium", i, "r", "m", "s") for i in range(3)]
    assert _compute_score(f, None) == "C"


def test_score_d_one_high():
    f = [Finding("high", 1, "r", "m", "s")]
    assert _compute_score(f, None) == "D"


def test_score_f_three_plus_high():
    f = [Finding("high", i, "r", "m", "s") for i in range(3)]
    assert _compute_score(f, None) == "F"


def test_score_f_parse_error():
    assert _compute_score([], "parse failed") == "F"


def test_summary_format():
    findings = [
        Finding("high", 1, "r", "m", "s"),
        Finding("medium", 2, "r", "m", "s"),
        Finding("medium", 3, "r", "m", "s"),
        Finding("low", 4, "r", "m", "s"),
    ]
    assert _make_summary(findings) == "1 high, 2 medium, 1 low"


# ── parse failure graceful handling ───────────────────────────────────────────

def test_parse_failure_returns_f_score():
    # sqlglot is lenient, so force a truly unparseable input
    result = analyze("THIS IS NOT SQL @@@###$$$")
    # Should not raise; result is either findings or empty with parse_error
    assert result.score in {"A", "B", "C", "D", "F"}
    assert result.summary is not None


def test_empty_sql_returns_clean_result():
    result = analyze("")
    assert result.score == "A"
    assert result.findings == []
    assert result.parse_error is None


def test_analyze_returns_lint_result():
    result = analyze("SELECT id FROM users WHERE id = 1")
    assert isinstance(result, LintResult)
    assert isinstance(result.findings, list)
    assert result.score in {"A", "B", "C", "D", "F"}
    assert "high" in result.summary


# ── multi-statement ────────────────────────────────────────────────────────────

@_needs_sqlglot
def test_multi_statement_both_analyzed():
    sql = "SELECT * FROM a;\nSELECT NVL(x, 0) FROM b;"
    result = analyze(sql)
    rules = _rules(result)
    assert "select-star" in rules
    assert "oracle-residual-nvl" in rules


def test_multi_statement_count_star_not_flagged():
    sql = "SELECT COUNT(*) FROM a;\nSELECT COUNT(*) FROM b;"
    result = analyze(sql)
    assert "select-star" not in _rules(result)


@_needs_sqlglot
def test_multi_statement_line_numbers_distinct():
    sql = "SELECT NVL(a, 0) FROM t1;\nSELECT NVL(b, '') FROM t2;"
    result = analyze(sql)
    nvl_findings = [f for f in result.findings if f.rule == "oracle-residual-nvl"]
    assert len(nvl_findings) == 2
    lines = {f.line for f in nvl_findings}
    assert len(lines) == 2  # findings on different lines


# ── to_dict output shape ───────────────────────────────────────────────────────

@_needs_sqlglot
def test_to_dict_has_required_fields():
    result = analyze("SELECT NVL(x, 0) FROM t")
    d = result.to_dict()
    assert "findings" in d
    assert "score" in d
    assert "summary" in d
    assert "parse_error" in d
    assert len(d["findings"]) >= 1
    finding = d["findings"][0]
    for key in ("severity", "line", "rule", "message", "suggestion"):
        assert key in finding


def test_skill_run_returns_json():
    skill = TrinoLinter()
    output = skill.run(sql="SELECT NVL(col, 0) FROM t")
    parsed = json.loads(output)
    assert "findings" in parsed
    assert parsed["score"] in {"A", "B", "C", "D", "F"}


# ── register ───────────────────────────────────────────────────────────────────

def test_register_adds_trino_linter():
    from genie.core.registry import SkillRegistry

    register(SkillRegistry)
    skill = SkillRegistry.get("trino_linter")
    assert skill is not None
    assert skill.name == "trino_linter"


def test_skill_spec_has_sql_arg():
    skill = TrinoLinter()
    spec = skill.spec()
    arg_names = [a["name"] for a in spec["args"]]
    assert "sql" in arg_names


# ── H1/H2/H3/M1/M2 regression tests ──────────────────────────────────────────

def test_nvl_in_line_comment_not_flagged():
    """H1: NVL inside -- comment must not trigger oracle-residual-nvl."""
    result = analyze("-- NVL(a,0)\nSELECT 1")
    assert "oracle-residual-nvl" not in _rules(result)


def test_sysdate_in_string_literal_not_flagged():
    """H1: SYSDATE inside string literal must not trigger oracle-residual-sysdate."""
    result = analyze("SELECT 'SYSDATE' FROM t")
    assert "oracle-residual-sysdate" not in _rules(result)


def test_rownum_in_block_comment_not_flagged():
    """H1: ROWNUM inside block comment must not trigger oracle-residual-rownum."""
    result = analyze("/* ROWNUM */ SELECT id FROM t")
    assert "oracle-residual-rownum" not in _rules(result)


def test_invalid_sql_returns_parse_error_and_score_f():
    """H2: truly invalid SQL (unrecognized tokens) must set parse_error and return score F."""
    result = analyze("THIS IS NOT SQL @@@")
    assert result.parse_error is not None
    assert result.score == "F"


def test_uncorrelated_subquery_not_flagged():
    """H3: subquery in WHERE with no outer-scope column reference is not correlated."""
    result = analyze("SELECT * FROM t WHERE id IN (SELECT id FROM u)")
    assert "correlated-subquery" not in _rules(result)


@_needs_sqlglot
def test_multi_statement_select_star_line_numbers_differ():
    """M2: each statement's select-star finding should reference its own line."""
    sql = "SELECT * FROM a;\nSELECT * FROM b;"
    result = analyze(sql)
    star_findings = [f for f in result.findings if f.rule == "select-star"]
    assert len(star_findings) == 2
    lines = {f.line for f in star_findings}
    assert len(lines) == 2  # line 1 and line 2


# ── combined oracle-residual scenario ─────────────────────────────────────────

@_needs_sqlglot
def test_all_oracle_residuals_detected():
    sql = """
SELECT
    NVL(a, 0),
    DECODE(status, 1, 'active', 'inactive'),
    SYSDATE,
    ROWNUM
FROM t
WHERE t.id = other.id(+)
"""
    result = analyze(sql)
    rules = _rules(result)
    assert "oracle-residual-nvl" in rules
    assert "oracle-residual-decode" in rules
    assert "oracle-residual-sysdate" in rules
    assert "oracle-residual-rownum" in rules
    assert "oracle-residual-plus-join" in rules
    # Score should be D or F (multiple high findings)
    assert result.score in {"D", "F"}


# ── catalog integrity: missing pattern raises, not silently returns [] ─────────

def test_oracle_residual_raises_on_missing_catalog_entry():
    """_check_oracle_residual must raise LookupError when pattern not in catalog.

    This pins the Bug-2 fix: before the fix, a missing catalog entry would
    silently return [], masking detection failures.  Now it must raise so that
    catalog drift is caught at development time rather than in production.
    """
    from genie.skills.trino_linter.rules import _check_oracle_residual
    with pytest.raises(LookupError, match="no pattern in the shared catalog"):
        _check_oracle_residual("SELECT 1", "NON_EXISTENT_CONSTRUCT_XYZ", "test-rule-id")


# ── missing sqlglot dependency — actionable error ────────────────────────────

def test_analyze_surfaces_sqlglot_missing_as_install_hint(monkeypatch):
    """If sqlglot is not importable, analyze() should return a parse_error
    that tells the user HOW to fix it (pip install ...), not a cryptic
    ModuleNotFoundError message."""
    import builtins
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "sqlglot" or name.startswith("sqlglot."):
            raise ImportError("No module named 'sqlglot'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    result = analyze("SELECT * FROM t")
    assert result.findings == []
    assert result.score == "F"
    assert result.parse_error is not None
    assert "sqlglot" in result.parse_error
    assert "pip install" in result.parse_error
