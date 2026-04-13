"""Tests for Phase 2: oracle2trino structured output + shared pattern catalog."""
from __future__ import annotations

import json

import pytest

try:
    import sqlglot
    _has_sqlglot = True
except ImportError:
    _has_sqlglot = False

from genie.skills.oracle2trino import AnalyzeOracleSP, TranspileSQL
from genie.skills.oracle2trino.models import ConversionResult, UnsupportedConstruct
from genie.core.sql_patterns import (
    ORACLE_CONSTRUCTS,
    compute_confidence,
    get_construct_meta,
)


# ── helpers ────────────────────────────────────────────────────────────────────

def _run_transpile(sql: str) -> dict:
    return json.loads(TranspileSQL().run(sql))


def _run_analyze(sql: str, connector: str = "iceberg") -> dict:
    return json.loads(AnalyzeOracleSP().run(sql, connector))


def _constructs(result: dict) -> set[str]:
    return {u["construct"] for u in result["unsupported"]}


# ── transpile_sql: JSON shape ──────────────────────────────────────────────────

def test_transpile_sql_returns_valid_json():
    raw = TranspileSQL().run("SELECT id FROM t WHERE id = 1")
    parsed = json.loads(raw)  # must not raise
    assert isinstance(parsed, dict)


def test_transpile_sql_has_five_core_fields():
    result = _run_transpile("SELECT id FROM t WHERE id = 1")
    assert {"converted_sql", "unsupported", "warnings", "confidence", "manual_fix_notes"} <= result.keys()


def test_transpile_sql_field_types():
    result = _run_transpile("SELECT id FROM t WHERE id = 1")
    assert isinstance(result["converted_sql"], str)
    assert isinstance(result["unsupported"], list)
    assert isinstance(result["warnings"], list)
    assert isinstance(result["confidence"], float)
    assert isinstance(result["manual_fix_notes"], list)


@pytest.mark.skipif(not _has_sqlglot, reason="sqlglot not installed")
def test_transpile_clean_sql_high_confidence():
    result = _run_transpile("SELECT id, name FROM users WHERE id = 1")
    assert result["confidence"] == 1.0
    assert result["unsupported"] == []


# ── transpile_sql: unsupported construct detection ────────────────────────────

def test_rownum_detected_in_transpile():
    result = _run_transpile("SELECT * FROM t WHERE ROWNUM <= 10")
    assert "ROWNUM" in _constructs(result)


def test_connect_by_detected_in_transpile():
    sql = "SELECT id FROM t START WITH parent_id IS NULL CONNECT BY PRIOR id = parent_id"
    result = _run_transpile(sql)
    assert "CONNECT BY" in _constructs(result)


def test_listagg_detected_in_transpile():
    sql = "SELECT LISTAGG(name, ',') WITHIN GROUP (ORDER BY name) FROM t GROUP BY dept"
    result = _run_transpile(sql)
    assert "LISTAGG" in _constructs(result)


def test_execute_immediate_detected_in_transpile():
    sql = "BEGIN EXECUTE IMMEDIATE 'DROP TABLE t'; END;"
    result = _run_transpile(sql)
    assert "EXECUTE IMMEDIATE" in _constructs(result)


def test_unsupported_construct_has_required_fields():
    result = _run_transpile("SELECT * FROM t WHERE ROWNUM < 5")
    items = [u for u in result["unsupported"] if u["construct"] == "ROWNUM"]
    assert len(items) == 1
    item = items[0]
    assert {"construct", "severity", "message", "suggestion"} <= item.keys()
    assert item["severity"] in {"high", "medium", "low"}
    assert item["message"]
    assert item["suggestion"]


def test_no_duplicate_constructs_in_unsupported():
    # Multiple ROWNUM occurrences must not produce duplicate entries
    sql = "SELECT * FROM (SELECT ROWNUM rn FROM t) WHERE ROWNUM < 5"
    result = _run_transpile(sql)
    rownum_entries = [u for u in result["unsupported"] if u["construct"] == "ROWNUM"]
    assert len(rownum_entries) == 1


# ── transpile_sql: confidence calculation ─────────────────────────────────────

@pytest.mark.skipif(not _has_sqlglot, reason="sqlglot not installed")
def test_confidence_decreases_with_high_severity():
    clean = _run_transpile("SELECT id FROM t WHERE id = 1")
    heavy = _run_transpile("SELECT * FROM t WHERE ROWNUM < 10 CONNECT BY PRIOR id = parent_id")
    assert heavy["confidence"] < clean["confidence"]


def test_confidence_rownum_high_severity_deducts_03():
    # ROWNUM is high → 1.0 - 0.30 = 0.70 (assuming no other constructs survive transpile)
    result = _run_transpile("SELECT * FROM t WHERE ROWNUM <= 1")
    rownum_items = [u for u in result["unsupported"] if u["construct"] == "ROWNUM"]
    if rownum_items:  # only assert if ROWNUM wasn't fixed by sqlglot
        assert result["confidence"] <= 0.70


