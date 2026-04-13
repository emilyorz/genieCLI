"""Tests for genie.core.sql_patterns — shared Oracle construct catalog."""
import pytest

from genie.core.sql_patterns import (
    ORACLE_CONSTRUCTS,
    compute_confidence,
    get_construct_meta,
    get_construct_pattern,
)


def test_oracle_constructs_is_nonempty():
    assert len(ORACLE_CONSTRUCTS) > 0


def test_each_construct_has_required_fields():
    required = {"pattern", "construct", "severity", "message", "suggestion", "linter_rule", "plsql"}
    for entry in ORACLE_CONSTRUCTS:
        missing = required - set(entry.keys())
        assert not missing, f"Construct {entry.get('construct', '?')} missing fields: {missing}"


def test_get_construct_meta_known():
    meta = get_construct_meta("ROWNUM")
    assert meta is not None
    assert meta["severity"] == "high"


def test_get_construct_meta_unknown():
    assert get_construct_meta("NONEXISTENT_THING") is None


def test_get_construct_pattern_known():
    pattern = get_construct_pattern("NVL")
    assert pattern is not None
    assert "NVL" in pattern


def test_get_construct_pattern_unknown():
    assert get_construct_pattern("NONEXISTENT") is None


def test_compute_confidence_no_issues():
    assert compute_confidence([]) == 1.0


def test_compute_confidence_high_deducts():
    issues = [{"severity": "high"}]
    assert compute_confidence(issues) == pytest.approx(0.7)


def test_compute_confidence_floors_at_zero():
    issues = [{"severity": "high"}] * 10
    assert compute_confidence(issues) == 0.0


def test_reexport_from_oracle2trino():
    """Verify the backward-compat re-export from oracle2trino.patterns works."""
    from genie.skills.oracle2trino.patterns import (
        ORACLE_CONSTRUCTS as reexported,
        get_construct_meta as reexported_meta,
    )
    assert reexported is ORACLE_CONSTRUCTS
    assert reexported_meta is get_construct_meta


def test_lint_rules_import_from_core():
    """Verify lint rules (now in genie/core/) import from core, not cross-skill."""
    import inspect
    from genie.core import lint_rules
    source = inspect.getsource(lint_rules)
    assert "genie.core.sql_patterns" in source
    assert "genie.skills.oracle2trino.patterns" not in source
