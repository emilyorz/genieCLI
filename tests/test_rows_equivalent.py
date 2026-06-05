"""Tests for rows_equivalent and the _results_equivalent backward-compat shim.

Pure unit tests — no cluster, no MCP.
"""
from __future__ import annotations

import math

import pytest

from genie.skills.mcp_trino.research import EquivDiff, _results_equivalent, rows_equivalent


# ---------------------------------------------------------------------------
# Helper fixtures
# ---------------------------------------------------------------------------

_ROW_A = {"id": 1, "name": "alice", "val": 10}
_ROW_B = {"id": 2, "name": "bob",   "val": 20}


# ---------------------------------------------------------------------------
# Core correctness tests
# ---------------------------------------------------------------------------

def test_rows_equivalent_identical_no_exclusion():
    """Identical rows -> True, 'exact match', mismatches == 0."""
    rows = [_ROW_A, _ROW_B]
    equiv, diff = rows_equivalent(rows, rows)
    assert equiv is True
    assert diff.equivalent is True
    assert diff.reason == "exact match"
    assert diff.mismatches == 0
    assert diff.first_mismatch is None
    assert diff.row_count_a == 2
    assert diff.row_count_b == 2


def test_rows_equivalent_empty_rows():
    """Both empty -> True, 'both empty'."""
    equiv, diff = rows_equivalent([], [])
    assert equiv is True
    assert diff.reason == "both empty"
    assert diff.row_count_a == 0


def test_rows_equivalent_row_count_diff():
    """Different lengths -> False, 'row count differs: A vs B'."""
    rows_a = [_ROW_A, _ROW_B]
    rows_b = [_ROW_A]
    equiv, diff = rows_equivalent(rows_a, rows_b)
    assert equiv is False
    assert diff.reason == f"row count differs: {len(rows_a)} vs {len(rows_b)}"
    assert diff.mismatches == 0
    assert diff.first_mismatch is None


def test_rows_equivalent_diff_summary_first_mismatch():
    """first_mismatch points at the first differing entry; mismatches count correct.

    Use two single-row sets so the mismatch count is unambiguous (1 vs 1).
    """
    rows_a = [{"x": 1}]
    rows_b = [{"x": 99}]
    equiv, diff = rows_equivalent(rows_a, rows_b)
    assert equiv is False
    assert diff.mismatches == 1
    assert diff.first_mismatch is not None
    assert "vs" in diff.first_mismatch  # "a vs b" format


def test_rows_equivalent_order_independent():
    """Row order doesn't matter (sorted comparison)."""
    rows_a = [_ROW_A, _ROW_B]
    rows_b = [_ROW_B, _ROW_A]  # reversed
    equiv, diff = rows_equivalent(rows_a, rows_b)
    assert equiv is True
    assert diff.reason == "exact match"


# ---------------------------------------------------------------------------
# NaN / Inf / None distinction (critical safety test)
# ---------------------------------------------------------------------------

def test_rows_equivalent_nan_not_equal_null():
    """NaN in one side vs None in the other -> NOT equivalent (sentinel guard)."""
    rows_a = [{"x": float("nan")}]
    rows_b = [{"x": None}]
    equiv, diff = rows_equivalent(rows_a, rows_b)
    assert equiv is False, (
        "NaN and None must NOT be treated as equivalent — "
        "a rewrite that changes NaN-producing expressions to NULL is a semantic change"
    )


def test_rows_equivalent_nan_both_equal():
    """Both NaN in same position -> equivalent (sentinel identity)."""
    rows_a = [{"x": float("nan")}]
    rows_b = [{"x": float("nan")}]
    equiv, diff = rows_equivalent(rows_a, rows_b)
    assert equiv is True, "NaN == NaN should hold via sentinel identity"


def test_rows_equivalent_inf_not_equal_null():
    """Inf vs None -> NOT equivalent."""
    rows_a = [{"x": float("inf")}]
    rows_b = [{"x": None}]
    equiv, diff = rows_equivalent(rows_a, rows_b)
    assert equiv is False


def test_rows_equivalent_pos_inf_not_equal_neg_inf():
    """+Inf vs -Inf -> NOT equivalent."""
    rows_a = [{"x": float("inf")}]
    rows_b = [{"x": float("-inf")}]
    equiv, diff = rows_equivalent(rows_a, rows_b)
    assert equiv is False


# ---------------------------------------------------------------------------
# exclude_columns tests
# ---------------------------------------------------------------------------

def test_rows_equivalent_excludes_nondeterministic_column():
    """Rows differ ONLY at index 2 (timestamp); with exclude -> True; without -> False."""
    rows_a = [(1, "alice", "2026-01-01 00:00:00")]
    rows_b = [(1, "alice", "2026-01-02 12:00:00")]

    # Without exclusion: should differ
    equiv_without, _ = rows_equivalent(rows_a, rows_b)
    assert equiv_without is False, "rows with different timestamps should differ without exclusion"

    # With exclusion of column index 2: should be equivalent
    equiv_with, diff_with = rows_equivalent(rows_a, rows_b, exclude_columns=(2,))
    assert equiv_with is True, (
        f"rows should be equivalent when timestamp column (idx 2) is excluded; diff: {diff_with}"
    )