def test_confidence_never_negative():
    # Many high-severity constructs → confidence clamped at 0.0
    sql = (
        "BEGIN DECLARE v NUMBER; CURSOR c IS SELECT * FROM t; "
        "BEGIN FOR r IN c LOOP EXECUTE IMMEDIATE 'x'; END LOOP; "
        "EXCEPTION WHEN OTHERS THEN NULL; END; END;"
    )
    result = _run_transpile(sql)
    assert result["confidence"] >= 0.0


def test_confidence_clamped_to_zero_on_sqlglot_failure():
    # This is hard to trigger reliably; test compute_confidence directly instead
    unsupported = [
        UnsupportedConstruct("A", "high", "m", "s"),
        UnsupportedConstruct("B", "high", "m", "s"),
        UnsupportedConstruct("C", "high", "m", "s"),
        UnsupportedConstruct("D", "high", "m", "s"),
    ]
    assert compute_confidence(unsupported) == 0.0


# ── analyze_oracle_sp: JSON shape + manual_fix_notes ─────────────────────────

PLSQL_BLOCK = (
    "CREATE OR REPLACE PROCEDURE update_salaries AS\n"
    "  CURSOR emp_cur IS SELECT id, salary FROM employees;\n"
    "  v_new_sal NUMBER;\n"
    "BEGIN\n"
    "  FOR rec IN emp_cur LOOP\n"
    "    v_new_sal := rec.salary * 1.1;\n"
    "    EXECUTE IMMEDIATE 'UPDATE employees SET salary = ' || v_new_sal;\n"
    "  END LOOP;\n"
    "EXCEPTION\n"
    "  WHEN OTHERS THEN NULL;\n"
    "END;"
)


def test_analyze_sp_returns_valid_json():
    raw = AnalyzeOracleSP().run(PLSQL_BLOCK)
    parsed = json.loads(raw)
    assert isinstance(parsed, dict)


def test_analyze_sp_has_five_core_fields():
    result = _run_analyze(PLSQL_BLOCK)
    assert {"converted_sql", "unsupported", "warnings", "confidence", "manual_fix_notes"} <= result.keys()


def test_analyze_sp_detects_plsql_constructs():
    result = _run_analyze(PLSQL_BLOCK)
    found = _constructs(result)
    # The SP contains BEGIN, CURSOR, FOR...IN, LOOP, EXECUTE IMMEDIATE, EXCEPTION
    assert found & {"BEGIN", "CURSOR", "EXECUTE IMMEDIATE", "EXCEPTION"}


def test_analyze_sp_has_manual_fix_notes_for_plsql():
    result = _run_analyze(PLSQL_BLOCK)
    assert len(result["manual_fix_notes"]) > 0


def test_analyze_sp_manual_fix_notes_are_strings():
    result = _run_analyze(PLSQL_BLOCK)
    for note in result["manual_fix_notes"]:
        assert isinstance(note, str)
        assert note.strip()


def test_analyze_sp_connector_note_in_warnings():
    result = _run_analyze("SELECT * FROM t", connector="hive")
    connector_warnings = [w for w in result["warnings"] if "hive" in w.lower()]
    assert len(connector_warnings) >= 1


def test_analyze_sp_low_confidence_for_heavy_plsql():
    result = _run_analyze(PLSQL_BLOCK)
    assert result["confidence"] < 1.0


@pytest.mark.skipif(not _has_sqlglot, reason="sqlglot not installed")
def test_analyze_sp_clean_sql_no_manual_notes():
    result = _run_analyze("SELECT id, name FROM users WHERE id = 1")
    assert result["manual_fix_notes"] == []


# ── shared catalog: pattern coverage and linter alignment ─────────────────────

def test_catalog_has_entries():
    assert len(ORACLE_CONSTRUCTS) > 0


def test_catalog_constructs_have_required_fields():
    required = {"pattern", "construct", "severity", "message", "suggestion", "linter_rule", "plsql"}
    for entry in ORACLE_CONSTRUCTS:
        assert required <= entry.keys(), f"Missing fields in entry: {entry.get('construct')}"


def test_catalog_severity_values_are_valid():
    valid = {"high", "medium", "low"}
    for entry in ORACLE_CONSTRUCTS:
        assert entry["severity"] in valid, f"Bad severity: {entry}"


def test_catalog_contains_key_constructs():
    names = {c["construct"] for c in ORACLE_CONSTRUCTS}
    for expected in ("ROWNUM", "NVL", "DECODE", "(+)", "SYSDATE", "LISTAGG", "CONNECT BY", "EXECUTE IMMEDIATE"):
        assert expected in names, f"Missing construct: {expected}"


