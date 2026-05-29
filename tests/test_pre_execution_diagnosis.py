"""Tests for genie/skills/mcp_trino/pre_execution_diagnosis.py

Fake input objects use types.SimpleNamespace so we stay decoupled from
production classes while matching the real attribute shapes exactly.
"""
from __future__ import annotations

import types
from dataclasses import dataclass, field

import pytest

from genie.skills.mcp_trino.pre_execution_diagnosis import (
    HIGH_PEAK_MEMORY_BYTES,
    LARGE_SCAN_BYTES,
    OptimizationDirection,
    format_directions_for_prompt,
    pre_execution_diagnosis,
)


# ---------------------------------------------------------------------------
# Fake input builders — attribute shapes match production exactly
# ---------------------------------------------------------------------------

def _make_finding(
    severity: str = "high",
    rule_id: str = "cartesian-join",
    message: str = "Cartesian join detected.",
    suggestion: str = "Add a JOIN condition.",
    line: int = 4,
) -> types.SimpleNamespace:
    return types.SimpleNamespace(
        severity=severity,
        rule_id=rule_id,
        message=message,
        suggestion=suggestion,
        line=line,
    )


def _make_static_report(
    findings: list | None = None,
    parse_error: str | None = None,
) -> types.SimpleNamespace:
    return types.SimpleNamespace(
        findings=findings if findings is not None else [],
        parse_error=parse_error,
    )


def _make_table_metadata(
    table_name: str = "events",
    properties: dict | None = None,
) -> types.SimpleNamespace:
    return types.SimpleNamespace(
        catalog="hive",
        schema="prod",
        table_name=table_name,
        columns=[],
        properties=properties if properties is not None else {},
    )


def _large_plan_with_non_leaf_build(size_bytes: int) -> dict:
    """Plan tree: root → [child → [grandchild]] with estimates on child (non-leaf)."""
    return {
        "name": "HashJoin",
        "estimates": [{"outputSizeInBytes": size_bytes}],
        "children": [
            {
                "name": "TableScan",
                "estimates": [],
                "children": [
                    {"name": "Leaf", "estimates": [], "children": []}
                ],
            }
        ],
    }


# ---------------------------------------------------------------------------
# AC1 — high-severity static finding ranks first
# ---------------------------------------------------------------------------

def test_should_rank_high_severity_static_finding_first():
    finding = _make_finding(severity="high", rule_id="cartesian-join", line=4)
    report = _make_static_report(findings=[finding])

    result = pre_execution_diagnosis("SELECT 1", static_report=report)

    assert len(result) >= 1
    first = result[0]
    assert first.severity == "high"
    assert first.evidence.startswith("static:")
    assert first.kind == "fix-cartesian-join"


def test_should_emit_one_direction_per_static_finding():
    findings = [
        _make_finding(severity="high", rule_id="cartesian-join", line=1),
        _make_finding(severity="medium", rule_id="select-star", line=5),
    ]
    report = _make_static_report(findings=findings)

    result = pre_execution_diagnosis("SELECT 1", static_report=report)

    static_directions = [d for d in result if d.evidence.startswith("static:")]
    assert len(static_directions) == 2


def test_should_map_known_rule_id_to_canonical_kind():
    known_rules = [
        ("cartesian-join", "fix-cartesian-join"),
        ("select-star", "fix-select-star"),
        ("redundant-distinct-after-group-by", "fix-distinct-after-group-by"),
        ("unnecessary-order-by-in-subquery", "fix-order-by-in-subquery"),
        ("subquery-in-select-pushable-to-join", "fix-subquery-in-select"),
        ("predicate-not-pushed-to-cte", "fix-predicate-pushdown"),
        ("null-unsafe-equals", "fix-null-unsafe-equals"),
        ("redundant-cast-chain", "fix-redundant-cast"),
    ]
    for rule_id, expected_kind in known_rules:
        finding = _make_finding(rule_id=rule_id)
        report = _make_static_report(findings=[finding])
        result = pre_execution_diagnosis("SELECT 1", static_report=report)
        assert result[0].kind == expected_kind, f"rule_id={rule_id}"