def test_exclude_out_of_range_index_ignored():
    """exclude_columns=(99,) on 3-col rows -> no crash, behaves like no exclusion."""
    rows_a = [(1, 2, 3)]
    rows_b = [(1, 2, 3)]
    # Should not raise
    equiv, diff = rows_equivalent(rows_a, rows_b, exclude_columns=(99,))
    assert equiv is True  # identical rows


def test_exclude_multiple_columns():
    """Multiple columns excluded; rows differ only in excluded positions -> True."""
    rows_a = [(1, "ts1", "uuid-a", 100)]
    rows_b = [(1, "ts2", "uuid-b", 100)]
    equiv, diff = rows_equivalent(rows_a, rows_b, exclude_columns=(1, 2))
    assert equiv is True


def test_exclude_ragged_rows_no_crash():
    """Ragged rows (shorter than excluded index) -> no IndexError."""
    rows_a = [(1,)]            # length 1
    rows_b = [(1,)]
    # Index 5 is out of range — must be silently ignored
    equiv, diff = rows_equivalent(rows_a, rows_b, exclude_columns=(5,))
    assert equiv is True


# ---------------------------------------------------------------------------
# excluded_columns field
# ---------------------------------------------------------------------------

def test_excluded_columns_field_records_actually_present():
    """excluded_columns in EquivDiff records indices actually present."""
    rows_a = [(1, 2, 3)]
    rows_b = [(1, 2, 9)]
    _, diff = rows_equivalent(rows_a, rows_b, exclude_columns=(2,))
    assert 2 in diff.excluded_columns, (
        f"index 2 should be in excluded_columns, got {diff.excluded_columns}"
    )


def test_excluded_columns_out_of_range_not_included():
    """Out-of-range indices should not appear in excluded_columns."""
    rows_a = [(1, 2)]
    rows_b = [(1, 2)]
    _, diff = rows_equivalent(rows_a, rows_b, exclude_columns=(99,))
    assert 99 not in diff.excluded_columns


# ---------------------------------------------------------------------------
# Backward-compat shim
# ---------------------------------------------------------------------------

def test_results_equivalent_shim_unchanged():
    """Legacy _results_equivalent returns same (bool, str) as before for fixed fixtures.

    Regression guard for the 4 call sites (lines 1242, 1283, 2175, 2301).
    Verbatim strings must match exactly.
    """
    # exact match
    rows = [{"id": 1, "v": 10}, {"id": 2, "v": 20}]
    ok, reason = _results_equivalent(rows, rows)
    assert ok is True
    assert reason == "exact match"

    # both empty
    ok, reason = _results_equivalent([], [])
    assert ok is True
    assert reason == "both empty"

    # row count differs
    ok, reason = _results_equivalent([{"id": 1}], [{"id": 1}, {"id": 2}])
    assert ok is False
    assert reason == "row count differs: 1 vs 2"

    # rows differ
    ok, reason = _results_equivalent([{"id": 1, "v": 10}], [{"id": 1, "v": 99}])
    assert ok is False
    assert reason == "1 row(s) differ"


def test_results_equivalent_shim_returns_str():
    """_results_equivalent must return a plain (bool, str) 2-tuple, not EquivDiff."""
    result = _results_equivalent([{"x": 1}], [{"x": 1}])
    assert len(result) == 2
    ok, reason = result
    assert isinstance(ok, bool)
    assert isinstance(reason, str)


# ---------------------------------------------------------------------------
# Dict row exclusion (the production Trino row format)
# ---------------------------------------------------------------------------

def test_rows_equivalent_dict_rows_exclude_by_position():
    """Dict rows: exclude_columns by insertion-order position works correctly.

    Trino MCP rows arrive as dicts.  Exclude index 2 (the timestamp column)
    so that rows differing ONLY in that column are accepted as equivalent.
    This tests the production MCP path.
    """
    rows_a = [{"id": 1, "name": "alice", "ts": "2026-01-01 00:00:00"}]
    rows_b = [{"id": 1, "name": "alice", "ts": "2026-01-02 12:00:00"}]

    # Without exclusion: should differ
    equiv_without, _ = rows_equivalent(rows_a, rows_b)
    assert equiv_without is False, "dict rows with different timestamps should differ without exclusion"

    # With exclusion of column index 2 (ts): should be equivalent
    equiv_with, diff_with = rows_equivalent(rows_a, rows_b, exclude_columns=(2,))
    assert equiv_with is True, (
        f"dict rows should be equivalent when ts column (idx 2) is excluded; diff: {diff_with}"
    )
    assert 2 in diff_with.excluded_columns, (
        f"index 2 should appear in excluded_columns, got {diff_with.excluded_columns}"
    )


def test_rows_equivalent_dict_rows_exclude_out_of_range():
    """Dict rows: out-of-range exclude index is silently ignored, excluded_columns is ()."""
    rows_a = [{"id": 1, "v": 10}]  # 2 keys
    rows_b = [{"id": 1, "v": 10}]
    equiv, diff = rows_equivalent(rows_a, rows_b, exclude_columns=(99,))
    assert equiv is True
    assert 99 not in diff.excluded_columns