def test_catalog_linter_rule_alignment():
    """Each linter rule that declares a linter_rule must align with a known rule ID."""
    expected_linter_rules = {
        "oracle-residual-rownum",
        "oracle-residual-nvl",
        "oracle-residual-decode",
        "oracle-residual-plus-join",
        "oracle-residual-sysdate",
    }
    catalog_linter_rules = {
        c["linter_rule"] for c in ORACLE_CONSTRUCTS if c["linter_rule"] is not None
    }
    assert expected_linter_rules <= catalog_linter_rules


def test_get_construct_meta_returns_entry():
    meta = get_construct_meta("ROWNUM")
    assert meta is not None
    assert meta["construct"] == "ROWNUM"
    assert meta["severity"] == "high"


def test_get_construct_meta_returns_none_for_unknown():
    assert get_construct_meta("NONEXISTENT_FUNCTION_XYZ") is None


def test_lint_rules_import_catalog():
    """Verify lint rules (now in genie/core/) import from the shared catalog."""
    from genie.core.lint_rules import check_nvl, check_rownum, check_sysdate

    # Check that lint findings use catalog metadata
    nvl_findings = check_nvl("SELECT NVL(a, 0) FROM t", [])
    catalog_nvl = get_construct_meta("NVL")
    assert nvl_findings[0].message == catalog_nvl["message"]
    assert nvl_findings[0].suggestion == catalog_nvl["suggestion"]

    rownum_findings = check_rownum("SELECT * FROM t WHERE ROWNUM < 5", [])
    catalog_rownum = get_construct_meta("ROWNUM")
    assert rownum_findings[0].message == catalog_rownum["message"]


def test_both_linter_and_converter_use_catalog():
    """Both lint engine and converter must import from the shared catalog."""
    import genie.core.lint_rules as lint_rules
    import genie.skills.oracle2trino as converter

    # The lint module must reference the catalog import
    assert hasattr(lint_rules, "get_construct_meta")

    # The converter __init__ must reference ORACLE_CONSTRUCTS
    assert hasattr(converter, "ORACLE_CONSTRUCTS") or "ORACLE_CONSTRUCTS" in dir(converter)


# ── compute_confidence unit tests ─────────────────────────────────────────────

def test_compute_confidence_empty():
    assert compute_confidence([]) == 1.0


def test_compute_confidence_single_high():
    items = [UnsupportedConstruct("X", "high", "m", "s")]
    assert abs(compute_confidence(items) - 0.70) < 1e-9


def test_compute_confidence_single_medium():
    items = [UnsupportedConstruct("X", "medium", "m", "s")]
    assert abs(compute_confidence(items) - 0.85) < 1e-9


def test_compute_confidence_single_low():
    items = [UnsupportedConstruct("X", "low", "m", "s")]
    assert abs(compute_confidence(items) - 0.95) < 1e-9


def test_compute_confidence_mixed():
    items = [
        UnsupportedConstruct("A", "high", "m", "s"),    # -0.30
        UnsupportedConstruct("B", "medium", "m", "s"),  # -0.15
        UnsupportedConstruct("C", "low", "m", "s"),     # -0.05
    ]
    assert abs(compute_confidence(items) - 0.50) < 1e-9


def test_compute_confidence_clamped_at_zero():
    items = [UnsupportedConstruct(str(i), "high", "m", "s") for i in range(10)]
    assert compute_confidence(items) == 0.0


# ── comment/string stripping regression tests (H1 / M2) ──────────────────────

def test_nvl_in_line_comment_not_flagged():
    # NVL only appears in a line comment — must not be reported as unsupported
    result = _run_transpile("SELECT 1 -- NVL(a,0)\nFROM t")
    assert "NVL" not in _constructs(result)


def test_rownum_in_string_literal_not_flagged():
    # ROWNUM only appears inside a string literal — must not be reported
    result = _run_transpile("SELECT 'ROWNUM' AS x FROM t")
    assert "ROWNUM" not in _constructs(result)


def test_analyze_sp_nvl_in_comment_not_flagged():
    # analyze_oracle_sp must also strip comments before construct detection
    result = _run_analyze("SELECT 1 /* NVL(a,0) is the Oracle function */ FROM t")
    assert "NVL" not in _constructs(result)


def test_linter_and_converter_agree_on_comment_sql():
    """Both linter and converter must produce zero Oracle-residual findings for SQL
    where Oracle keywords only appear inside comments."""
    from genie.core.lint_rules import check_nvl, check_rownum, check_sysdate

    sql = "SELECT id -- ROWNUM NVL SYSDATE\nFROM t WHERE id = 1"

    # Converter: no unsupported constructs
    result = _run_transpile(sql)
    assert _constructs(result).isdisjoint({"NVL", "ROWNUM", "SYSDATE"})

    # Linter: no findings for the same keywords
    assert check_nvl(sql, []) == []
    assert check_rownum(sql, []) == []
    assert check_sysdate(sql, []) == []
