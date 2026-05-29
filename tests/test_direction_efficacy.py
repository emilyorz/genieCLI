"""v32 T2: observational direction efficacy / attribution."""
from __future__ import annotations

from genie.skills.mcp_trino.pre_execution_diagnosis import (
    OptimizationDirection,
    attribute_directions,
    format_attribution_report,
)


def _direction(kind: str, target_metric: str) -> OptimizationDirection:
    return OptimizationDirection(
        kind=kind,
        severity="high",
        rationale="r",
        evidence="explain:x",
        target_metric=target_metric,
    )


def test_marks_direction_moved_when_target_metric_improves():
    d = _direction("memory-pressure", "peak_memory_bytes")
    [outcome] = attribute_directions(
        [d],
        {"peak_memory_bytes": 2_000_000_000.0},
        {"peak_memory_bytes": 1_000_000_000.0},
    )
    assert outcome.observed_moved is True
    assert outcome.delta == -1_000_000_000.0
    assert outcome.co_attributed is False


def test_does_not_mark_moved_when_metric_unchanged_or_worse():
    d = _direction("reduce-scan", "physical_input_bytes")
    [worse] = attribute_directions(
        [d], {"physical_input_bytes": 100.0}, {"physical_input_bytes": 150.0},
    )
    assert worse.observed_moved is False
    assert worse.delta == 50.0


def test_missing_metric_yields_no_delta_and_not_moved():
    d = _direction("leverage-sort", "physical_input_bytes")
    [outcome] = attribute_directions([d], {"wall_time_ms": 10.0}, {"wall_time_ms": 5.0})
    assert outcome.delta is None
    assert outcome.observed_moved is False
    assert outcome.baseline_value is None


def test_directions_sharing_a_metric_are_co_attributed():
    shared_a = _direction("reduce-scan", "physical_input_bytes")
    shared_b = _direction("leverage-partitioning", "physical_input_bytes")
    sole = _direction("memory-pressure", "peak_memory_bytes")
    outcomes = attribute_directions(
        [shared_a, shared_b, sole],
        {"physical_input_bytes": 100.0, "peak_memory_bytes": 100.0},
        {"physical_input_bytes": 50.0, "peak_memory_bytes": 50.0},
    )
    by_kind = {o.kind: o for o in outcomes}
    assert by_kind["reduce-scan"].co_attributed is True
    assert by_kind["leverage-partitioning"].co_attributed is True
    assert by_kind["memory-pressure"].co_attributed is False
    # all three still observed as moved (lower is better)
    assert all(o.observed_moved for o in outcomes)


def test_empty_inputs_render_to_empty_block():
    assert attribute_directions([], {}, {}) == []
    assert format_attribution_report([]) == ""


def test_report_renders_table_with_moved_and_attribution_columns():
    d = _direction("memory-pressure", "peak_memory_bytes")
    outcomes = attribute_directions(
        [d], {"peak_memory_bytes": 2.0}, {"peak_memory_bytes": 1.0}
    )
    block = format_attribution_report(outcomes)
    assert "Direction efficacy" in block
    assert "memory-pressure" in block
    assert "yes" in block          # moved
    assert "sole" in block         # attribution
