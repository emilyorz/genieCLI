"""Tests for R10 join-key-computed — a function/cast/arithmetic wraps a JOIN ON key.

`JOIN t ON UPPER(a.x) = b.y` blocks the planner from hash-joining on a raw key.
None of R1-R9 inspect the JOIN ON predicate; R10 closes that gap. These tests pin:
positive cases fire, bare-column / range / WHERE-side functions do NOT misfire,
symmetric normalization is flagged at lower severity, and the full registration
chain (rule_ids → gate → diagnosis) is wired (no v32-style silent fall-through).
"""
import pytest

from genie.skills.trino_query.sql_static import analyze
from genie.skills.trino_query.sql_static.rule_ids import (
    ALL_RULE_IDS,
    RULE_JOIN_KEY_COMPUTED,
)
from genie.skills.mcp_trino.rule_gate import build_rule_gate_summary
from genie.skills.mcp_trino.pre_execution_diagnosis import (
    _RULE_KIND_MAP,
    pre_execution_diagnosis,
)


def _r10(sql):
    return [f for f in analyze(sql).findings if f.rule_id == RULE_JOIN_KEY_COMPUTED]


# ── positive: a computed join key fires ───────────────────────────────────────

@pytest.mark.parametrize(
    "sql",
    [
        "SELECT 1 FROM a JOIN b ON UPPER(a.x) = b.y",
        "SELECT 1 FROM a JOIN b ON CAST(a.id AS varchar) = b.id",
        "SELECT 1 FROM a JOIN b ON a.x + 1 = b.k",
        "SELECT 1 FROM a JOIN b ON COALESCE(a.x, 0) = b.y",
        "SELECT 1 FROM a JOIN b ON b.y = SUBSTRING(a.x, 1, 3)",  # computed on the right
    ],
)
def test_computed_join_key_fires_medium(sql):
    findings = _r10(sql)
    assert len(findings) == 1
    assert findings[0].severity == "medium"


def test_symmetric_normalization_fires_low():
    findings = _r10("SELECT 1 FROM a JOIN b ON LOWER(a.x) = LOWER(b.x)")
    assert len(findings) == 1
    assert findings[0].severity == "low"


# ── negative: must NOT misfire ────────────────────────────────────────────────

@pytest.mark.parametrize(
    "sql",
    [
        "SELECT 1 FROM a JOIN b ON a.x = b.y",                       # bare columns
        "SELECT 1 FROM a JOIN b ON a.x = b.y AND a.z = b.w",         # multiple bare keys
        "SELECT 1 FROM a JOIN b ON a.ts BETWEEN b.s AND b.e",        # range join, no EQ
        "SELECT 1 FROM a JOIN b ON a.x > b.y",                       # inequality, no EQ
        "SELECT x FROM a WHERE UPPER(a.x) = 'A'",                    # function in WHERE, not ON
        "SELECT 1 FROM a JOIN b ON a.x = b.y WHERE UPPER(a.z) = 'Q'",  # WHERE func must not leak
    ],
)
def test_does_not_misfire(sql):
    assert _r10(sql) == []


# ── registration chain: no silent fall-through (the v32 lesson) ───────────────

def test_rule_id_registered():
    assert RULE_JOIN_KEY_COMPUTED in ALL_RULE_IDS
    assert RULE_JOIN_KEY_COMPUTED == "join-key-computed"


def test_rule_gate_classifies_as_advise_not_generic():
    report = analyze("SELECT 1 FROM a JOIN b ON UPPER(a.x) = b.y")
    summary = build_rule_gate_summary(report, [])
    items = [i for i in summary.items if i.rule_id == RULE_JOIN_KEY_COMPUTED]
    assert len(items) == 1
    # ADVISE (not auto-rewrite) and a real gate suggestion, not the generic fallback
    assert items[0].action == "advise"
    assert "raw column" in items[0].suggestion.lower()


def test_diagnosis_kind_mapped():
    assert _RULE_KIND_MAP[RULE_JOIN_KEY_COMPUTED] == "fix-join-key-computed"
    report = analyze("SELECT 1 FROM a JOIN b ON UPPER(a.x) = b.y")
    directions = pre_execution_diagnosis(
        "SELECT 1 FROM a JOIN b ON UPPER(a.x) = b.y",
        static_report=report,
        explain_cost=None,
        table_metadata=None,
        peak_memory_bytes=None,
    )
    kinds = [d.kind for d in directions]
    assert "fix-join-key-computed" in kinds
