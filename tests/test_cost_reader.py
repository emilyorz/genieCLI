"""Tests for genie.skills.mcp_trino.cost_reader.

Pure unit tests — uses stub runner closures, no live MCP/cluster.
"""
from __future__ import annotations

import json

import pytest

from genie.skills.mcp_trino.cost_reader import CostReading, read_cost
from genie.skills.mcp_trino.preflight import _combine_cost


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------

def _make_explain_json(rows: float | None = 12345.0, bytes_: float | None = 67890.0) -> str:
    """Build a minimal Trino EXPLAIN (FORMAT JSON) fixture with `estimates`."""
    estimates = {}
    if rows is not None:
        estimates["outputRowCount"] = rows
    if bytes_ is not None:
        estimates["outputSizeInBytes"] = bytes_
    return json.dumps({
        "estimates": [estimates],
        "children": [],
    })


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_read_cost_none_runner_no_runner():
    """runner=None -> available=False, reason='no_runner', all fields None."""
    result = read_cost("SELECT 1", None)
    assert isinstance(result, CostReading)
    assert result.available is False
    assert result.reason == "no_runner"
    assert result.rows_est is None
    assert result.bytes_est is None
    assert result.cost_scalar is None
    assert result.plan_json is None


def test_read_cost_runner_raises_cost_unavailable():
    """Runner raises (simulate cluster down) -> caught inside plan_cost -> cost_unavailable."""
    def raising_runner(sql: str):
        raise ConnectionError("cluster unreachable")

    result = read_cost("SELECT 1", raising_runner)
    assert result.available is False
    assert result.reason == "cost_unavailable"
    assert result.rows_est is None
    assert result.bytes_est is None
    assert result.cost_scalar is None


def test_read_cost_garbage_runner_output_cost_unavailable():
    """Runner returns non-JSON garbage -> plan_cost collapses -> cost_unavailable.

    Same coarse reason as a raise — we do NOT claim to distinguish these.
    """
    def garbage_runner(sql: str) -> str:
        return "not json at all"

    result = read_cost("SELECT 1", garbage_runner)
    assert result.available is False
    assert result.reason == "cost_unavailable"
    # Asserts the coarse reason — garbage and cluster-down both map to same string
    assert result.cost_scalar is None


def test_read_cost_valid_explain_json():
    """Canned Trino EXPLAIN JSON -> rows_est/bytes_est as ints, cost_scalar correct, 'ok'."""
    expected_rows = 12345
    expected_bytes = 67890

    def valid_runner(sql: str) -> str:
        return _make_explain_json(float(expected_rows), float(expected_bytes))

    result = read_cost("SELECT 1", valid_runner)
    assert result.available is True
    assert result.reason == "ok"
    assert isinstance(result.rows_est, int), f"rows_est should be int, got {type(result.rows_est)}"
    assert isinstance(result.bytes_est, int), f"bytes_est should be int, got {type(result.bytes_est)}"
    assert result.rows_est == expected_rows
    assert result.bytes_est == expected_bytes
    # cost_scalar must match _combine_cost
    expected_scalar = _combine_cost(result.rows_est, result.bytes_est)
    assert result.cost_scalar == expected_scalar, (
        f"cost_scalar {result.cost_scalar!r} != _combine_cost result {expected_scalar!r}"
    )
    assert isinstance(result.cost_scalar, int), (
        f"cost_scalar should be int, got {type(result.cost_scalar)}"
    )
    assert result.plan_json is not None


def test_read_cost_bytes_only_plan():
    """Bytes but no rows -> cost_scalar uses bytes-only path of _combine_cost."""
    expected_bytes = 999999

    def bytes_only_runner(sql: str) -> str:
        return _make_explain_json(rows=None, bytes_=float(expected_bytes))

    result = read_cost("SELECT 1", bytes_only_runner)
    assert result.available is True
    assert result.rows_est is None
    assert isinstance(result.bytes_est, int)
    assert result.bytes_est == expected_bytes
    # _combine_cost(None, bytes_) returns bytes_
    assert result.cost_scalar == expected_bytes, (
        f"cost_scalar should equal bytes_est ({expected_bytes}), got {result.cost_scalar}"
    )


def test_read_cost_never_raises():
    """read_cost must never propagate exceptions regardless of runner behaviour."""
    import traceback

    bad_runners = [
        None,
        lambda sql: (_ for _ in ()).throw(RuntimeError("boom")),  # raises
        lambda sql: None,       # returns None
        lambda sql: "",         # returns empty string
        lambda sql: "{}",       # valid JSON, no estimates
    ]
    for runner in bad_runners:
        try:
            result = read_cost("SELECT 1", runner)
            assert isinstance(result, CostReading)
        except Exception as exc:
            pytest.fail(f"read_cost raised unexpectedly: {exc}\n{traceback.format_exc()}")
