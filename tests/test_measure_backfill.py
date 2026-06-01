"""The EXPLAIN ANALYZE stat-backfill must not kill a candidate via the candidate
timeout — it only enriches cpu/memory stats and is normally slower than the
plain run. Surfaced by live-cluster testing (fast metadata queries where the MCP
server returns rows but no server-side stats, so every candidate triggered the
backfill and timed out)."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from genie.skills.mcp_trino import research as mcp_research
from genie.skills.mcp_trino.research import RunMetrics, _measure_mcp
from genie.skills.mcp_trino.preflight import CandidateTimeoutError


def _exec_plain_ok_backfill_times_out(_client, sql, timeout_ms=None, label="candidate"):
    if sql.strip().upper().startswith("EXPLAIN ANALYZE"):
        # the stat-backfill re-run exceeds the candidate timeout
        raise CandidateTimeoutError(timeout_ms or 0, label)
    # plain run: rows present, but the server reports no structured stats
    # (cpu/memory == 0) → this is what triggers the backfill
    return {
        "rows": [{"a": 1}],
        "columns": ["a"],
        "row_count": 1,
        "metrics": RunMetrics(query_time_ms=500, cpu_time_ms=0, peak_memory_bytes=0),
        "error": None,
        "raw": "",
    }


def test_measure_mcp_keeps_plain_metrics_when_backfill_times_out():
    client = MagicMock()
    with patch.object(
        mcp_research, "_execute_via_mcp", side_effect=_exec_plain_ok_backfill_times_out
    ):
        # candidate timeout present; the plain run completes, only the backfill
        # would exceed it — the candidate must NOT be killed.
        result = _measure_mcp(
            client, "SELECT a FROM t", "query_time_ms", 1, timeout_ms=1000
        )

    assert result.median_metric == 500.0
    assert result.row_count == 1


def test_measure_mcp_plain_run_timeout_still_propagates():
    # If the PLAIN run itself times out (genuinely slower than baseline), that
    # must still kill the candidate — only the backfill is made non-fatal.
    client = MagicMock()

    def _plain_times_out(_client, sql, timeout_ms=None, label="candidate"):
        raise CandidateTimeoutError(timeout_ms or 0, label)

    with patch.object(mcp_research, "_execute_via_mcp", side_effect=_plain_times_out):
        try:
            _measure_mcp(client, "SELECT a FROM t", "query_time_ms", 1, timeout_ms=1000)
        except CandidateTimeoutError:
            pass
        else:  # pragma: no cover
            raise AssertionError("plain-run timeout should propagate")
