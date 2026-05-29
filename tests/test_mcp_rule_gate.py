"""Tests for the /trino-research rule-first gate."""
from __future__ import annotations

from types import SimpleNamespace

from genie.skills.mcp_trino.pre_execution_diagnosis import OptimizationDirection
from genie.skills.mcp_trino.rule_gate import (
    ACTION_ADVISE,
    ACTION_BLOCK,
    ACTION_PASS,
    ACTION_REWRITE,
    build_rule_gate_summary,
    format_rule_gate_for_prompt,
    render_rule_gate_summary,
)
from genie.skills.trino_query.sql_static import Finding, StaticAnalysisReport


def _report(*findings: Finding) -> StaticAnalysisReport:
    return StaticAnalysisReport(findings=list(findings))


def test_rule_gate_classifies_static_findings_by_action():
    summary = build_rule_gate_summary(
        _report(
            Finding("high", "cartesian-join", "cross join", "add join condition", 2),
            Finding("medium", "predicate-not-pushed-to-cte", "pushable", "move predicate", 5),
            Finding("medium", "select-star", "star", "name columns", 1),
        )
    )

    assert [item.action for item in summary.items] == [
        ACTION_BLOCK,
        ACTION_REWRITE,
        ACTION_ADVISE,
    ]
    assert summary.counts[ACTION_BLOCK] == 1
    assert summary.counts[ACTION_REWRITE] == 1
    assert summary.counts[ACTION_ADVISE] == 1
    assert summary.counts[ACTION_PASS] == 0
    assert summary.should_auto_iterate is True


def test_rule_gate_skips_static_directions_to_avoid_duplicates():
    directions = [
        OptimizationDirection(
            kind="fix-cartesian-join",
            severity="high",
            rationale="static duplicate",
            evidence="static:cartesian-join@L2",
            target_metric="wall_time_ms",
        ),
        OptimizationDirection(
            kind="materialize-cte-steps",
            severity="high",
            rationale="deep CTE chain",
            evidence="sql-shape:cte_count=3,heavy_ctes=2",
            target_metric="query_time_ms",
        ),
    ]

    summary = build_rule_gate_summary(_report(), directions)

    assert [item.rule_id for item in summary.items] == ["materialize-cte-steps"]
    assert summary.items[0].action == ACTION_ADVISE
    assert "CTAS/DDL" in summary.items[0].suggestion


def test_rule_gate_prompt_is_capped_and_labeled():
    findings = [
        Finding("low", "select-star", f"star {i}", "name columns", i)
        for i in range(1, 6)
    ]
    summary = build_rule_gate_summary(_report(*findings))

    block = format_rule_gate_for_prompt(summary, limit=2)

    assert block.startswith("Rule-based gate")
    assert "Counts: block=0, rewrite=0, advise=5" in block
    assert "block: do not semantic-repair automatically" in block
    assert "side effects: do not emit CTAS/materialized-view DDL" in block
    assert block.count("- [advise/low/medium] select-star") == 2


def test_rule_gate_empty_summary_returns_no_prompt_block():
    summary = build_rule_gate_summary(_report())

    assert summary.counts[ACTION_PASS] == 1
    assert format_rule_gate_for_prompt(summary) == ""


def test_rule_gate_fails_open_on_malformed_diagnostic_object():
    malformed = SimpleNamespace(
        findings=[
            SimpleNamespace(
                severity="high",
                rule_id="cartesian-join",
                message="bad line object",
                suggestion="inspect joins",
                line=object(),
            )
        ],
        parse_error=None,
    )

    summary = build_rule_gate_summary(malformed)

    assert summary.items == ()
    assert summary.counts[ACTION_PASS] == 1
    assert format_rule_gate_for_prompt(summary) == ""


def test_render_rule_gate_summary_uses_compact_human_block():
    class _Output:
        def __init__(self):
            self.lines = []

        def print(self, msg):
            self.lines.append(str(msg))

    output = _Output()
    summary = build_rule_gate_summary(
        _report(
            Finding("high", "cartesian-join", "cross join", "add join condition", 2),
            Finding("medium", "predicate-not-pushed-to-cte", "pushable", "move predicate", 5),
        )
    )

    render_rule_gate_summary(output, summary)

    rendered = "\n".join(output.lines)
    assert "rule gate" in rendered
    assert "block[/dim]=1" in rendered
    assert "rewrite[/dim]=1" in rendered
    assert "cartesian-join" in rendered
    assert "predicate-not-pushed" in rendered