def test_should_use_static_prefix_fallback_for_unknown_rule_id():
    finding = _make_finding(rule_id="some-new-rule")
    report = _make_static_report(findings=[finding])

    result = pre_execution_diagnosis("SELECT 1", static_report=report)

    assert result[0].kind == "static-some-new-rule"


# ---------------------------------------------------------------------------
# AC2 — explain_cost with large non-leaf outputSizeInBytes → memory-pressure
# ---------------------------------------------------------------------------

def test_should_emit_memory_pressure_from_explain_plan_when_peak_memory_none():
    large_bytes = HIGH_PEAK_MEMORY_BYTES + 1
    plan = _large_plan_with_non_leaf_build(large_bytes)
    explain_cost = (None, None, plan)

    result = pre_execution_diagnosis(
        "SELECT 1",
        explain_cost=explain_cost,
        peak_memory_bytes=None,
    )

    memory_dirs = [
        d for d in result
        if d.kind == "memory-pressure" and d.target_metric == "peak_memory_bytes"
    ]
    assert len(memory_dirs) >= 1
    assert memory_dirs[0].evidence.startswith("explain:")


def test_should_emit_reduce_scan_when_bytes_est_over_threshold():
    large_bytes = LARGE_SCAN_BYTES + 1
    explain_cost = (None, large_bytes, None)

    result = pre_execution_diagnosis("SELECT 1", explain_cost=explain_cost)

    scan_dirs = [d for d in result if d.kind == "reduce-scan"]
    assert len(scan_dirs) == 1
    assert scan_dirs[0].target_metric == "physical_input_bytes"
    assert scan_dirs[0].evidence.startswith("explain:")


def test_should_emit_reduce_scan_high_severity_when_ten_times_threshold():
    very_large = LARGE_SCAN_BYTES * 11
    explain_cost = (None, very_large, None)

    result = pre_execution_diagnosis("SELECT 1", explain_cost=explain_cost)

    scan_dirs = [d for d in result if d.kind == "reduce-scan"]
    assert scan_dirs[0].severity == "high"


def test_should_not_emit_memory_pressure_from_leaf_only_plan():
    # A plan tree that is entirely leaves — no non-leaf node has estimates
    leaf_plan = {
        "name": "TableScan",
        "estimates": [{"outputSizeInBytes": HIGH_PEAK_MEMORY_BYTES + 1}],
        "children": [],  # leaf — should be ignored
    }
    explain_cost = (None, None, leaf_plan)

    result = pre_execution_diagnosis("SELECT 1", explain_cost=explain_cost)

    memory_from_explain = [
        d for d in result
        if d.kind == "memory-pressure" and d.evidence.startswith("explain:")
    ]
    assert memory_from_explain == []


# ---------------------------------------------------------------------------
# SQL shape — CTE materialization / repeated raw scan advice
# ---------------------------------------------------------------------------

def test_should_emit_materialize_cte_steps_for_complex_heavy_cte_chain():
    sql = """
    WITH
      step1 AS (
        SELECT account_id, count(*) AS cnt
        FROM raw.events
        GROUP BY account_id
      ),
      step2 AS (
        SELECT s.account_id, d.region, s.cnt
        FROM step1 s
        JOIN curated.dim_account d ON d.account_id = s.account_id
      ),
      step3 AS (
        SELECT region, sum(cnt) AS total_cnt
        FROM step2
        GROUP BY region
      )
    SELECT * FROM step3
    """

    result = pre_execution_diagnosis(sql)

    materialize = [d for d in result if d.kind == "materialize-cte-steps"]
    assert len(materialize) == 1
    assert materialize[0].severity == "high"
    assert materialize[0].evidence == "sql-shape:cte_count=3,heavy_ctes=3"


def test_should_not_emit_materialize_cte_steps_for_single_simple_cte():
    sql = "WITH step1 AS (SELECT account_id FROM raw.events) SELECT * FROM step1"

    result = pre_execution_diagnosis(sql)

    kinds = [d.kind for d in result]
    assert "materialize-cte-steps" not in kinds


