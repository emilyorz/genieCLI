"""Dual-path wiring tests for v29 T2 pre-execution diagnosis.

Asserts the ranked-directions block produced by
``pre_execution_diagnosis`` / ``format_directions_for_prompt`` is injected
into the optimizer system prompt on BOTH execution paths:

  - MCP default path:  ``run_mcp_enhancement``
  - ``--direct`` path: ``_run_optimization_loop``

Strategy: patch ``genie.session.manager.new_session`` (imported locally by
both functions) to capture the system prompt and short-circuit the run via a
sentinel exception, so we never touch the iteration loop, the provider, or a
live Trino server. The baseline is faked with peak_memory_bytes above the
1 GiB threshold so a ``memory-pressure`` direction is guaranteed regardless of
static findings or plan cost.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from genie.skills.mcp_trino.pre_execution_diagnosis import HIGH_PEAK_MEMORY_BYTES
from genie.skills.mcp_trino.research import MeasureResult, RunMetrics, ExplainAnalyzeResult
from genie.skills.trino_query import QueryMetrics


_DIAGNOSIS_HEADER = "Pre-execution diagnosis"
_HIGH_PEAK = HIGH_PEAK_MEMORY_BYTES + 1  # guarantees a memory-pressure direction


class _PromptCaptured(Exception):
    """Carries the captured system prompt out of new_session."""

    def __init__(self, sys_prompt: str):
        super().__init__("captured")
        self.sys_prompt = sys_prompt


def _capturing_new_session(sys_prompt: str):
    raise _PromptCaptured(sys_prompt)


# ---------------------------------------------------------------------------
# --direct path
# ---------------------------------------------------------------------------


def _fake_direct_baseline() -> dict:
    return {
        "median": 100.0,
        "samples": [100.0],
        "row_count": 5,  # > 0 → not a no-data path
        "rows": [(1,), (2,), (3,), (4,), (5,)],
        "metrics": QueryMetrics(wall_time_ms=1000, peak_memory_bytes=_HIGH_PEAK),
    }


def test_should_inject_directions_block_into_direct_path_prompt():
    from genie.skills.trino_query import research as direct_research

    output = MagicMock()

    with patch.object(direct_research, "_measure", return_value=_fake_direct_baseline()), \
         patch("genie.session.manager.new_session", side_effect=_capturing_new_session):
        with pytest.raises(_PromptCaptured) as exc:
            direct_research._run_optimization_loop(
                provider=MagicMock(),
                model="test-model",
                reasoning="disable",
                original_sql="SELECT 1",
                metric_key="wall_time_ms",
                max_iterations=3,
                verify_runs=1,
                output=output,
                build_prompt=lambda *a, **k: "SKILL_PROMPT_TEXT",
                explain_runner=None,  # no plan cost → diagnosis driven by peak memory
            )

    sys_prompt = exc.value.sys_prompt
    assert _DIAGNOSIS_HEADER in sys_prompt
    assert "memory-pressure" in sys_prompt
    # block must precede the skill prompt body
    assert sys_prompt.index(_DIAGNOSIS_HEADER) < sys_prompt.index("SKILL_PROMPT_TEXT")


# ---------------------------------------------------------------------------
# MCP default path
# ---------------------------------------------------------------------------


def _fake_mcp_baseline() -> MeasureResult:
    return MeasureResult(
        median_metric=100.0,
        samples=[100.0],
        row_count=5,  # > 0 → not a no-data path
        rows=[(1,)],
        columns=["c"],
        metrics=RunMetrics(wall_time_ms=1000.0, peak_memory_bytes=_HIGH_PEAK),
    )


def test_should_inject_directions_block_into_mcp_path_prompt():
    from genie.skills.mcp_trino import research as mcp_research

    output = MagicMock()
    client = MagicMock()

    with patch.object(mcp_research, "_measure_mcp", return_value=_fake_mcp_baseline()), \
         patch.object(
             mcp_research, "_fetch_explain_analyze",
             return_value=ExplainAnalyzeResult(raw_text="", available=False),
         ), \
         patch("genie.session.manager.new_session", side_effect=_capturing_new_session):
        with pytest.raises(_PromptCaptured) as exc:
            mcp_research.run_mcp_enhancement(
                client=client,
                sql="SELECT 1",  # no qualified tables → no metadata round-trip
                metric_key="wall_time_ms",
                max_iterations=3,
                verify_runs=1,
                provider=MagicMock(),
                model="test-model",
                reasoning="disable",
                output=output,
                build_prompt=lambda *a, **k: "SKILL_PROMPT_TEXT",
            )

    sys_prompt = exc.value.sys_prompt
    assert _DIAGNOSIS_HEADER in sys_prompt
    assert "memory-pressure" in sys_prompt
    assert sys_prompt.index(_DIAGNOSIS_HEADER) < sys_prompt.index("SKILL_PROMPT_TEXT")


# ---------------------------------------------------------------------------
# symmetry — both paths inject the same header so neither silently no-ops
# ---------------------------------------------------------------------------


def test_should_inject_same_diagnosis_header_on_both_paths():
    from genie.skills.mcp_trino import research as mcp_research
    from genie.skills.trino_query import research as direct_research

    direct_prompt = {}
    mcp_prompt = {}

    with patch.object(direct_research, "_measure", return_value=_fake_direct_baseline()), \
         patch("genie.session.manager.new_session", side_effect=_capturing_new_session):
        with pytest.raises(_PromptCaptured) as d_exc:
            direct_research._run_optimization_loop(
                provider=MagicMock(),
                model="m",
                reasoning="disable",
                original_sql="SELECT 1",
                metric_key="wall_time_ms",
                max_iterations=2,
                verify_runs=1,
                output=MagicMock(),
                build_prompt=lambda *a, **k: "SKILL",
                explain_runner=None,
            )
    direct_prompt["v"] = d_exc.value.sys_prompt

    with patch.object(mcp_research, "_measure_mcp", return_value=_fake_mcp_baseline()), \
         patch.object(
             mcp_research, "_fetch_explain_analyze",
             return_value=ExplainAnalyzeResult(raw_text="", available=False),
         ), \
         patch("genie.session.manager.new_session", side_effect=_capturing_new_session):
        with pytest.raises(_PromptCaptured) as m_exc:
            mcp_research.run_mcp_enhancement(
                client=MagicMock(),
                sql="SELECT 1",
                metric_key="wall_time_ms",
                max_iterations=2,
                verify_runs=1,
                provider=MagicMock(),
                model="m",
                reasoning="disable",
                output=MagicMock(),
                build_prompt=lambda *a, **k: "SKILL",
            )
    mcp_prompt["v"] = m_exc.value.sys_prompt

    assert _DIAGNOSIS_HEADER in direct_prompt["v"]
    assert _DIAGNOSIS_HEADER in mcp_prompt["v"]
