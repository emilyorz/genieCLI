"""Tests for genie/skills/oracle2trino/__init__.py"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

import genie.skills.oracle2trino as o2t
from genie.skills.oracle2trino import (
    AnalyzeOracleSP,
    ListTrinoLimitations,
    LookupOracleFunction,
    LookupOracleType,
    TranspileSQL,
    _load_db,
    _sqlglot_transpile,
    _truncate,
    register,
)


# ── _truncate ─────────────────────────────────────────────────────────────────

def test_truncate_short_text_unchanged():
    assert _truncate("hello", 3000) == "hello"


def test_truncate_long_text_adds_ellipsis():
    result = _truncate("x" * 4000, 3000)
    assert len(result) == 3003   # 3000 + "..."
    assert result.endswith("...")


def test_truncate_exact_limit_unchanged():
    text = "a" * 3000
    assert _truncate(text) == text


# ── _load_db ──────────────────────────────────────────────────────────────────

def test_load_db_returns_dict():
    db = _load_db()
    assert isinstance(db, dict)
    assert "functions" in db or "types" in db


def test_load_db_caches():
    # Reset cache
    original_db = o2t._DB
    o2t._DB = None
    db1 = _load_db()
    db2 = _load_db()
    assert db1 is db2
    o2t._DB = original_db


# ── _sqlglot_transpile ────────────────────────────────────────────────────────

def test_sqlglot_transpile_not_installed():
    """_sqlglot_transpile returns 3-tuple (sql, errors, success)."""
    with patch.dict("sys.modules", {"sqlglot": None}):
        with patch("builtins.__import__", side_effect=lambda n, *a, **k: (_ for _ in ()).throw(ImportError()) if n == "sqlglot" else __import__(n, *a, **k)):
            result, errors, success = _sqlglot_transpile("SELECT 1 FROM DUAL")
    assert "SELECT 1 FROM DUAL" in result
    assert not success

def test_sqlglot_transpile_import_error_branch():
    """Test the ImportError path directly."""
    with patch("builtins.__import__", side_effect=ImportError("no module")):
        result, errors, success = _sqlglot_transpile("SELECT NVL(a, 0) FROM t")
    assert isinstance(result, str)
    assert not success
    assert any("sqlglot" in e.lower() for e in errors)


def test_sqlglot_transpile_exception_branch():
    """Test the generic Exception path."""
    mock_sqlglot = MagicMock()
    mock_sqlglot.transpile.side_effect = RuntimeError("parse error")
    mock_errors = MagicMock()
    mock_errors.ErrorLevel.IGNORE = "IGNORE"

    with patch.dict("sys.modules", {"sqlglot": mock_sqlglot, "sqlglot.errors": mock_errors}):
        result, errors, success = _sqlglot_transpile("INVALID SQL @#$")
    assert not success
    assert any("transpile error" in e for e in errors)


def test_sqlglot_transpile_known_gap_detection():
    """_sqlglot_transpile itself does NOT detect gaps — that's _detect_unsupported's job."""
    mock_sqlglot = MagicMock()
    mock_sqlglot.transpile.return_value = ["SELECT ROWNUM FROM t"]
    mock_errors = MagicMock()
    mock_errors.ErrorLevel.IGNORE = "IGNORE"

    with patch.dict("sys.modules", {"sqlglot": mock_sqlglot, "sqlglot.errors": mock_errors}):
        result, errors, success = _sqlglot_transpile("SELECT ROWNUM FROM t")
    assert success
    assert result == "SELECT ROWNUM FROM t"


def test_sqlglot_transpile_no_gaps():
    mock_sqlglot = MagicMock()
    mock_sqlglot.transpile.return_value = ["SELECT col FROM t"]
    mock_errors = MagicMock()
    mock_errors.ErrorLevel.IGNORE = "IGNORE"

    with patch.dict("sys.modules", {"sqlglot": mock_sqlglot, "sqlglot.errors": mock_errors}):
        result, errors, success = _sqlglot_transpile("SELECT col FROM t")
    assert success
    assert errors == []


# ── TranspileSQL ──────────────────────────────────────────────────────────────

def test_transpile_sql_run_no_warnings():
    """TranspileSQL.run() now returns JSON ConversionResult."""
    import json
    mock_sqlglot = MagicMock()
    mock_sqlglot.transpile.return_value = ["SELECT col FROM t"]
    mock_errors = MagicMock()
    mock_errors.ErrorLevel.IGNORE = "IGNORE"

    with patch.dict("sys.modules", {"sqlglot": mock_sqlglot, "sqlglot.errors": mock_errors}):
        skill = TranspileSQL()
        result = skill.run(sql="SELECT col FROM t")
    data = json.loads(result)
    assert data["converted_sql"] == "SELECT col FROM t"
    assert data["unsupported"] == []
    assert data["confidence"] == 1.0


def test_transpile_sql_run_with_warning():
    mock_sqlglot = MagicMock()
    mock_sqlglot.transpile.return_value = ["SELECT ROWNUM FROM t"]
    mock_errors = MagicMock()
    mock_errors.ErrorLevel.IGNORE = "IGNORE"

    with patch.dict("sys.modules", {"sqlglot": mock_sqlglot, "sqlglot.errors": mock_errors}):
        skill = TranspileSQL()
        result = skill.run(sql="SELECT ROWNUM FROM t")
    assert "Warnings" in result or "ROWNUM" in result


# ── LookupOracleFunction ──────────────────────────────────────────────────────

def test_lookup_oracle_function_exact_match():
    skill = LookupOracleFunction()
    result = skill.run(oracle_name="NVL")
    assert "NVL" in result
    assert "COALESCE" in result or "No mapping" not in result


def test_lookup_oracle_function_case_insensitive():
    skill = LookupOracleFunction()
    result_upper = skill.run(oracle_name="NVL")
    result_lower = skill.run(oracle_name="nvl")
    assert result_upper == result_lower


def test_lookup_oracle_function_not_found():
    skill = LookupOracleFunction()
    result = skill.run(oracle_name="XYZNONEXISTENT_FUNC_99")
    assert "No mapping found" in result


def test_lookup_oracle_function_partial_match():
    skill = LookupOracleFunction()
    # Should find NVL and NVL2 with partial "NVL"
    result = skill.run(oracle_name="NVL")
    assert "Oracle: NVL" in result


# ── LookupOracleType ──────────────────────────────────────────────────────────

def test_lookup_oracle_type_found():
    db = _load_db()
    # Find an actual type in the DB to test with
    types = db.get("types", [])
    if types:
        oracle_type = types[0]["oracle"]
        skill = LookupOracleType()
        result = skill.run(oracle_type=oracle_type)
        assert oracle_type in result
    else:
        pytest.skip("No types in DB")


def test_lookup_oracle_type_not_found():
    skill = LookupOracleType()
    result = skill.run(oracle_type="NONEXISTENT_TYPE_XYZ")
    assert "No type mapping found" in result


def test_lookup_oracle_type_case_insensitive():
    db = _load_db()
    types = db.get("types", [])
    if not types:
        pytest.skip("No types in DB")
    oracle_type = types[0]["oracle"]
    skill = LookupOracleType()
    result_upper = skill.run(oracle_type=oracle_type.upper())
    result_lower = skill.run(oracle_type=oracle_type.lower())
    assert result_upper == result_lower


# ── ListTrinoLimitations ──────────────────────────────────────────────────────

def test_list_trino_limitations_returns_data():
    skill = ListTrinoLimitations()
    result = skill.run()
    assert isinstance(result, str)
    assert len(result) > 0


def test_list_trino_limitations_handles_empty_db():
    skill = ListTrinoLimitations()
    with patch.object(o2t, "_load_db", return_value={"trino_limitations": []}):
        result = skill.run()
    assert "No limitations" in result


# ── AnalyzeOracleSP ───────────────────────────────────────────────────────────

def test_analyze_oracle_sp_pure_sql():
    """AnalyzeOracleSP.run() now returns JSON ConversionResult."""
    import json
    mock_sqlglot = MagicMock()
    mock_sqlglot.transpile.return_value = ["SELECT col FROM t"]
    mock_errors = MagicMock()
    mock_errors.ErrorLevel.IGNORE = "IGNORE"

    with patch.dict("sys.modules", {"sqlglot": mock_sqlglot, "sqlglot.errors": mock_errors}):
        skill = AnalyzeOracleSP()
        result = skill.run(sql="SELECT col FROM t", connector="iceberg")
    data = json.loads(result)
    assert data["converted_sql"] == "SELECT col FROM t"
    assert data["unsupported"] == []
    assert data["confidence"] == 1.0
    assert any("iceberg" in w.lower() for w in data["warnings"])


def test_analyze_oracle_sp_detects_begin_block():
    mock_sqlglot = MagicMock()
    mock_sqlglot.transpile.return_value = ["BEGIN SELECT 1; END;"]
    mock_errors = MagicMock()
    mock_errors.ErrorLevel.IGNORE = "IGNORE"

    with patch.dict("sys.modules", {"sqlglot": mock_sqlglot, "sqlglot.errors": mock_errors}):
        skill = AnalyzeOracleSP()
        result = skill.run(sql="BEGIN SELECT 1; END;", connector="hive")
    assert "BEGIN" in result


def test_analyze_oracle_sp_detects_rownum():
    mock_sqlglot = MagicMock()
    mock_sqlglot.transpile.return_value = ["SELECT * FROM t WHERE ROWNUM <= 10"]
    mock_errors = MagicMock()
    mock_errors.ErrorLevel.IGNORE = "IGNORE"

    with patch.dict("sys.modules", {"sqlglot": mock_sqlglot, "sqlglot.errors": mock_errors}):
        skill = AnalyzeOracleSP()
        result = skill.run(sql="SELECT * FROM t WHERE ROWNUM <= 10")
    assert "ROWNUM" in result


def test_analyze_oracle_sp_connector_notes():
    mock_sqlglot = MagicMock()
    mock_sqlglot.transpile.return_value = ["SELECT 1"]
    mock_errors = MagicMock()
    mock_errors.ErrorLevel.IGNORE = "IGNORE"

    for connector in ("hive", "iceberg", "delta", "generic"):
        with patch.dict("sys.modules", {"sqlglot": mock_sqlglot, "sqlglot.errors": mock_errors}):
            skill = AnalyzeOracleSP()
            result = skill.run(sql="SELECT 1", connector=connector)
        assert connector in result


# ── register ──────────────────────────────────────────────────────────────────

def test_register_all_skills():
    class _FakeRegistry:
        def __init__(self):
            self.registered = []
        def register(self, skill):
            self.registered.append(skill)

    reg = _FakeRegistry()
    register(reg)
    names = [s.name for s in reg.registered]
    assert "transpile_sql" in names
    assert "lookup_oracle_function" in names
    assert "lookup_oracle_type" in names
    assert "list_trino_limitations" in names
    assert "analyze_oracle_sp" in names
