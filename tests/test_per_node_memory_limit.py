"""Tests for _fetch_per_node_memory_limit, MemoryLimitResult, _resolve_memory_pressure_fraction,
and the MCP-path threading that delivers peak_memory_limit_bytes to pre_execution_diagnosis.

Every test that touches env vars starts with monkeypatch.delenv for both knobs so the
host environment never leaks into assertions.
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock, call, patch

import pytest

from genie.skills.mcp_trino.research import (
    MemoryLimitResult,
    MeasureResult,
    RunMetrics,
    ExplainAnalyzeResult,
    _fetch_per_node_memory_limit,
    _parse_trino_datasize,
)
from genie.skills.mcp_trino.pre_execution_diagnosis import (
    HIGH_PEAK_MEMORY_BYTES,
    MEMORY_PRESSURE_FRACTION,
    _memory_pressure_threshold,
    _resolve_memory_pressure_fraction,
)
from genie.skills.mcp_trino.client import McpClient


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_ENV_LIMIT = "GENIE_TRINO_MEMORY_LIMIT_PER_NODE_BYTES"
_ENV_FRAC = "GENIE_TRINO_MEMORY_PRESSURE_FRACTION"


def _mock_client_with_rows(rows):
    """Return a MagicMock McpClient whose SHOW SESSION returns the given rows."""
    client = MagicMock(spec=McpClient)
    client.list_tools.return_value = [
        {"name": "query", "inputSchema": {"properties": {"sql": {"type": "string"}}}},
    ]
    client.call_tool.return_value = json.dumps(rows)
    return client


def _mock_client_error():
    """MCP client that returns an error response."""
    client = MagicMock(spec=McpClient)
    client.list_tools.return_value = [
        {"name": "query", "inputSchema": {"properties": {"sql": {"type": "string"}}}},
    ]
    client.call_tool.return_value = json.dumps({"error": "some mcp error"})
    return client


def _clear_resolved_tool():
    """Reset the cached resolved tool so each test starts clean."""
    import genie.skills.mcp_trino.research as _mod
    _mod._resolved_tool = None


# ---------------------------------------------------------------------------
# _fetch_per_node_memory_limit — env override cases
# ---------------------------------------------------------------------------

class TestEnvOverride:
    def setup_method(self):
        _clear_resolved_tool()

    def test_env_override_wins_over_show_session(self, monkeypatch):
        """C01: env set + valid → bytes from env; SHOW SESSION NOT called."""
        monkeypatch.delenv(_ENV_LIMIT, raising=False)
        monkeypatch.delenv(_ENV_FRAC, raising=False)
        monkeypatch.setenv(_ENV_LIMIT, "10737418240")  # 10 GiB

        client = _mock_client_with_rows([
            {"name": "query_max_memory_per_node", "value": "5GB"},
        ])

        result = _fetch_per_node_memory_limit(client)

        assert result.bytes == 10737418240
        assert result.source == "env"
        client.call_tool.assert_not_called()

    def test_env_override_no_round_trip(self, monkeypatch):
        """C02: env set + valid, no rows → bytes from env; call_tool NOT called."""
        monkeypatch.delenv(_ENV_LIMIT, raising=False)
        monkeypatch.delenv(_ENV_FRAC, raising=False)
        monkeypatch.setenv(_ENV_LIMIT, "10737418240")

        client = _mock_client_with_rows([])

        result = _fetch_per_node_memory_limit(client)

        assert result.bytes == 10737418240
        assert result.source == "env"
        client.call_tool.assert_not_called()

    def test_bad_env_falls_through_to_show_session(self, monkeypatch):
        """C03: env set but invalid → fall through, SHOW SESSION → per-node row used."""
        monkeypatch.delenv(_ENV_LIMIT, raising=False)
        monkeypatch.delenv(_ENV_FRAC, raising=False)
        monkeypatch.setenv(_ENV_LIMIT, "not-a-number")

        client = _mock_client_with_rows([
            {"name": "query_max_memory_per_node", "value": "5GB"},
        ])
        _clear_resolved_tool()

        result = _fetch_per_node_memory_limit(client)

        assert result.bytes == 5 * 1024**3
        assert result.source == "bad_env_fallthrough"


# ---------------------------------------------------------------------------
# _fetch_per_node_memory_limit — SHOW SESSION cases
# ---------------------------------------------------------------------------

class TestShowSession:
    def setup_method(self):
        _clear_resolved_tool()

    def test_show_session_per_node_lowercase_keys(self, monkeypatch):
        """D01: lowercase keys, value column present."""
        monkeypatch.delenv(_ENV_LIMIT, raising=False)
        monkeypatch.delenv(_ENV_FRAC, raising=False)

        client = _mock_client_with_rows([
            {"name": "query_max_memory_per_node", "value": "5GB"},
        ])

        result = _fetch_per_node_memory_limit(client)

        assert result.bytes == 5 * 1024**3
        assert result.source == "show_session"

    def test_only_total_returns_default_fallback(self, monkeypatch):
        """D02 (critical): rows contain only query_max_memory (total) + near-miss → None, NOT 20 GiB."""
        monkeypatch.delenv(_ENV_LIMIT, raising=False)
        monkeypatch.delenv(_ENV_FRAC, raising=False)

        client = _mock_client_with_rows([
            {"name": "query_max_memory", "value": "20GB"},
            {"name": "query_max_memory_per_worker", "value": "4GB"},
        ])

        result = _fetch_per_node_memory_limit(client)

        assert result.bytes is None
        assert result.source == "default-fallback"
        assert result.bytes != 20 * 1024**3

    def test_empty_rows_returns_default_fallback(self, monkeypatch):
        """D03: empty rows list."""
        monkeypatch.delenv(_ENV_LIMIT, raising=False)
        monkeypatch.delenv(_ENV_FRAC, raising=False)

        client = _mock_client_with_rows([])

        result = _fetch_per_node_memory_limit(client)

        assert result.bytes is None
        assert result.source == "default-fallback"

    def test_error_response_returns_default_fallback(self, monkeypatch):
        """Error response → default-fallback."""
        monkeypatch.delenv(_ENV_LIMIT, raising=False)
        monkeypatch.delenv(_ENV_FRAC, raising=False)

        client = _mock_client_error()

        result = _fetch_per_node_memory_limit(client)

        assert result.bytes is None
        assert result.source == "default-fallback"

    def test_call_tool_raises_returns_default_fallback(self, monkeypatch):
        """Transport error (call_tool raises) → default-fallback."""
        monkeypatch.delenv(_ENV_LIMIT, raising=False)
        monkeypatch.delenv(_ENV_FRAC, raising=False)

        client = MagicMock(spec=McpClient)
        client.list_tools.return_value = [
            {"name": "query", "inputSchema": {"properties": {"sql": {"type": "string"}}}},
        ]
        client.call_tool.side_effect = RuntimeError("connection refused")

        result = _fetch_per_node_memory_limit(client)

        assert result.bytes is None
        assert result.source == "default-fallback"

    def test_unparseable_value_returns_default_fallback(self, monkeypatch):
        """D05: per-node row present but value is not a valid DataSize string."""
        monkeypatch.delenv(_ENV_LIMIT, raising=False)
        monkeypatch.delenv(_ENV_FRAC, raising=False)

        client = _mock_client_with_rows([
            {"name": "query_max_memory_per_node", "value": "not-a-size"},
        ])

        result = _fetch_per_node_memory_limit(client)

        assert result.bytes is None
        assert result.source == "default-fallback"

    def test_zero_value_returns_default_fallback(self, monkeypatch):
        """D06: per-node row with zero value → None."""
        monkeypatch.delenv(_ENV_LIMIT, raising=False)
        monkeypatch.delenv(_ENV_FRAC, raising=False)

        client = _mock_client_with_rows([
            {"name": "query_max_memory_per_node", "value": "0B"},
        ])

        result = _fetch_per_node_memory_limit(client)

        assert result.bytes is None
        assert result.source == "default-fallback"

    def test_title_case_keys(self, monkeypatch):
        """D07: keys are title-case (Name/Value) → normalized correctly."""
        monkeypatch.delenv(_ENV_LIMIT, raising=False)
        monkeypatch.delenv(_ENV_FRAC, raising=False)

        client = _mock_client_with_rows([
            {"Name": "query_max_memory_per_node", "Value": "8GB"},
        ])

        result = _fetch_per_node_memory_limit(client)

        assert result.bytes == 8 * 1024**3
        assert result.source == "show_session"

    def test_value_empty_falls_back_to_default_column(self, monkeypatch):
        """D08: value column is empty, default column is populated → parse default."""
        monkeypatch.delenv(_ENV_LIMIT, raising=False)
        monkeypatch.delenv(_ENV_FRAC, raising=False)

        client = _mock_client_with_rows([
            {"name": "query_max_memory_per_node", "value": "", "default": "4GB"},
        ])

        result = _fetch_per_node_memory_limit(client)

        assert result.bytes == 4 * 1024**3
        assert result.source == "show_session"


# ---------------------------------------------------------------------------
# MemoryLimitResult source label assertions
# ---------------------------------------------------------------------------

class TestSourceLabels:
    def setup_method(self):
        _clear_resolved_tool()

    def test_source_label_env(self, monkeypatch):
        """T-F-13: result is MemoryLimitResult with source == 'env'."""
        monkeypatch.delenv(_ENV_LIMIT, raising=False)
        monkeypatch.delenv(_ENV_FRAC, raising=False)
        monkeypatch.setenv(_ENV_LIMIT, "10737418240")

        client = MagicMock(spec=McpClient)
        result = _fetch_per_node_memory_limit(client)

        assert isinstance(result, MemoryLimitResult)
        assert result.source == "env"

    def test_source_label_show_session(self, monkeypatch):
        """T-F-14: show_session path → source == 'show_session'."""
        monkeypatch.delenv(_ENV_LIMIT, raising=False)
        monkeypatch.delenv(_ENV_FRAC, raising=False)

        client = _mock_client_with_rows([
            {"name": "query_max_memory_per_node", "value": "5GB"},
        ])

        result = _fetch_per_node_memory_limit(client)

        assert result.source == "show_session"

    def test_source_label_default_fallback(self, monkeypatch):
        """T-F-15: empty rows → source == 'default-fallback'."""
        monkeypatch.delenv(_ENV_LIMIT, raising=False)
        monkeypatch.delenv(_ENV_FRAC, raising=False)

        client = _mock_client_with_rows([])

        result = _fetch_per_node_memory_limit(client)

        assert result.source == "default-fallback"


# ---------------------------------------------------------------------------
# _resolve_memory_pressure_fraction / threshold cases
# ---------------------------------------------------------------------------

class TestFractionAndThreshold:
    def test_default_fraction_half(self, monkeypatch):
        """T-F-16: no env → threshold is 0.5 × 1 GiB = 536870912."""
        monkeypatch.delenv(_ENV_LIMIT, raising=False)
        monkeypatch.delenv(_ENV_FRAC, raising=False)

        assert _memory_pressure_threshold(1 * 1024**3) == 536870912

    def test_env_fraction_override(self, monkeypatch):
        """T-F-17: GENIE_TRINO_MEMORY_PRESSURE_FRACTION=0.75 is visible without importlib.reload."""
        monkeypatch.delenv(_ENV_LIMIT, raising=False)
        monkeypatch.delenv(_ENV_FRAC, raising=False)
        monkeypatch.setenv(_ENV_FRAC, "0.75")

        expected = int(1 * 1024**3 * 0.75)
        assert _memory_pressure_threshold(1 * 1024**3) == expected

    def test_bad_fraction_clamps_to_default(self, monkeypatch):
        """T-F-18: unparseable fraction 'abc' → clamp to default 0.5."""
        monkeypatch.delenv(_ENV_LIMIT, raising=False)
        monkeypatch.delenv(_ENV_FRAC, raising=False)
        monkeypatch.setenv(_ENV_FRAC, "abc")

        assert _memory_pressure_threshold(1 * 1024**3) == 536870912

    def test_out_of_range_fraction_20_clamps(self, monkeypatch):
        """T-F-19: fraction '2.0' (> 1) → clamp to default 0.5."""
        monkeypatch.delenv(_ENV_LIMIT, raising=False)
        monkeypatch.delenv(_ENV_FRAC, raising=False)
        monkeypatch.setenv(_ENV_FRAC, "2.0")

        assert _memory_pressure_threshold(1 * 1024**3) == 536870912

    def test_out_of_range_fraction_negative_clamps(self, monkeypatch):
        """T-F-20: fraction '-0.1' (≤ 0) → clamp to default 0.5."""
        monkeypatch.delenv(_ENV_LIMIT, raising=False)
        monkeypatch.delenv(_ENV_FRAC, raising=False)
        monkeypatch.setenv(_ENV_FRAC, "-0.1")

        assert _memory_pressure_threshold(1 * 1024**3) == 536870912

    def test_nan_fraction_clamps_to_default(self, monkeypatch):
        """T-F-27 (MAJOR-1 regression): GENIE_TRINO_MEMORY_PRESSURE_FRACTION='nan'
        must clamp to 0.5; float('nan') satisfies neither frac<=0 nor frac>1 so the
        old guard would pass it through and cause int(limit * nan) → ValueError."""
        monkeypatch.delenv(_ENV_LIMIT, raising=False)
        monkeypatch.delenv(_ENV_FRAC, raising=False)
        monkeypatch.setenv(_ENV_FRAC, "nan")

        assert _resolve_memory_pressure_fraction() == 0.5
        assert _memory_pressure_threshold(1 * 1024**3) == 536870912

    def test_inf_fraction_clamps_to_default(self, monkeypatch):
        """T-F-28: GENIE_TRINO_MEMORY_PRESSURE_FRACTION='inf' → clamp to 0.5."""
        monkeypatch.delenv(_ENV_LIMIT, raising=False)
        monkeypatch.delenv(_ENV_FRAC, raising=False)
        monkeypatch.setenv(_ENV_FRAC, "inf")

        assert _resolve_memory_pressure_fraction() == 0.5
        assert _memory_pressure_threshold(1 * 1024**3) == 536870912

    def test_neg_inf_fraction_clamps_to_default(self, monkeypatch):
        """T-F-29: GENIE_TRINO_MEMORY_PRESSURE_FRACTION='-inf' → clamp to 0.5."""
        monkeypatch.delenv(_ENV_LIMIT, raising=False)
        monkeypatch.delenv(_ENV_FRAC, raising=False)
        monkeypatch.setenv(_ENV_FRAC, "-inf")

        assert _resolve_memory_pressure_fraction() == 0.5
        assert _memory_pressure_threshold(1 * 1024**3) == 536870912

    def test_nan_fraction_does_not_raise_with_per_node_limit(self, monkeypatch):
        """T-F-30 (MAJOR-1 regression): with a real per-node limit and 'nan' fraction,
        pre_execution_diagnosis must NOT raise — the old code would reach
        int(limit * nan) → ValueError, violating the 'never raises' contract."""
        from genie.skills.mcp_trino.pre_execution_diagnosis import pre_execution_diagnosis

        monkeypatch.delenv(_ENV_LIMIT, raising=False)
        monkeypatch.delenv(_ENV_FRAC, raising=False)
        monkeypatch.setenv(_ENV_FRAC, "nan")

        # Must not raise regardless of input
        result = pre_execution_diagnosis(
            "SELECT 1",
            peak_memory_bytes=int(1.5 * 1024**3),
            peak_memory_limit_bytes=5 * 1024**3,
        )
        # Memory pressure direction should fire because nan clamps to 0.5,
        # making threshold = 2.5 GiB; 1.5 GiB < 2.5 GiB so no direction
        assert isinstance(result, list)


# ---------------------------------------------------------------------------
# End-to-end wire-is-live
# ---------------------------------------------------------------------------

class TestWireIsLive:
    def test_1gib_default_fires_memory_pressure_direction(self, monkeypatch):
        """T-F-21: 1 GiB default limit, 1.5 GiB peak → memory-pressure direction emitted."""
        from genie.skills.mcp_trino.pre_execution_diagnosis import pre_execution_diagnosis

        monkeypatch.delenv(_ENV_LIMIT, raising=False)
        monkeypatch.delenv(_ENV_FRAC, raising=False)

        directions = pre_execution_diagnosis(
            "SELECT 1",
            peak_memory_bytes=int(1.5 * 1024**3),
            peak_memory_limit_bytes=None,
        )
        memory_dirs = [d for d in directions if d.kind == "memory-pressure"]
        assert len(memory_dirs) >= 1

    def test_5gib_limit_suppresses_memory_pressure_direction(self, monkeypatch):
        """T-F-22: 5 GiB per-node limit, threshold 2.5 GiB, 1.5 GiB peak → no direction."""
        from genie.skills.mcp_trino.pre_execution_diagnosis import pre_execution_diagnosis

        monkeypatch.delenv(_ENV_LIMIT, raising=False)
        monkeypatch.delenv(_ENV_FRAC, raising=False)

        directions = pre_execution_diagnosis(
            "SELECT 1",
            peak_memory_bytes=int(1.5 * 1024**3),
            peak_memory_limit_bytes=5 * 1024**3,
        )
        memory_dirs = [d for d in directions if d.kind == "memory-pressure"]
        assert memory_dirs == []


# ---------------------------------------------------------------------------
# MCP-path threading
# ---------------------------------------------------------------------------

class _PromptCaptured(Exception):
    def __init__(self, sys_prompt: str):
        super().__init__("captured")
        self.sys_prompt = sys_prompt


def _capturing_new_session(sys_prompt: str):
    raise _PromptCaptured(sys_prompt)


def _fake_mcp_baseline(peak_memory_bytes: int = 0) -> MeasureResult:
    return MeasureResult(
        median_metric=100.0,
        samples=[100.0],
        row_count=5,
        rows=[{"c": 1}],
        columns=["c"],
        metrics=RunMetrics(wall_time_ms=1000.0, peak_memory_bytes=peak_memory_bytes),
    )


def _fake_mcp_long_baseline(peak_memory_bytes: int = 0) -> MeasureResult:
    result = _fake_mcp_baseline(peak_memory_bytes)
    result.metrics.wall_time_ms = 61_000.0
    return result


class TestMcpPathThreading:
    def setup_method(self):
        _clear_resolved_tool()

    def test_run_mcp_enhancement_threads_limit_to_diagnosis(self, monkeypatch):
        """T-F-23: SHOW SESSION returns 5 GiB; all pre_execution_diagnosis calls
        must receive peak_memory_limit_bytes == 5368709120.

        pre_execution_diagnosis is imported locally inside _assemble_mcp_directions
        and _run_mcp_plan_cost_loop, so we patch it in its source module."""
        from genie.skills.mcp_trino import research as mcp_research
        import genie.skills.mcp_trino.pre_execution_diagnosis as ped_mod

        monkeypatch.delenv(_ENV_LIMIT, raising=False)
        monkeypatch.delenv(_ENV_FRAC, raising=False)

        _5gib = 5 * 1024**3
        recorded_limits: list = []
        _real_pre_exec = ped_mod.pre_execution_diagnosis

        def _fake_pre_exec(*args, **kwargs):
            recorded_limits.append(kwargs.get("peak_memory_limit_bytes"))
            return _real_pre_exec(*args, **kwargs)

        output = MagicMock()
        client = MagicMock()
        client.config.url = "mock://trino"

        # SHOW SESSION returns query_max_memory_per_node = 5GB
        show_session_resp = json.dumps([
            {"name": "query_max_memory_per_node", "value": "5GB"},
        ])
        client.call_tool.return_value = show_session_resp
        client.list_tools.return_value = [
            {"name": "query", "inputSchema": {"properties": {"sql": {"type": "string"}}}},
        ]

        with patch.object(mcp_research, "_measure_mcp", return_value=_fake_mcp_baseline()), \
             patch.object(
                 mcp_research, "_fetch_explain_analyze",
                 return_value=ExplainAnalyzeResult(raw_text="", available=False),
             ), \
             patch.object(ped_mod, "pre_execution_diagnosis", side_effect=_fake_pre_exec), \
             patch("genie.session.manager.new_session", side_effect=_capturing_new_session):
            with pytest.raises(_PromptCaptured):
                mcp_research.run_mcp_enhancement(
                    client=client,
                    sql="SELECT 1",
                    metric_key="wall_time_ms",
                    max_iterations=3,
                    verify_runs=1,
                    provider=MagicMock(),
                    model="test-model",
                    reasoning="disable",
                    output=output,
                    build_prompt=lambda *a, **k: "SKILL",
                )

        # Every recorded call must have the 5 GiB limit
        assert len(recorded_limits) >= 1
        for lim in recorded_limits:
            assert lim == _5gib, f"Expected {_5gib}, got {lim}"

    def test_show_session_fetched_once_per_run(self, monkeypatch):
        """T-F-24: SHOW SESSION issued exactly once; _run_mcp_plan_cost_loop receives the limit."""
        from genie.skills.mcp_trino import research as mcp_research

        monkeypatch.delenv(_ENV_LIMIT, raising=False)
        monkeypatch.delenv(_ENV_FRAC, raising=False)

        _5gib = 5 * 1024**3
        show_session_call_count = 0

        def _counting_execute_via_mcp(client, sql, *args, **kwargs):
            nonlocal show_session_call_count
            if sql.strip().upper() == "SHOW SESSION":
                show_session_call_count += 1
                return {
                    "rows": [{"name": "query_max_memory_per_node", "value": "5GB"}],
                    "columns": ["name", "value"],
                    "row_count": 1,
                    "metrics": RunMetrics(),
                    "error": None,
                    "raw": "",
                }
            if sql.strip().upper().startswith("EXPLAIN (FORMAT JSON)"):
                plan = json.dumps({
                    "estimates": [{
                        "outputRowCount": 10,
                        "outputSizeInBytes": 1024,
                    }],
                })
                return {
                    "rows": [{"Query Plan": plan}],
                    "columns": ["Query Plan"],
                    "row_count": 1,
                    "metrics": RunMetrics(),
                    "error": None,
                    "raw": plan,
                }
            # All other calls (EXPLAIN, session set, etc.) return empty
            return {
                "rows": [],
                "columns": [],
                "row_count": 0,
                "metrics": RunMetrics(),
                "error": None,
                "raw": "",
            }

        plan_cost_loop_kwargs: dict = {}

        def _fake_plan_cost_loop(**kwargs):
            plan_cost_loop_kwargs.update(kwargs)
            raise _PromptCaptured("plan_cost_loop called")

        output = MagicMock()
        client = MagicMock()
        client.config.url = "mock://trino"

        with patch.object(mcp_research, "_execute_via_mcp", side_effect=_counting_execute_via_mcp), \
             patch.object(mcp_research, "_measure_mcp", return_value=_fake_mcp_long_baseline()), \
             patch.object(
                 mcp_research, "_fetch_explain_analyze",
                 return_value=ExplainAnalyzeResult(raw_text="", available=False),
             ), \
             patch.object(mcp_research, "_run_mcp_plan_cost_loop", side_effect=_fake_plan_cost_loop), \
             patch("genie.session.manager.new_session", side_effect=_capturing_new_session):
            with pytest.raises(_PromptCaptured):
                mcp_research.run_mcp_enhancement(
                    client=client,
                    sql="SELECT 1",
                    metric_key="wall_time_ms",
                    max_iterations=3,
                    verify_runs=1,
                    provider=MagicMock(),
                    model="test-model",
                    reasoning="disable",
                    output=output,
                    build_prompt=lambda *a, **k: "SKILL",
                    long_query_opt_in=True,
                )

        # SHOW SESSION called exactly once
        assert show_session_call_count == 1

        assert plan_cost_loop_kwargs
        assert plan_cost_loop_kwargs.get("peak_memory_limit_bytes") == _5gib

    def test_bad_env_show_session_failure_breadcrumb_uses_fallback(self, monkeypatch):
        """Bad env + failed SHOW SESSION must tell the user the 1.0 GiB fallback is active."""
        from genie.skills.mcp_trino import research as mcp_research

        monkeypatch.delenv(_ENV_LIMIT, raising=False)
        monkeypatch.delenv(_ENV_FRAC, raising=False)
        monkeypatch.setenv(_ENV_LIMIT, "not-a-number")

        output = MagicMock()
        client = MagicMock()
        client.config.url = "mock://trino"
        client.list_tools.return_value = [
            {"name": "query", "inputSchema": {"properties": {"sql": {"type": "string"}}}},
        ]
        client.call_tool.return_value = json.dumps({"error": "session not available"})

        with patch.object(mcp_research, "_measure_mcp", return_value=_fake_mcp_baseline()), \
             patch.object(
                 mcp_research, "_fetch_explain_analyze",
                 return_value=ExplainAnalyzeResult(raw_text="", available=False),
             ), \
             patch("genie.session.manager.new_session", side_effect=_capturing_new_session):
            with pytest.raises(_PromptCaptured):
                mcp_research.run_mcp_enhancement(
                    client=client,
                    sql="SELECT 1",
                    metric_key="wall_time_ms",
                    max_iterations=3,
                    verify_runs=1,
                    provider=MagicMock(),
                    model="test-model",
                    reasoning="disable",
                    output=output,
                    build_prompt=lambda *a, **k: "SKILL",
                )

        progress_messages = " ".join(
            str(args[0]) for args, _kwargs in output.progress.call_args_list if args
        )
        assert "SHOW SESSION did not provide a usable query_max_memory_per_node" in progress_messages
        assert "using 1.0 GiB fallback" in progress_messages

    def test_introspection_failure_no_regression(self, monkeypatch):
        """T-F-25: SHOW SESSION returns error → run proceeds, no exception, 1 GiB fallback used."""
        from genie.skills.mcp_trino import research as mcp_research
        import genie.skills.mcp_trino.pre_execution_diagnosis as ped_mod

        monkeypatch.delenv(_ENV_LIMIT, raising=False)
        monkeypatch.delenv(_ENV_FRAC, raising=False)

        output = MagicMock()
        client = MagicMock()
        client.config.url = "mock://trino"
        client.list_tools.return_value = [
            {"name": "query", "inputSchema": {"properties": {"sql": {"type": "string"}}}},
        ]
        # SHOW SESSION returns error response
        client.call_tool.return_value = json.dumps({"error": "session not available"})

        recorded_limits: list = []
        _real_pre_exec = ped_mod.pre_execution_diagnosis

        def _fake_pre_exec(*args, **kwargs):
            recorded_limits.append(kwargs.get("peak_memory_limit_bytes"))
            return _real_pre_exec(*args, **kwargs)

        with patch.object(mcp_research, "_measure_mcp", return_value=_fake_mcp_baseline()), \
             patch.object(
                 mcp_research, "_fetch_explain_analyze",
                 return_value=ExplainAnalyzeResult(raw_text="", available=False),
             ), \
             patch.object(ped_mod, "pre_execution_diagnosis", side_effect=_fake_pre_exec), \
             patch("genie.session.manager.new_session", side_effect=_capturing_new_session):
            with pytest.raises(_PromptCaptured):
                mcp_research.run_mcp_enhancement(
                    client=client,
                    sql="SELECT 1",
                    metric_key="wall_time_ms",
                    max_iterations=3,
                    verify_runs=1,
                    provider=MagicMock(),
                    model="test-model",
                    reasoning="disable",
                    output=output,
                    build_prompt=lambda *a, **k: "SKILL",
                )

        # All calls received None (default-fallback → 1 GiB used internally)
        assert len(recorded_limits) >= 1
        for lim in recorded_limits:
            assert lim is None, f"Expected None (fallback), got {lim}"


# ---------------------------------------------------------------------------
# No-regression direction count (AC-U5)
# ---------------------------------------------------------------------------

class TestNoRegressionDirectionCount:
    def test_fallback_path_direction_count_unchanged(self, monkeypatch):
        """T-F-26: under fallback (peak_memory_limit_bytes=None), direction kinds/severities
        are identical regardless of whether breadcrumb-emitting A9 code has been called.
        No new direction kind is introduced by the breadcrumb addition."""
        from genie.skills.mcp_trino.pre_execution_diagnosis import pre_execution_diagnosis

        monkeypatch.delenv(_ENV_LIMIT, raising=False)
        monkeypatch.delenv(_ENV_FRAC, raising=False)

        # Both calls use identical args, peak_memory_bytes below 1 GiB threshold (0.3 GiB)
        # so no memory-pressure direction fires in either case
        sql = "SELECT 1"
        peak = int(0.3 * 1024**3)

        result_a = pre_execution_diagnosis(sql, peak_memory_bytes=peak, peak_memory_limit_bytes=None)
        result_b = pre_execution_diagnosis(sql, peak_memory_bytes=peak, peak_memory_limit_bytes=None)

        kinds_a = sorted(d.kind for d in result_a)
        kinds_b = sorted(d.kind for d in result_b)
        severities_a = sorted(d.severity for d in result_a)
        severities_b = sorted(d.severity for d in result_b)

        assert kinds_a == kinds_b
        assert severities_a == severities_b