def test_should_emit_reduce_raw_rescan_for_repeated_raw_table():
    sql = """
    WITH
      left_scan AS (SELECT account_id FROM raw.events WHERE dt = DATE '2026-05-01'),
      right_scan AS (SELECT account_id FROM raw.events WHERE dt = DATE '2026-05-02')
    SELECT *
    FROM left_scan l
    JOIN right_scan r ON l.account_id = r.account_id
    """

    result = pre_execution_diagnosis(sql)

    raw_rescan = [d for d in result if d.kind == "reduce-raw-rescan"]
    assert len(raw_rescan) == 1
    assert raw_rescan[0].severity == "medium"
    assert raw_rescan[0].evidence == "sql-shape:repeated_raw_scan=raw.eventsx2"


def test_should_not_emit_reduce_raw_rescan_for_repeated_curated_table():
    sql = """
    SELECT *
    FROM curated.dim_account a
    JOIN curated.dim_account b ON a.parent_id = b.account_id
    """

    result = pre_execution_diagnosis(sql)

    kinds = [d.kind for d in result]
    assert "reduce-raw-rescan" not in kinds


# ---------------------------------------------------------------------------
# AC3 — all inputs None → empty list, no raise
# ---------------------------------------------------------------------------

def test_should_return_empty_list_when_all_inputs_are_none():
    result = pre_execution_diagnosis(
        "SELECT 1",
        static_report=None,
        explain_cost=None,
        table_metadata=None,
        peak_memory_bytes=None,
    )
    assert result == []


# ---------------------------------------------------------------------------
# AC4 — determinism: identical inputs → identical output; input order irrelevant
# ---------------------------------------------------------------------------

def test_should_produce_identical_output_on_repeated_calls():
    findings = [
        _make_finding(severity="high", rule_id="cartesian-join", line=1),
        _make_finding(severity="low", rule_id="select-star", line=3),
    ]
    report = _make_static_report(findings=findings)
    table = _make_table_metadata(properties={"partitioned_by": "dt"})
    explain_cost = (1000, LARGE_SCAN_BYTES + 1, None)

    result_a = pre_execution_diagnosis(
        "SELECT * FROM t1, t2",
        static_report=report,
        explain_cost=explain_cost,
        table_metadata=[table],
    )
    result_b = pre_execution_diagnosis(
        "SELECT * FROM t1, t2",
        static_report=report,
        explain_cost=explain_cost,
        table_metadata=[table],
    )

    assert result_a == result_b


def test_should_produce_same_ranking_regardless_of_findings_input_order():
    finding_high = _make_finding(severity="high", rule_id="cartesian-join", line=1)
    finding_low = _make_finding(severity="low", rule_id="select-star", line=3)

    report_ab = _make_static_report(findings=[finding_high, finding_low])
    report_ba = _make_static_report(findings=[finding_low, finding_high])

    result_ab = pre_execution_diagnosis("SELECT 1", static_report=report_ab)
    result_ba = pre_execution_diagnosis("SELECT 1", static_report=report_ba)

    assert result_ab == result_ba


def test_should_produce_same_ranking_regardless_of_table_metadata_order():
    tm_sort = _make_table_metadata("t1", properties={"sorted_by": "col1"})
    tm_part = _make_table_metadata("t2", properties={"partitioned_by": "dt"})

    result_fwd = pre_execution_diagnosis(
        "SELECT 1", table_metadata=[tm_sort, tm_part]
    )
    result_rev = pre_execution_diagnosis(
        "SELECT 1", table_metadata=[tm_part, tm_sort]
    )

    # Total-order ranking → output list is identical regardless of input order.
    assert result_fwd == result_rev


def test_should_produce_same_order_for_same_kind_different_evidence():
    # Two partitioned tables produce two leverage-partitioning directions that
    # tie on (severity, source, kind); the evidence tie-breaker must make the
    # output order independent of input order.
    tm_a = _make_table_metadata("alpha", properties={"partitioned_by": "dt"})
    tm_b = _make_table_metadata("beta", properties={"partitioned_by": "region"})

    result_fwd = pre_execution_diagnosis("SELECT 1", table_metadata=[tm_a, tm_b])
    result_rev = pre_execution_diagnosis("SELECT 1", table_metadata=[tm_b, tm_a])

    assert result_fwd == result_rev
    assert [d.evidence for d in result_fwd] == [d.evidence for d in result_rev]


