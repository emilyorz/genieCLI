"""Contract tests binding the static-rule producers to their consumers.

Before v32 the rule producers emitted ids like ``redundant-distinct-after-group-by``
while ``rule_gate._STATIC_ACTIONS`` and ``pre_execution_diagnosis._RULE_KIND_MAP``
keyed on stale guesses, so the gate's ``REWRITE`` class never fired in production
and the gate tests passed only because they fed *fabricated* rule_ids. These tests
drive the REAL ``analyze()`` pipeline and forbid that drift from returning.
"""
from __future__ import annotations

from genie.skills.mcp_trino.pre_execution_diagnosis import (
    _RULE_KIND_MAP,
    pre_execution_diagnosis,
)
from genie.skills.mcp_trino.rule_gate import (
    ACTION_REWRITE,
    _STATIC_ACTIONS,
    build_rule_gate_summary,
)
from genie.skills.trino_query.sql_static import analyze
from genie.skills.trino_query.sql_static.rule_ids import ALL_RULE_IDS

# SQL that exercises each of the 8 rules. The contract only requires that every
# emitted rule_id is known; individual heuristics may or may not fire per snippet.
_CORPUS = [
    "SELECT a FROM x CROSS JOIN y",
    "SELECT * FROM t",
    "SELECT DISTINCT a FROM t GROUP BY a",
    "SELECT * FROM (SELECT a FROM t ORDER BY a) z",
    "SELECT (SELECT max(b) FROM u) AS m FROM t",
    "WITH c AS (SELECT a, b FROM raw.events) SELECT a FROM c WHERE a > 1",
    "SELECT a FROM t WHERE a = NULL",
    "SELECT CAST(CAST(a AS varchar) AS varchar) AS c FROM t",
]


def test_consumer_maps_cover_exactly_the_real_rule_ids():
    """Both consumer maps must key on real emitted ids — no stale guesses, no gaps."""
    assert set(_STATIC_ACTIONS) == set(ALL_RULE_IDS), (
        "rule_gate._STATIC_ACTIONS keys drifted from ALL_RULE_IDS"
    )
    assert set(_RULE_KIND_MAP) == set(ALL_RULE_IDS), (
        "pre_execution_diagnosis._RULE_KIND_MAP keys drifted from ALL_RULE_IDS"
    )


def test_producers_emit_only_known_rule_ids():
    """Whatever the rules emit across the corpus must be in ALL_RULE_IDS."""
    emitted: set[str] = set()
    for sql in _CORPUS:
        report = analyze(sql)
        emitted.update(f.rule_id for f in report.findings)
    assert emitted, "corpus triggered no findings — the test would be vacuous"
    assert emitted <= set(ALL_RULE_IDS), f"unknown rule_ids emitted: {emitted - set(ALL_RULE_IDS)}"


def test_real_analyze_to_gate_fires_rewrite():
    """End-to-end (no fabricated ids): a redundant-DISTINCT query must classify
    as REWRITE through the real analyze() -> gate pipeline. This is the exact
    path that silently produced rewrite=0 before v32."""
    sql = "SELECT DISTINCT a FROM t GROUP BY a"
    report = analyze(sql)
    assert any(
        f.rule_id == "redundant-distinct-after-group-by" for f in report.findings
    ), "fixture SQL no longer triggers r3; pick another REWRITE-class rule"

    summary = build_rule_gate_summary(
        static_report=report,
        directions=pre_execution_diagnosis(sql, static_report=report),
    )
    assert summary.counts[ACTION_REWRITE] >= 1, (
        f"REWRITE never fired through the real pipeline: {summary.counts}"
    )