# ---------------------------------------------------------------------------
# AC5 — TableMetadata with partition/sort → leverage-* directions
# ---------------------------------------------------------------------------

def test_should_emit_leverage_sort_when_sorted_by_present():
    tm = _make_table_metadata("events", properties={"sorted_by": "event_time"})

    result = pre_execution_diagnosis("SELECT 1", table_metadata=[tm])

    kinds = [d.kind for d in result]
    assert "leverage-sort" in kinds


def test_should_emit_leverage_sort_when_sort_order_present():
    tm = _make_table_metadata("events", properties={"sort_order": "ASC"})

    result = pre_execution_diagnosis("SELECT 1", table_metadata=[tm])

    kinds = [d.kind for d in result]
    assert "leverage-sort" in kinds


def test_should_emit_leverage_partitioning_when_partitioned_by_present():
    # Hive-style key
    tm = _make_table_metadata("events", properties={"partitioned_by": "dt"})

    result = pre_execution_diagnosis("SELECT 1", table_metadata=[tm])

    kinds = [d.kind for d in result]
    assert "leverage-partitioning" in kinds


def test_should_emit_leverage_partitioning_when_iceberg_partitioning_present():
    # Real Trino/Iceberg property key — the production path (research.py:231)
    tm = _make_table_metadata(
        "events", properties={"partitioning": "[\"day(event_time)\"]"}
    )

    result = pre_execution_diagnosis("SELECT 1", table_metadata=[tm])

    part = [d for d in result if d.kind == "leverage-partitioning"]
    assert len(part) == 1
    assert "partitioning=" in part[0].evidence


def test_should_not_emit_leverage_partitioning_when_partitioning_is_empty_brackets():
    # "[]" is how Trino reports an unpartitioned table — must NOT emit
    tm = _make_table_metadata("events", properties={"partitioning": "[]"})

    result = pre_execution_diagnosis("SELECT 1", table_metadata=[tm])

    kinds = [d.kind for d in result]
    assert "leverage-partitioning" not in kinds


def test_should_not_emit_leverage_partitioning_when_partitioning_is_empty_string():
    tm = _make_table_metadata("events", properties={"partitioning": ""})

    result = pre_execution_diagnosis("SELECT 1", table_metadata=[tm])

    kinds = [d.kind for d in result]
    assert "leverage-partitioning" not in kinds


def test_should_not_emit_leverage_directions_when_no_sort_or_partition_props():
    tm = _make_table_metadata("events", properties={"format": "ORC"})

    result = pre_execution_diagnosis("SELECT 1", table_metadata=[tm])

    kinds = [d.kind for d in result]
    assert "leverage-sort" not in kinds
    assert "leverage-partitioning" not in kinds


def test_should_return_empty_when_table_metadata_is_empty_list():
    result = pre_execution_diagnosis("SELECT 1", table_metadata=[])
    assert result == []


# ---------------------------------------------------------------------------
# parse_error — static_report with truthy parse_error → no static directions
# ---------------------------------------------------------------------------

def test_should_contribute_nothing_when_static_report_has_parse_error():
    report = _make_static_report(
        findings=[_make_finding()],
        parse_error="SQL parse failed: unexpected token",
    )

    result = pre_execution_diagnosis("SELECT 1", static_report=report)

    static_dirs = [d for d in result if d.evidence.startswith("static:")]
    assert static_dirs == []


# ---------------------------------------------------------------------------
# per-arg-None parametrized — one arg None at a time, no raise
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "kwargs",
    [
        {"static_report": None},
        {"explain_cost": None},
        {"table_metadata": None},
        {"peak_memory_bytes": None},
    ],
)
def test_should_not_raise_when_single_arg_is_none(kwargs):
    finding = _make_finding()
    report = _make_static_report(findings=[finding])
    plan = _large_plan_with_non_leaf_build(HIGH_PEAK_MEMORY_BYTES + 1)

    base = dict(
        static_report=report,
        explain_cost=(None, LARGE_SCAN_BYTES + 1, plan),
        table_metadata=[_make_table_metadata(properties={"partitioned_by": "dt"})],
        peak_memory_bytes=HIGH_PEAK_MEMORY_BYTES + 1,
    )
    base.update(kwargs)

    result = pre_execution_diagnosis("SELECT 1", **base)  # type: ignore[arg-type]
    assert isinstance(result, list)


# ---------------------------------------------------------------------------
# peak_memory_bytes over threshold → runtime memory-pressure
# ---------------------------------------------------------------------------

def test_should_emit_memory_pressure_from_runtime_when_peak_over_threshold():
    peak = HIGH_PEAK_MEMORY_BYTES + 1

    result = pre_execution_diagnosis(
        "SELECT 1",
        peak_memory_bytes=peak,
    )

    runtime_dirs = [d for d in result if d.evidence.startswith("runtime:")]
    assert len(runtime_dirs) == 1
    assert runtime_dirs[0].kind == "memory-pressure"
    assert runtime_dirs[0].target_metric == "peak_memory_bytes"
    assert f"peak_memory_bytes={peak}" in runtime_dirs[0].evidence


def test_should_not_emit_memory_pressure_when_peak_at_or_below_threshold():
    result = pre_execution_diagnosis(
        "SELECT 1",
        peak_memory_bytes=HIGH_PEAK_MEMORY_BYTES,  # exactly at threshold, not over
    )

    runtime_dirs = [d for d in result if d.evidence.startswith("runtime:")]
    assert runtime_dirs == []


# ---------------------------------------------------------------------------
# Malformed inputs — no raise
# ---------------------------------------------------------------------------

def test_should_not_raise_on_malformed_explain_cost_tuple():
    result = pre_execution_diagnosis("SELECT 1", explain_cost=("not", "a", "valid", "tuple"))
    assert isinstance(result, list)


def test_should_not_raise_on_explain_cost_non_tuple():
    result = pre_execution_diagnosis("SELECT 1", explain_cost=42)
    assert isinstance(result, list)


def test_should_not_raise_when_plan_json_is_deeply_nested_garbage():
    garbage_plan = {"children": [{"children": [None, {"children": []}]}]}
    result = pre_execution_diagnosis(
        "SELECT 1",
        explain_cost=(None, None, garbage_plan),
    )
    assert isinstance(result, list)


# ---------------------------------------------------------------------------
# format_directions_for_prompt — pure prompt rendering
# ---------------------------------------------------------------------------

def test_should_return_empty_string_when_no_directions():
    assert format_directions_for_prompt([]) == ""


def test_should_render_one_numbered_line_per_direction():
    finding = _make_finding(severity="high", rule_id="cartesian-join", line=4)
    report = _make_static_report(findings=[finding])
    directions = pre_execution_diagnosis("SELECT 1", static_report=report)

    block = format_directions_for_prompt(directions)

    assert block.startswith("Pre-execution diagnosis")
    # one numbered entry per direction
    numbered = [ln for ln in block.splitlines() if ln[:2] in ("1.", "2.", "3.")]
    assert len(numbered) == len(directions)
    assert "fix-cartesian-join" in block
    assert "[high]" in block
    assert "static:cartesian-join@L4" in block


def test_should_cap_rendered_lines_at_limit():
    findings = [
        _make_finding(severity="low", rule_id="select-star", line=i)
        for i in range(1, 11)
    ]
    report = _make_static_report(findings=findings)
    directions = pre_execution_diagnosis("SELECT 1", static_report=report)

    block = format_directions_for_prompt(directions, limit=3)

    numbered = [ln for ln in block.splitlines() if ln and ln[0].isdigit()]
    assert len(numbered) == 3


def test_should_be_deterministic_for_same_directions():
    findings = [
        _make_finding(severity="high", rule_id="cartesian-join", line=1),
        _make_finding(severity="medium", rule_id="select-star", line=5),
    ]
    report = _make_static_report(findings=findings)
    directions = pre_execution_diagnosis("SELECT 1", static_report=report)

    assert format_directions_for_prompt(directions) == format_directions_for_prompt(directions)
