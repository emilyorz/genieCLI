"""Tests for MCP Trino research (autoresearch enhancement) module."""
from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from genie.skills.mcp_trino.client import McpClient, McpConfig
from genie.core.sql_extraction import extract_sql_from_reply as _extract_sql_from_reply
from genie.skills.mcp_trino.research import (
    ColumnInfo,
    EnhancementReport,
    ExplainAnalyzeResult,
    IterationRecord,
    MeasureResult,
    RunMetrics,
    TableMetadata,
    TableSuggestion,
    _execute_via_mcp,
    _extract_table_names,
    _fetch_explain_analyze,
    _fmt_metric_value,
    _generate_table_suggestions,
    _parse_explain_stages,
    _produce_decompose_candidate,
    _render_iteration_result,
    _render_plan_card,
    _render_sql_diff,
    _render_summary_card,
    _results_equivalent,
    generate_report,
    run_trino_research_via_mcp,
)


def test_mcp_plan_cost_decompose_seed_authorization_failure_persists_one_canonical_record(monkeypatch):
    """MCP plan-cost seed envelope provenance cannot replace baseline."""
    from genie.skills.mcp_trino.research import _run_mcp_plan_cost_loop

    monkeypatch.setenv("GENIE_V48_SEED_DECOMPOSE", "1")
    sql = "SELECT id FROM t"
    seed_sql = "SELECT id FROM t WHERE id > 0"
    baseline_metrics = RunMetrics(query_time_ms=80_000, cpu_time_ms=80_000, wall_time_ms=80_000)
    baseline = MeasureResult(
        median_metric=80_000.0, samples=[80_000.0], row_count=1,
        rows=[{"id": 1}], columns=["id"], metrics=baseline_metrics,
        capture_status="complete", completeness="unverified_received_envelope",
    )
    seed = MeasureResult(
        median_metric=10_000.0, samples=[10_000.0], row_count=1,
        rows=[{"id": 1}], columns=["id"],
        metrics=RunMetrics(query_time_ms=10_000, cpu_time_ms=10_000, wall_time_ms=10_000),
        capture_status="complete", completeness="unverified_received_envelope",
    )
    client = MagicMock()
    client.config = McpConfig(url="http://mcp.test/mcp", enabled=True, timeout=1)
    plan = json.dumps({
        "name": "TableScan[hive.default.t]", "descriptor": {"table": "hive.default.t"},
        "estimates": [{"outputRowCount": 100, "outputSizeInBytes": 10}], "children": [],
    })

    with patch("genie.skills.mcp_trino.write_analysis._make_advisory_llm_fn", return_value=MagicMock()), \
         patch("genie.skills.mcp_trino.research._produce_decompose_candidate",
               return_value=(seed_sql, [], [], None)), \
         patch("genie.skills.mcp_trino.research._measure_mcp", return_value=seed):
        report = _run_mcp_plan_cost_loop(
            client=client, provider=MagicMock(), model="m", reasoning="default",
            original_sql=sql, metric_key="cpu_time_ms", max_iterations=0,
            verify_runs=1, output=_output_mock(), build_prompt=lambda *a, **kw: "",
            baseline=baseline, static_report=None, explain_runner=lambda _: plan,
            max_fallbacks=3,
        )

    rejections = [it for it in report.iterations
                  if it.status == "equivalence_unverified_incomplete_result"]
    assert report.enhanced_sql == sql
    assert report.enhanced_metrics is baseline.metrics
    assert len(rejections) == 1
    rejection = rejections[0]
    assert {
        "iteration": rejection.iteration, "status": rejection.status,
        "rejection_reason": rejection.rejection_reason,
        "metric": rejection.metric_value, "delta": rejection.delta,
        "base_sql": rejection.base_sql, "candidate_sql": rejection.candidate_sql,
        "baseline_capture_status": rejection.baseline_capture_status,
        "candidate_capture_status": rejection.candidate_capture_status,
        "baseline_completeness": rejection.baseline_completeness,
        "candidate_completeness": rejection.candidate_completeness,
    } == {
        "iteration": 0, "status": "equivalence_unverified_incomplete_result",
        "rejection_reason": "both_upstream_completeness_unverified",
        "metric": 10_000.0, "delta": -70_000.0,
        "base_sql": sql, "candidate_sql": seed_sql,
        "baseline_capture_status": "complete", "candidate_capture_status": "complete",
        "baseline_completeness": "unverified_received_envelope",
        "candidate_completeness": "unverified_received_envelope",
    }


def test_iteration_record_accepts_canonical_incomplete_history_metric():
    """Canonical persisted history maps ``metric`` to ``metric_value``."""
    from genie.skills.mcp_trino.research import (
        IterationRecord, MeasureResult, RunMetrics, _incomplete_history,
    )

    baseline = MeasureResult(
        median_metric=80.0, samples=[80.0], row_count=1, rows=[{"id": 1}],
        columns=["id"], metrics=RunMetrics(), capture_status="complete",
        completeness="unverified_received_envelope",
    )
    candidate = MeasureResult(
        median_metric=10.0, samples=[10.0], row_count=1, rows=[{"id": 1}],
        columns=["id"], metrics=RunMetrics(), capture_status="complete",
        completeness="unverified_received_envelope",
    )
    history = _incomplete_history(
        iteration=1, baseline=baseline, candidate=candidate,
        base_sql="SELECT id FROM t", candidate_sql="SELECT id FROM t WHERE id > 0",
        metric=10.0, delta=-70.0,
    )

    record = IterationRecord.from_history(
        history, hypothesis=history["rejection_reason"], sql=history["candidate_sql"]
    )

    assert record.metric_value == 10.0
    assert record.delta == -70.0
    assert record.status == "equivalence_unverified_incomplete_result"


def test_mcp_plan_cost_incomplete_failure_persists_full_history_and_keeps_baseline(monkeypatch):
    """MCP envelopes remain unverified, even when their local rows match."""
    from genie.skills.mcp_trino.research import _run_mcp_plan_cost_loop

    monkeypatch.setenv("GENIE_V48_SEED_DECOMPOSE", "0")
    sql = "SELECT id FROM t"
    candidate_sql = "SELECT id FROM t WHERE id > 0"
    metrics = RunMetrics(query_time_ms=80_000, cpu_time_ms=80_000, wall_time_ms=80_000)
    baseline = MeasureResult(
        median_metric=80_000.0, samples=[80_000.0], row_count=1,
        rows=[{"id": 1}], columns=["id"], metrics=metrics,
        capture_status="complete", completeness="unverified_received_envelope",
    )
    candidate = MeasureResult(
        median_metric=10_000.0, samples=[10_000.0], row_count=1,
        rows=[{"id": 1}], columns=["id"],
        metrics=RunMetrics(query_time_ms=10_000, cpu_time_ms=10_000, wall_time_ms=10_000),
        capture_status="complete", completeness="unverified_received_envelope",
    )
    client = MagicMock()
    client.config = McpConfig(url="http://mcp.test/mcp", enabled=True, timeout=1)
    plan = json.dumps({
        "name": "TableScan[hive.default.t]", "descriptor": {"table": "hive.default.t"},
        "estimates": [{"outputRowCount": 100, "outputSizeInBytes": 10}], "children": [],
    })
    candidate_plan = json.dumps({
        "name": "TableScan[hive.default.t]", "descriptor": {"table": "hive.default.t"},
        "estimates": [{"outputRowCount": 10, "outputSizeInBytes": 10}], "children": [],
    })
    provider = MagicMock()
    provider.complete_text.return_value = f"```sql\n{candidate_sql}\n```"
    with patch("genie.skills.mcp_trino.research._measure_mcp", return_value=candidate):
        report = _run_mcp_plan_cost_loop(
            client=client, provider=provider, model="m", reasoning="default",
            original_sql=sql, metric_key="cpu_time_ms", max_iterations=1,
            verify_runs=1, output=_output_mock(), build_prompt=lambda *a, **kw: "",
            baseline=baseline, static_report=None,
            explain_runner=lambda statement: plan if statement == sql else candidate_plan,
            max_fallbacks=3,
        )

    assert report.enhanced_sql == sql
    assert report.data_consistent is False
    rejection = next(it for it in report.iterations if it.status == "equivalence_unverified_incomplete_result")
    assert {
        "iteration": rejection.iteration, "status": rejection.status,
        "rejection_reason": rejection.rejection_reason,
        "metric": rejection.metric_value, "delta": rejection.delta,
        "base_sql": rejection.base_sql, "candidate_sql": rejection.candidate_sql,
        "baseline_capture_status": rejection.baseline_capture_status,
        "candidate_capture_status": rejection.candidate_capture_status,
        "baseline_completeness": rejection.baseline_completeness,
        "candidate_completeness": rejection.candidate_completeness,
    } == {
        "iteration": 1, "status": "equivalence_unverified_incomplete_result",
        "rejection_reason": "both_upstream_completeness_unverified",
        "metric": 10_000.0, "delta": -70_000.0,
        "base_sql": sql, "candidate_sql": candidate_sql,
        "baseline_capture_status": "complete", "candidate_capture_status": "complete",
        "baseline_completeness": "unverified_received_envelope",
        "candidate_completeness": "unverified_received_envelope",
    }


def test_mcp_plan_cost_mixed_ranking_and_rejection_history_keeps_measurement_provenance(monkeypatch):
    """Compact ranking entries render n/a while canonical rejections keep facts."""
    from genie.skills.mcp_trino.research import _run_mcp_plan_cost_loop

    monkeypatch.setenv("GENIE_V48_SEED_DECOMPOSE", "0")
    sql = "SELECT id FROM t"
    candidate_sql = "SELECT id FROM t WHERE id > 0"
    baseline = MeasureResult(
        median_metric=80_000.0, samples=[80_000.0], row_count=1,
        rows=[{"id": 1}], columns=["id"],
        metrics=RunMetrics(query_time_ms=80_000, cpu_time_ms=80_000, wall_time_ms=80_000),
        capture_status="complete", completeness="unverified_received_envelope",
    )
    candidate = MeasureResult(
        median_metric=10_000.0, samples=[10_000.0], row_count=1,
        rows=[{"id": 1}], columns=["id"],
        metrics=RunMetrics(query_time_ms=10_000, cpu_time_ms=10_000, wall_time_ms=10_000),
        capture_status="complete", completeness="unverified_received_envelope",
    )
    client = MagicMock()
    client.config = McpConfig(url="http://mcp.test/mcp", enabled=True, timeout=1)
    plan = json.dumps({
        "name": "TableScan[hive.default.t]", "descriptor": {"table": "hive.default.t"},
        "estimates": [{"outputRowCount": 100, "outputSizeInBytes": 10}], "children": [],
    })
    candidate_plan = json.dumps({
        "name": "TableScan[hive.default.t]", "descriptor": {"table": "hive.default.t"},
        "estimates": [{"outputRowCount": 10, "outputSizeInBytes": 10}], "children": [],
    })
    provider = MagicMock()
    provider.complete_text.return_value = f"```sql\n{candidate_sql}\n```"

    with patch("genie.skills.mcp_trino.research._measure_mcp", return_value=candidate):
        report = _run_mcp_plan_cost_loop(
            client=client, provider=provider, model="m", reasoning="default",
            original_sql=sql, metric_key="cpu_time_ms", max_iterations=1,
            verify_runs=1, output=_output_mock(), build_prompt=lambda *a, **kw: "",
            baseline=baseline, static_report=None,
            explain_runner=lambda statement: plan if statement == sql else candidate_plan,
            max_fallbacks=3,
        )

    assert [(it.status, it.metric_value, it.delta) for it in report.iterations] == [
        ("plan_cost_better", None, None),
        ("equivalence_unverified_incomplete_result", 10_000.0, -70_000.0),
    ]
    rejection = report.iterations[1]
    assert rejection.rejection_reason == "both_upstream_completeness_unverified"
    assert rejection.base_sql == sql
    assert rejection.candidate_sql == candidate_sql
    assert rejection.baseline_capture_status == rejection.candidate_capture_status == "complete"
    assert (
        rejection.baseline_completeness == rejection.candidate_completeness
        == "unverified_received_envelope"
    )
    markdown = generate_report(report)
    assert "| 1 | plan_cost_better | n/a | n/a |" in markdown
    assert "| 1 | equivalence_unverified_incomplete_result | 10000.0 | -70000.0 |" in markdown


def test_mcp_measurement_capture_is_always_unverified_envelope(monkeypatch):
    """A locally complete MCP envelope never becomes verified Trino equivalence."""
    from genie.skills.mcp_trino.research import _measure_mcp, _incomplete_rejection_reason

    monkeypatch.setattr(
        "genie.skills.mcp_trino.research._execute_via_mcp",
        lambda *args, **kwargs: {
            "error": None, "metrics": RunMetrics(query_time_ms=1, cpu_time_ms=1, peak_memory_bytes=1),
            "row_count": 1, "rows": [{"id": 1}], "columns": ["id"],
        },
    )
    result = _measure_mcp(MagicMock(), "SELECT 1", "query_time_ms", 1, capture_rows=True)
    assert result.row_count == result.observed_row_count == 1
    assert result.capture_status == "complete"
    assert result.completeness == "unverified_received_envelope"
    assert _incomplete_rejection_reason(result, result) == "both_upstream_completeness_unverified"


# ── RunMetrics ───────────────────────────────────────────────────────────────


class TestRunMetrics:
    def test_summary(self):
        m = RunMetrics(query_time_ms=42.5, cpu_time_ms=30, wall_time_ms=45, processed_rows=100)
        s = m.summary()
        assert "query=42ms" in s or "query=43ms" in s
        assert "rows=100" in s


def _output_mock():
    output = MagicMock()
    output.print = MagicMock()
    output.progress = MagicMock()
    output.error = MagicMock()
    return output


def _read_write_analysis_report(tmp_path):
    return next((tmp_path / "report").glob("trino-research-write-analysis-*.md")).read_text()


@pytest.mark.parametrize("sql", ["SELECT * FROM source", "INSERT INTO audit_log SELECT * FROM source"])
def test_mcp_public_entry_rejects_invalid_safe_limit_before_advisory_provider_explain_baseline_or_loop(
    monkeypatch, sql
):
    """Supplied SQL validates policy before every MCP research/work surface."""
    output = _output_mock()
    provider = MagicMock()
    advisory = MagicMock(side_effect=AssertionError("write advisory must not run"))
    monkeypatch.setattr("genie.skills.mcp_trino.research.run_write_analysis_only", advisory)
    monkeypatch.setattr(
        "genie.skills.mcp_trino.research.load_mcp_config",
        MagicMock(side_effect=AssertionError("MCP config must not load")),
    )
    monkeypatch.setattr(
        "genie.skills.mcp_trino.research.McpClient",
        MagicMock(side_effect=AssertionError("MCP client must not construct")),
    )
    monkeypatch.setattr(
        "genie.skills.mcp_trino.preflight.run_preflight",
        MagicMock(side_effect=AssertionError("EXPLAIN must not run")),
    )
    monkeypatch.setattr(
        "genie.skills.mcp_trino.research.run_mcp_enhancement",
        MagicMock(side_effect=AssertionError("baseline/loop must not run")),
    )

    with pytest.raises(ValueError, match="safe_limit must be a positive integer or None"):
        run_trino_research_via_mcp(
            provider, {}, "test-model", "default", output, lambda *_: "",
            sql_text=sql, safe_limit=0, metric="query_time_ms", iterations=1, runs=1,
        )

    advisory.assert_not_called()
    provider.complete_text.assert_not_called()


def test_mcp_plan_cost_generated_write_never_reaches_tool_or_candidate_explain(monkeypatch):
    """MCP adapter keeps unsafe generated SQL outside tool and EXPLAIN calls."""
    from genie.skills.mcp_trino.research import _run_mcp_plan_cost_loop

    monkeypatch.setenv("GENIE_V48_SEED_DECOMPOSE", "0")
    original_sql = "SELECT id FROM source"
    candidate_sql = "DELETE FROM source WHERE id = 1"
    client = MagicMock()
    client.config = McpConfig(url="http://mcp.test/mcp", enabled=True, timeout=1)
    baseline = MeasureResult(
        median_metric=1.0, samples=[1.0], row_count=1, rows=[{"id": 1}],
        columns=["id"], metrics=RunMetrics(query_time_ms=1.0, wall_time_ms=1.0),
        capture_status="complete", completeness="unverified_received_envelope",
    )
    explained = []

    def explain_runner(sql):
        explained.append(sql)
        if sql != original_sql:
            raise AssertionError("unsafe candidate reached EXPLAIN")
        return json.dumps({
            "name": "TableScan[hive.default.source]",
            "estimates": [{"outputRowCount": 1, "outputSizeInBytes": 1}],
            "children": [],
        })

    with patch("genie.skills.mcp_trino.research._measure_mcp", side_effect=AssertionError("MCP tool must not run")):
        report = _run_mcp_plan_cost_loop(
            client=client,
            provider=MagicMock(complete_text=MagicMock(return_value=f"```sql\n{candidate_sql}\n```")),
            model="test-model", reasoning="default", original_sql=original_sql,
            metric_key="query_time_ms", max_iterations=1, verify_runs=1,
            output=_output_mock(), build_prompt=lambda *_: "", baseline=baseline,
            static_report=None, explain_runner=explain_runner, max_fallbacks=1,
        )

    assert report.enhanced_sql == original_sql
    assert candidate_sql not in explained


def test_mcp_write_analysis_skips_preflight_safe_limit_enhancement(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    output = _output_mock()
    provider = MagicMock()
    provider.complete_text.return_value = "Advisory: check insert target ownership."

    with patch("genie.skills.mcp_trino.research.load_mcp_config", side_effect=AssertionError("load_mcp_config called")), \
         patch("genie.skills.mcp_trino.research.McpClient", side_effect=AssertionError("McpClient constructed")), \
         patch("genie.skills.mcp_trino.research.run_mcp_enhancement", side_effect=AssertionError("enhancement called")), \
         patch("genie.skills.mcp_trino.research._execute_via_mcp", side_effect=AssertionError("execute called")), \
         patch("genie.skills.mcp_trino.research._measure_mcp", side_effect=AssertionError("measure called")), \
         patch("genie.skills.mcp_trino.research._build_mcp_explain_runner", side_effect=AssertionError("explain called")), \
         patch("genie.skills.mcp_trino.preflight.run_preflight", side_effect=AssertionError("preflight called")), \
         patch("genie.skills.mcp_trino.preflight.apply_safe_limit", side_effect=AssertionError("safe limit called")):
        run_trino_research_via_mcp(
            provider, {}, "test-model", "default", output, lambda *a, **kw: "",
            sql_text="INSERT INTO x SELECT * FROM t",
            safe_limit=100,
            diagnose_only=True,
            metric="query_time_ms",
            iterations=1,
            runs=1,
        )

    md = _read_write_analysis_report(tmp_path)
    assert "| Kind | insert |" in md
    assert "| SQL executed | no |" in md
    assert "advisory, unverified" in md
    # LLM is used for advisory (single-shot + v40 per-fragment decompose); invoked, count not pinned.
    provider.complete_text.assert_called()


def test_mcp_write_analysis_renders_suggested_sql_before_advisory_prose(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    output = _output_mock()
    provider = MagicMock()
    provider.complete_text.return_value = (
        "Use a pre-filtered source.\n"
        "```sql\n"
        "INSERT INTO target\n"
        "SELECT order_id\n"
        "FROM source\n"
        "```\n"
        "Then validate manually."
    )

    with patch("genie.skills.mcp_trino.research.load_mcp_config", side_effect=AssertionError("load_mcp_config called")), \
         patch("genie.skills.mcp_trino.research.McpClient", side_effect=AssertionError("McpClient constructed")), \
         patch("genie.skills.mcp_trino.research.run_mcp_enhancement", side_effect=AssertionError("enhancement called")), \
         patch("genie.skills.mcp_trino.research._execute_via_mcp", side_effect=AssertionError("execute called")), \
         patch("genie.skills.mcp_trino.research._measure_mcp", side_effect=AssertionError("measure called")), \
         patch("genie.skills.mcp_trino.research._build_mcp_explain_runner", side_effect=AssertionError("explain called")), \
         patch("genie.skills.mcp_trino.preflight.run_preflight", side_effect=AssertionError("preflight called")), \
         patch("genie.skills.mcp_trino.preflight.apply_safe_limit", side_effect=AssertionError("safe limit called")):
        run_trino_research_via_mcp(
            provider, {}, "test-model", "default", output, lambda *a, **kw: "",
            sql_text="INSERT INTO x SELECT * FROM t",
            metric="query_time_ms",
            iterations=1,
            runs=1,
        )

    md = _read_write_analysis_report(tmp_path)
    assert "## Suggested SQL Command (advisory, not executed)" in md
    assert md.index("## Suggested SQL Command (advisory, not executed)") < md.index("## Advisory Suggestions")
    assert "It was not executed, EXPLAINed, benchmarked, MCP-validated, or row-equivalence verified." in md
    assert "```sql\nINSERT INTO target\nSELECT order_id\nFROM source;\n```" in md
    assert "| SQL executed | no |" in md
    assert "| EXPLAIN run | no |" in md
    assert "| MCP/Trino reached | no |" in md
    assert "| Verified optimization | no |" in md


def test_mcp_write_analysis_uses_last_sql_fence_only(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    provider = MagicMock()
    provider.complete_text.return_value = (
        "Option A:\n```sql\nINSERT INTO a SELECT * FROM old_source\n```\n"
        "Option B:\n```sql\nINSERT INTO a SELECT * FROM new_source\n```\n"
        "Choose carefully."
    )

    with patch("genie.skills.mcp_trino.research.load_mcp_config", side_effect=AssertionError("load_mcp_config called")), \
         patch("genie.skills.mcp_trino.research.McpClient", side_effect=AssertionError("McpClient constructed")), \
         patch("genie.skills.mcp_trino.research.run_mcp_enhancement", side_effect=AssertionError("enhancement called")), \
         patch("genie.skills.mcp_trino.research._execute_via_mcp", side_effect=AssertionError("execute called")), \
         patch("genie.skills.mcp_trino.research._measure_mcp", side_effect=AssertionError("measure called")), \
         patch("genie.skills.mcp_trino.research._build_mcp_explain_runner", side_effect=AssertionError("explain called")), \
         patch("genie.skills.mcp_trino.preflight.run_preflight", side_effect=AssertionError("preflight called")), \
         patch("genie.skills.mcp_trino.preflight.apply_safe_limit", side_effect=AssertionError("safe limit called")):
        run_trino_research_via_mcp(
            provider, {}, "test-model", "default", _output_mock(), lambda *a, **kw: "",
            sql_text="INSERT INTO x SELECT * FROM t",
            metric="query_time_ms",
            iterations=1,
            runs=1,
        )

    md = _read_write_analysis_report(tmp_path)
    suggested_block = md.split("## Advisory Suggestions", 1)[0]
    assert "INSERT INTO a SELECT * FROM new_source;" in suggested_block
    assert "INSERT INTO a SELECT * FROM old_source;" not in suggested_block
    assert "Option A:" in md
    assert "Option B:" in md


def test_mcp_write_analysis_prose_only_is_complete_advisory_result(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    provider = MagicMock()
    provider.complete_text.return_value = "Keep the write offline and validate in a scratch target."

    with patch("genie.skills.mcp_trino.research.load_mcp_config", side_effect=AssertionError("load_mcp_config called")), \
         patch("genie.skills.mcp_trino.research.McpClient", side_effect=AssertionError("McpClient constructed")), \
         patch("genie.skills.mcp_trino.research.run_mcp_enhancement", side_effect=AssertionError("enhancement called")), \
         patch("genie.skills.mcp_trino.research._execute_via_mcp", side_effect=AssertionError("execute called")), \
         patch("genie.skills.mcp_trino.research._measure_mcp", side_effect=AssertionError("measure called")), \
         patch("genie.skills.mcp_trino.research._build_mcp_explain_runner", side_effect=AssertionError("explain called")), \
         patch("genie.skills.mcp_trino.preflight.run_preflight", side_effect=AssertionError("preflight called")), \
         patch("genie.skills.mcp_trino.preflight.apply_safe_limit", side_effect=AssertionError("safe limit called")):
        run_trino_research_via_mcp(
            provider, {}, "test-model", "default", _output_mock(), lambda *a, **kw: "",
            sql_text="INSERT INTO x SELECT * FROM t",
            metric="query_time_ms",
            iterations=1,
            runs=1,
        )

    md = _read_write_analysis_report(tmp_path)
    assert "## Suggested SQL Command (advisory, not executed)" not in md
    assert "Keep the write offline and validate in a scratch target." in md
    assert "No complete SQL command was extracted from advisory text." in md


def test_mcp_write_analysis_provider_error_is_complete_advisory_result(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    provider = MagicMock()
    provider.complete_text.side_effect = RuntimeError("provider down")

    with patch("genie.skills.mcp_trino.research.load_mcp_config", side_effect=AssertionError("load_mcp_config called")), \
         patch("genie.skills.mcp_trino.research.McpClient", side_effect=AssertionError("McpClient constructed")), \
         patch("genie.skills.mcp_trino.research.run_mcp_enhancement", side_effect=AssertionError("enhancement called")), \
         patch("genie.skills.mcp_trino.research._execute_via_mcp", side_effect=AssertionError("execute called")), \
         patch("genie.skills.mcp_trino.research._measure_mcp", side_effect=AssertionError("measure called")), \
         patch("genie.skills.mcp_trino.research._build_mcp_explain_runner", side_effect=AssertionError("explain called")), \
         patch("genie.skills.mcp_trino.preflight.run_preflight", side_effect=AssertionError("preflight called")), \
         patch("genie.skills.mcp_trino.preflight.apply_safe_limit", side_effect=AssertionError("safe limit called")):
        run_trino_research_via_mcp(
            provider, {}, "test-model", "default", _output_mock(), lambda *a, **kw: "",
            sql_text="INSERT INTO x SELECT * FROM t",
            metric="query_time_ms",
            iterations=1,
            runs=1,
        )

    md = _read_write_analysis_report(tmp_path)
    assert "## Suggested SQL Command (advisory, not executed)" not in md
    assert "LLM advice unavailable: provider down" in md


def test_mcp_sql_file_write_analysis_skips_live_surfaces(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    sql_file = tmp_path / "write.sql"
    sql_file.write_text("INSERT INTO x SELECT * FROM t")

    with patch("genie.skills.mcp_trino.research.load_mcp_config", side_effect=AssertionError("load_mcp_config called")), \
         patch("genie.skills.mcp_trino.research.McpClient", side_effect=AssertionError("McpClient constructed")), \
         patch("genie.skills.mcp_trino.research.run_mcp_enhancement", side_effect=AssertionError("enhancement called")), \
         patch("genie.skills.mcp_trino.research._execute_via_mcp", side_effect=AssertionError("execute called")), \
         patch("genie.skills.mcp_trino.research._measure_mcp", side_effect=AssertionError("measure called")), \
         patch("genie.skills.mcp_trino.research._build_mcp_explain_runner", side_effect=AssertionError("explain called")), \
         patch("genie.skills.mcp_trino.preflight.run_preflight", side_effect=AssertionError("preflight called")), \
         patch("genie.skills.mcp_trino.preflight.apply_safe_limit", side_effect=AssertionError("safe limit called")):
        run_trino_research_via_mcp(
            None, {}, "test-model", "default", _output_mock(), lambda *a, **kw: "",
            sql_file=str(sql_file),
            metric="query_time_ms",
            iterations=1,
            runs=1,
        )

    md = _read_write_analysis_report(tmp_path)
    assert f"| Source | {sql_file} |" in md


@pytest.mark.parametrize(
    ("sql", "keyword"),
    [
        ("RENAME TABLE old_name TO new_name", "RENAME"),
        ("REVOKE SELECT ON TABLE x FROM ROLE y", "REVOKE"),
    ],
)
def test_mcp_rename_revoke_dispatch_to_write_analysis(tmp_path, monkeypatch, sql, keyword):
    monkeypatch.chdir(tmp_path)
    output = _output_mock()

    with patch("genie.skills.mcp_trino.research.load_mcp_config", side_effect=AssertionError("load_mcp_config called")), \
         patch("genie.skills.mcp_trino.research.McpClient", side_effect=AssertionError("McpClient constructed")), \
         patch("genie.skills.mcp_trino.research.run_mcp_enhancement", side_effect=AssertionError("enhancement called")), \
         patch("genie.skills.mcp_trino.research._execute_via_mcp", side_effect=AssertionError("execute called")), \
         patch("genie.skills.mcp_trino.research._measure_mcp", side_effect=AssertionError("measure called")), \
         patch("genie.skills.mcp_trino.research._build_mcp_explain_runner", side_effect=AssertionError("explain called")), \
         patch("genie.skills.mcp_trino.preflight.run_preflight", side_effect=AssertionError("preflight called")), \
         patch("genie.skills.mcp_trino.preflight.apply_safe_limit", side_effect=AssertionError("safe limit called")):
        run_trino_research_via_mcp(
            None, {}, "test-model", "default", output, lambda *a, **kw: "",
            sql_text=sql,
            metric="query_time_ms",
            iterations=1,
            runs=1,
        )

    md = _read_write_analysis_report(tmp_path)
    assert "| Kind | ddl |" in md
    assert f"| Keyword | {keyword} |" in md


def test_mcp_interactive_prompts_before_preflight(monkeypatch):
    events = []

    class Sentinel(Exception):
        pass

    class FakeClient:
        def __init__(self, config):
            events.append("client")
            self.config = config

        def list_tools(self):
            events.append("list_tools")
            return [{"name": "query"}]

    def fake_read_paste_mode():
        events.append("paste")
        return "SELECT * FROM orders"

    def fake_read_input(prompt):
        if "Choose" in prompt:
            events.append("metric_prompt")
            return "1"
        if "Max iterations" in prompt:
            events.append("iterations_prompt")
            return "1"
        if "Verify runs" in prompt:
            events.append("runs_prompt")
            return "1"
        raise AssertionError(f"unexpected prompt: {prompt}")

    def fake_run_preflight(sql, explain_runner, budget):
        events.append("preflight")
        raise Sentinel

    monkeypatch.setattr(
        "genie.skills.mcp_trino.research.load_mcp_config",
        lambda: McpConfig(url="http://mcp.test/mcp", enabled=True, timeout=1),
    )
    monkeypatch.setattr("genie.skills.mcp_trino.research.McpClient", FakeClient)
    monkeypatch.setattr("genie.input._read_paste_mode", fake_read_paste_mode)
    monkeypatch.setattr("genie.input._read_input", fake_read_input)
    monkeypatch.setattr("genie.skills.mcp_trino.preflight.run_preflight", fake_run_preflight)

    with pytest.raises(Sentinel):
        run_trino_research_via_mcp(
            None, {}, "test-model", "default", _output_mock(), lambda *a, **kw: ""
        )

    assert events.index("paste") < events.index("metric_prompt")
    assert events.index("metric_prompt") < events.index("preflight")
    assert events.index("iterations_prompt") < events.index("preflight")
    assert events.index("runs_prompt") < events.index("preflight")


def test_mcp_interactive_write_sql_diverts_offline_after_reachability(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    events = []

    class FakeClient:
        def __init__(self, config):
            events.append("client")
            self.config = config

        def list_tools(self):
            events.append("list_tools")
            return [{"name": "query"}]

    def fake_read_paste_mode():
        events.append("paste")
        return "INSERT INTO x SELECT * FROM t"

    def fake_read_input(prompt):
        if "Choose" in prompt:
            events.append("metric_prompt")
            return "1"
        if "Max iterations" in prompt:
            events.append("iterations_prompt")
            return "1"
        if "Verify runs" in prompt:
            events.append("runs_prompt")
            return "1"
        raise AssertionError(f"unexpected prompt: {prompt}")

    monkeypatch.setattr(
        "genie.skills.mcp_trino.research.load_mcp_config",
        lambda: McpConfig(url="http://mcp.test/mcp", enabled=True, timeout=1),
    )
    monkeypatch.setattr("genie.skills.mcp_trino.research.McpClient", FakeClient)
    monkeypatch.setattr("genie.input._read_paste_mode", fake_read_paste_mode)
    monkeypatch.setattr("genie.input._read_input", fake_read_input)

    with patch("genie.skills.mcp_trino.preflight.run_preflight", side_effect=AssertionError("preflight called")), \
         patch("genie.skills.mcp_trino.preflight.apply_safe_limit", side_effect=AssertionError("safe limit called")), \
         patch("genie.skills.mcp_trino.research.run_mcp_enhancement", side_effect=AssertionError("enhancement called")), \
         patch("genie.skills.mcp_trino.research._execute_via_mcp", side_effect=AssertionError("execute called")), \
         patch("genie.skills.mcp_trino.research._measure_mcp", side_effect=AssertionError("measure called")), \
         patch("genie.skills.mcp_trino.research._build_mcp_explain_runner", side_effect=AssertionError("explain called")):
        run_trino_research_via_mcp(
            None, {}, "test-model", "default", _output_mock(), lambda *a, **kw: ""
        )

    md = _read_write_analysis_report(tmp_path)
    assert events == ["client", "list_tools", "paste"]
    assert "write-analysis" in md


# ── Result Equivalence ───────────────────────────────────────────────────────


class TestResultsEquivalent:
    def test_identical_dicts(self):
        rows = [{"a": 1, "b": "x"}, {"a": 2, "b": "y"}]
        eq, reason = _results_equivalent(rows, rows)
        assert eq is True
        assert "match" in reason

    def test_different_row_count(self):
        eq, reason = _results_equivalent([{"a": 1}], [{"a": 1}, {"a": 2}])
        assert eq is False
        assert "row count" in reason

    def test_both_empty(self):
        eq, reason = _results_equivalent([], [])
        assert eq is True

    def test_different_values(self):
        rows_a = [{"a": 1}]
        rows_b = [{"a": 999}]
        eq, reason = _results_equivalent(rows_a, rows_b)
        assert eq is False


# ── SQL Extraction ───────────────────────────────────────────────────────────


class TestExtractSql:
    def test_sql_fence(self):
        reply = "Here's the optimized query:\n```sql\nSELECT a FROM t\n```\nDone."
        sql = _extract_sql_from_reply(reply)
        assert sql == "SELECT a FROM t"

    def test_strips_semicolon(self):
        reply = "```sql\nSELECT 1;\n```"
        sql = _extract_sql_from_reply(reply)
        assert sql == "SELECT 1"

    def test_generic_fence_with_sql(self):
        reply = "```\nSELECT a, b FROM t WHERE x = 1\n```"
        sql = _extract_sql_from_reply(reply)
        assert sql is not None
        assert "SELECT" in sql

    def test_no_sql(self):
        reply = "I think we should use a CTE approach."
        sql = _extract_sql_from_reply(reply)
        assert sql is None


# ── MCP Execution ────────────────────────────────────────────────────────────


class TestExecuteViaMcp:
    def setup_method(self):
        import genie.skills.mcp_trino.research as _mod
        _mod._resolved_tool = None

    def _mock_client(self, call_return):
        mock_client = MagicMock(spec=McpClient)
        mock_client.list_tools.return_value = [
            {"name": "query", "inputSchema": {"properties": {"sql": {"type": "string"}}}},
        ]
        mock_client.call_tool.return_value = call_return
        return mock_client

    def test_parses_json_response(self):
        mock_client = self._mock_client(json.dumps({
            "rows": [{"id": 1}, {"id": 2}],
            "columns": ["id"],
            "duration_ms": 42,
            "metrics": {
                "cpu_time_ms": 10,
                "wall_time_ms": 15,
                "processed_rows": 2,
            }
        }))
        result = _execute_via_mcp(mock_client, "SELECT id FROM t")
        assert result["row_count"] == 2
        assert result["columns"] == ["id"]
        assert result["metrics"].cpu_time_ms == 10
        assert result["error"] is None

    def test_handles_text_response(self):
        mock_client = self._mock_client("plain text result")
        result = _execute_via_mcp(mock_client, "SELECT 1")
        assert result["rows"] == []
        assert result["metrics"].query_time_ms > 0


# ── Report Generation ────────────────────────────────────────────────────────


class TestUxHelpers:
    """Render helpers added in v22 UX sprint."""

    def _mock_output(self):
        mock = MagicMock()
        mock._lines = []
        def _print(msg):
            mock._lines.append(str(msg))
        mock.print = _print
        return mock

    def test_fmt_metric_value_adaptive_precision(self):
        assert _fmt_metric_value(0) == "0"
        assert _fmt_metric_value(0.0005) == "0.0005"
        assert _fmt_metric_value(0.053) == "0.053"
        assert _fmt_metric_value(12.3) == "12.30"
        assert _fmt_metric_value(1234) == "1234"

    def test_render_sql_diff_shows_added_and_removed_lines(self):
        out = self._mock_output()
        _render_sql_diff(out, "SELECT * FROM t", "SELECT id FROM t WHERE id > 0")
        rendered = "\n".join(out._lines)
        assert "green" in rendered  # +added line colored
        assert "red" in rendered    # -removed line colored

    def test_render_sql_diff_noop_when_unchanged(self):
        out = self._mock_output()
        _render_sql_diff(out, "SELECT 1", "SELECT 1")
        assert any("no SQL change" in ln for ln in out._lines)

    def test_render_iteration_result_kept_status(self):
        out = self._mock_output()
        _render_iteration_result(
            out, iteration=1, total=5, status="improved",
            hypothesis="add partition filter", metric_key="query_time_ms",
            metric_value=0.053, delta=-0.012, elapsed_s=1.24,
        )
        rendered = "\n".join(out._lines)
        assert "KEPT" in rendered
        assert "green" in rendered
        assert "iteration[/dim] 1/5" in rendered
        assert "metric [/dim] query_time_ms" in rendered
        assert "elapsed[/dim]     1.2s" in rendered
        assert "0.053" in rendered

    def test_render_iteration_result_revert_status(self):
        out = self._mock_output()
        _render_iteration_result(
            out, iteration=2, total=5, status="semantic_drift",
            hypothesis="drop WHERE clause", metric_key="query_time_ms",
            metric_value=0.020, delta=-0.030, elapsed_s=1.5,
            reason="semantic_drift: row count differs: 5 vs 0",
        )
        rendered = "\n".join(out._lines)
        assert "REVERT" in rendered
        assert "red" in rendered
        assert "reason [/dim] semantic_drift: row count differs: 5 vs 0" in rendered
        assert "note   [/dim] drop WHERE clause" in rendered

    def test_render_plan_card_shows_sql_stats(self):
        out = self._mock_output()
        _render_plan_card(
            out, sql="SELECT 1\nFROM t", sql_source="q.sql",
            metric="query_time_ms", iterations=5, runs=3,
            server="http://localhost:8811/mcp", safe_limit=None,
            query_timeout=300,
        )
        rendered = "\n".join(out._lines)
        assert "Research Plan" in rendered
        assert "q.sql" in rendered
        assert "2 lines" in rendered
        assert "http://localhost:8811/mcp" in rendered

    def test_render_plan_card_shows_safe_limit_when_set(self):
        out = self._mock_output()
        _render_plan_card(
            out, sql="SELECT 1", sql_source="stdin",
            metric="query_time_ms", iterations=3, runs=3,
            server="url", safe_limit=1000, query_timeout=300,
        )
        rendered = "\n".join(out._lines)
        assert "LIMIT 1000" in rendered

    def test_measure_mcp_shows_progress_via_status(self):
        """_measure_mcp should wrap each run in output.status when available."""
        from genie.skills.mcp_trino.research import _measure_mcp
        from contextlib import nullcontext

        calls = []
        class FakeOutput:
            def status(self, msg):
                calls.append(msg)
                return nullcontext()

        mock_client = MagicMock(spec=McpClient)
        mock_client.list_tools.return_value = [
            {"name": "query", "inputSchema": {"properties": {"sql": {"type": "string"}}}},
        ]
        mock_client.call_tool.return_value = json.dumps({
            "rows": [], "columns": [], "duration_ms": 1,
            # Non-zero server-side metrics so backfill doesn't trigger
            # extra EXPLAIN ANALYZE rounds in this status-coverage test.
            "metrics": {"cpu_time_ms": 1, "peak_memory_bytes": 1},
        })
        import genie.skills.mcp_trino.research as _mod
        _mod._resolved_tool = None

        out = FakeOutput()
        _measure_mcp(mock_client, "SELECT 1", "query_time_ms", runs=3,
                     output=out, label="baseline")
        assert len(calls) == 3
        assert "baseline: run 1/3" in calls[0]
        assert "baseline: run 3/3" in calls[2]

    def test_measure_mcp_applies_candidate_timeout_to_tool_call(self):
        """Candidate timeout should cap the MCP request and show the limit."""
        from genie.skills.mcp_trino.research import _measure_mcp
        from contextlib import nullcontext

        calls = []
        class FakeOutput:
            def status(self, msg):
                calls.append(msg)
                return nullcontext()

        mock_client = MagicMock(spec=McpClient)
        mock_client.list_tools.return_value = [
            {"name": "query", "inputSchema": {"properties": {"sql": {"type": "string"}}}},
        ]
        mock_client.call_tool.return_value = json.dumps({
            "rows": [], "columns": [], "duration_ms": 1,
            "metrics": {"cpu_time_ms": 1, "peak_memory_bytes": 1},
        })
        import genie.skills.mcp_trino.research as _mod
        _mod._resolved_tool = None

        _measure_mcp(
            mock_client, "SELECT 1", "query_time_ms", runs=1,
            output=FakeOutput(), label="iter 1 candidate", timeout_ms=12_345,
        )

        assert mock_client.call_tool.call_args.kwargs["timeout"] == 12.345
        assert "limit=12.3s" in calls[0]

    def test_execute_via_mcp_handles_bare_list_response(self):
        """mcp-trino returns rows as a top-level JSON list, not wrapped in {"rows": ...}.
        _execute_via_mcp must extract rows + infer columns from the list shape."""
        from genie.skills.mcp_trino.research import _execute_via_mcp

        mock_client = MagicMock(spec=McpClient)
        mock_client.list_tools.return_value = [
            {"name": "execute_query", "inputSchema": {"properties": {"query": {"type": "string"}}}},
        ]
        # Server returns a bare JSON list — mcp-trino's actual shape.
        mock_client.call_tool.return_value = json.dumps([
            {"col_a": 1, "col_b": "x"},
            {"col_a": 2, "col_b": "y"},
        ])
        import genie.skills.mcp_trino.research as _mod
        _mod._resolved_tool = None

        result = _execute_via_mcp(mock_client, "SELECT * FROM t")
        assert result["row_count"] == 2
        assert result["rows"] == [{"col_a": 1, "col_b": "x"}, {"col_a": 2, "col_b": "y"}]
        assert sorted(result["columns"]) == ["col_a", "col_b"]
        assert result["error"] is None

    def test_measure_mcp_backfills_metrics_from_explain_analyze(self):
        """When the MCP server returns rows but no structured stats,
        _measure_mcp should fall back to EXPLAIN ANALYZE and parse stage totals."""
        from genie.skills.mcp_trino.research import _measure_mcp

        # Server returns rows but empty metrics dict on raw SELECT,
        # and a Trino EXPLAIN ANALYZE plan text on the EXPLAIN ANALYZE call.
        explain_text = (
            "Trino version: 467\n"
            "Queued: 78us, Analysis: 225us, Planning: 4ms, Execution: 2.40s\n"
            "Fragment 1 [SINGLE]\n"
            "    CPU: 35.44us, Scheduled: 35.98us, Blocked 0.00ns "
            "(Input: 0.00ns, Output: 0.00ns), Input: 1 row (5B); per task: avg.: 1.00 std.dev.: 0.00, "
            "Output: 1 row (5B)\n"
            "    Peak Memory: 132B, Tasks count: 1; per task: max: 132B\n"
        )

        def call_tool_side_effect(tool_name, params):
            sql = params.get("sql") or params.get("query") or ""
            # Use bare-list response shape (mcp-trino's actual behavior)
            # to ensure backfill works end-to-end on the real server contract.
            if sql.upper().startswith("EXPLAIN ANALYZE"):
                return json.dumps([{"Query Plan": explain_text}])
            return json.dumps([{"x": 1}])

        mock_client = MagicMock(spec=McpClient)
        mock_client.list_tools.return_value = [
            {"name": "execute_query", "inputSchema": {"properties": {"query": {"type": "string"}}}},
        ]
        mock_client.call_tool.side_effect = call_tool_side_effect
        import genie.skills.mcp_trino.research as _mod
        _mod._resolved_tool = None

        result = _measure_mcp(mock_client, "SELECT 1", "cpu_time_ms", runs=2)

        # Median run's metrics should now have non-zero server-side fields
        # backfilled from the EXPLAIN ANALYZE parse.
        assert result.metrics.cpu_time_ms > 0, (
            f"cpu_time_ms should be backfilled from EA parse, got {result.metrics.cpu_time_ms}"
        )
        assert result.metrics.peak_memory_bytes > 0, (
            f"peak_memory_bytes should be backfilled, got {result.metrics.peak_memory_bytes}"
        )
        assert result.metrics.processed_rows > 0, (
            f"processed_rows should be backfilled, got {result.metrics.processed_rows}"
        )

        # Median sample uses the requested metric_key (cpu_time_ms here).
        assert result.median_metric > 0

        # 2 runs * (1 raw + 1 EA) = 4 call_tool invocations.
        assert mock_client.call_tool.call_count == 4

    def test_render_summary_card_shows_improvement(self):
        out = self._mock_output()
        _render_summary_card(
            out, baseline_value=1.0, best_value=0.4, metric_key="query_time_ms",
            improvement_abs=-0.6, improvement_pct=-60.0,
            data_consistent=True, data_consistency_reason="exact match",
            iterations_ran=5,
        )
        rendered = "\n".join(out._lines)
        assert "Final Result" in rendered
        assert "↓" in rendered  # improvement arrow
        assert "PASS" in rendered
        assert "█" in rendered  # visual bar


class TestGenerateReport:
    def _make_report(self) -> EnhancementReport:
        return EnhancementReport(
            timestamp="2026-04-13 14:00:00",
            original_sql="SELECT * FROM t",
            original_result_sample=[{"id": 1, "name": "a"}, {"id": 2, "name": "b"}],
            original_columns=["id", "name"],
            original_row_count=2,
            original_metrics=RunMetrics(query_time_ms=100, cpu_time_ms=80, wall_time_ms=120,
                                         processed_rows=2),
            enhanced_sql="SELECT id, name FROM t",
            enhanced_result_sample=[{"id": 1, "name": "a"}, {"id": 2, "name": "b"}],
            enhanced_columns=["id", "name"],
            enhanced_row_count=2,
            enhanced_metrics=RunMetrics(query_time_ms=60, cpu_time_ms=40, wall_time_ms=70,
                                         processed_rows=2),
            metric_key="query_time_ms",
            baseline_value=100.0,
            best_value=60.0,
            improvement_abs=-40.0,
            improvement_pct=-40.0,
            iterations=[
                IterationRecord(iteration=1, status="improved", metric_value=60.0,
                                delta=-40.0, hypothesis="Replace SELECT * with named columns"),
            ],
            data_consistent=True,
            data_consistency_reason="exact match",
            mcp_server_url="http://localhost:8811/mcp",
            verify_runs=3,
        )

    def test_report_has_all_sections(self):
        report = self._make_report()
        md = generate_report(report)
        assert "# Trino Query Enhancement Report" in md
        assert "## Meta" in md
        assert "## Performance Comparison" in md
        assert "## Summary" in md
        assert "## Iteration History" in md
        assert "## Original SQL" in md
        assert "## Original Result (sample)" in md
        assert "## Enhanced SQL" in md
        assert "## Enhanced Result (sample)" in md

    def test_report_contains_data(self):
        report = self._make_report()
        md = generate_report(report)
        assert "SELECT * FROM t" in md
        assert "SELECT id, name FROM t" in md
        assert "http://localhost:8811/mcp" in md
        assert "query_time_ms" in md
        assert "exact match" in md
        assert "YES" in md  # data_consistent

    def test_report_unverified_envelope_uses_diagnostic_text_contract(self):
        report = self._make_report()
        report.data_consistent = False
        report.data_consistency_reason = "both_upstream_completeness_unverified"

        md = generate_report(report)

        assert "unverified/incomplete result" in md
        assert "`both_upstream_completeness_unverified`" in md
        assert (
            "First 10 rows of the received query-output envelope, shown for "
            "diagnostic inspection only; semantic preservation is unverified."
        ) in md
        assert "| Data Consistent | NO |" in md
        assert "full row-level equivalence check verified" not in md
        assert "full row-level equivalence was verified separately." not in md
        assert "semantic preservation was verified" not in md

    def test_report_table_structure_is_fixed(self):
        """Verify that the report uses consistent table headers."""
        report = self._make_report()
        md = generate_report(report)
        # Performance comparison table
        assert "| Metric | Original | Enhanced | Delta | Change % |" in md
        # Summary table
        assert "| Field | Value |" in md
        # Iteration history table
        assert "| Round | Status | Metric Value | Delta | Hypothesis |" in md

    def test_no_improvement_shows_unchanged(self):
        report = self._make_report()
        report.enhanced_sql = report.original_sql  # no change
        md = generate_report(report)
        assert "no improvement found" in md.lower()

    def test_report_deterministic(self):
        """Same input produces identical output."""
        report = self._make_report()
        md1 = generate_report(report)
        md2 = generate_report(report)
        assert md1 == md2

    def test_report_has_table_suggestions_section(self):
        report = self._make_report()
        md = generate_report(report)
        assert "## Table Structure Suggestions" in md

    def test_report_shows_suggestions_when_present(self):
        report = self._make_report()
        report.table_suggestions = [
            TableSuggestion(
                table="cat.sch.orders",
                category="partition",
                suggestion="Consider partitioning by order_date.",
                suggestion_zh="建議使用 order_date 進行分區。",
                severity="warning",
            ),
        ]
        md = generate_report(report)
        assert "cat.sch.orders" in md
        assert "partition" in md
        assert "Consider partitioning" in md

    def test_report_no_suggestions_message(self):
        report = self._make_report()
        report.table_suggestions = []
        report.had_qualified_tables = True
        md = generate_report(report)
        assert "No table structure issues detected" in md

    def test_report_unqualified_tables_hint(self):
        report = self._make_report()
        report.table_suggestions = []
        report.had_qualified_tables = False
        md = generate_report(report)
        assert "No fully-qualified tables" in md


# ── Chinese Locale ──────────────────────────────────────────────────────────


class TestChineseLocale:
    def _make_report(self) -> EnhancementReport:
        return EnhancementReport(
            timestamp="2026-04-13 14:00:00",
            original_sql="SELECT * FROM t",
            original_result_sample=[],
            original_columns=[],
            original_row_count=0,
            original_metrics=RunMetrics(query_time_ms=100),
            enhanced_sql="SELECT id FROM t",
            enhanced_result_sample=[],
            enhanced_columns=[],
            enhanced_row_count=0,
            enhanced_metrics=RunMetrics(query_time_ms=60),
            metric_key="query_time_ms",
            baseline_value=100.0,
            best_value=60.0,
            improvement_abs=-40.0,
            improvement_pct=-40.0,
            iterations=[],
            data_consistent=True,
            data_consistency_reason="exact match",
            mcp_server_url="http://localhost:8811/mcp",
            verify_runs=3,
        )

    def test_zh_headers(self):
        report = self._make_report()
        md = generate_report(report, locale="zh")
        assert "# Trino 查詢優化報告" in md
        assert "## 基本資訊" in md
        assert "## 效能比較" in md
        assert "## 摘要" in md
        assert "## 迭代歷程" in md
        assert "## 原始 SQL" in md
        assert "## 優化後 SQL" in md
        assert "## 表結構優化建議" in md

    def test_zh_summary_labels(self):
        report = self._make_report()
        md = generate_report(report, locale="zh")
        assert "基線" in md
        assert "最佳" in md
        assert "改善" in md
        assert "越低越好" in md

    def test_zh_sql_stays_english(self):
        report = self._make_report()
        md = generate_report(report, locale="zh")
        assert "SELECT * FROM t" in md
        assert "SELECT id FROM t" in md

    def test_zh_metrics_stay_english(self):
        report = self._make_report()
        md = generate_report(report, locale="zh")
        assert "query_time_ms" in md

    def test_zh_suggestion_uses_chinese_text(self):
        report = self._make_report()
        report.table_suggestions = [
            TableSuggestion(
                table="cat.sch.t",
                category="partition",
                suggestion="Consider partitioning by date.",
                suggestion_zh="建議使用日期進行分區。",
                severity="warning",
            ),
        ]
        md = generate_report(report, locale="zh")
        assert "建議使用日期進行分區" in md
        assert "Consider partitioning" not in md

    def test_en_is_default(self):
        report = self._make_report()
        md = generate_report(report)
        assert "# Trino Query Enhancement Report" in md
        assert "Trino 查詢優化報告" not in md


# ── Table Name Extraction ───────────────────────────────────────────────────


class TestExtractTableNames:
    def test_simple_select(self):
        tables = _extract_table_names("SELECT a FROM my_catalog.my_schema.orders")
        assert ("my_catalog", "my_schema", "orders") in tables

    def test_unqualified_table(self):
        tables = _extract_table_names("SELECT a FROM orders")
        assert any(t[2] == "orders" for t in tables)

    def test_join_multiple_tables(self):
        sql = "SELECT a FROM cat.sch.orders o JOIN cat.sch.items i ON o.id = i.order_id"
        tables = _extract_table_names(sql)
        names = {t[2] for t in tables}
        assert "orders" in names
        assert "items" in names

    def test_subquery(self):
        sql = "SELECT * FROM (SELECT id FROM cat.sch.events) e"
        tables = _extract_table_names(sql)
        assert any(t[2] == "events" for t in tables)

    def test_invalid_sql_returns_empty(self):
        tables = _extract_table_names("THIS IS NOT SQL AT ALL <<<>>>")
        assert tables == []

    def test_cte(self):
        sql = "WITH cte AS (SELECT id FROM cat.sch.users) SELECT * FROM cte"
        tables = _extract_table_names(sql)
        names = {t[2] for t in tables}
        assert "users" in names


# ── Table Suggestions ───────────────────────────────────────────────────────


class TestGenerateTableSuggestions:
    def test_no_partition_suggests_partition(self):
        meta = [TableMetadata(
            catalog="cat", schema="sch", table_name="events",
            columns=[
                ColumnInfo("event_id", "bigint", "NO", 1),
                ColumnInfo("event_date", "date", "NO", 2),
                ColumnInfo("payload", "varchar", "YES", 3),
            ],
            properties={"partitioning": "[]"},
        )]
        suggestions = _generate_table_suggestions(meta)
        partition_sugs = [s for s in suggestions if s.category == "partition"]
        assert len(partition_sugs) == 1
        assert "event_date" in partition_sugs[0].suggestion
        assert "event_date" in partition_sugs[0].suggestion_zh

    def test_no_bucket_suggests_bucket(self):
        meta = [TableMetadata(
            catalog="cat", schema="sch", table_name="orders",
            columns=[
                ColumnInfo("order_id", "bigint", "NO", 1),
                ColumnInfo("customer_id", "bigint", "NO", 2),
            ],
            properties={},
        )]
        suggestions = _generate_table_suggestions(meta)
        bucket_sugs = [s for s in suggestions if s.category == "bucket"]
        assert len(bucket_sugs) == 1

    def test_unbounded_varchar_id_suggests_length(self):
        meta = [TableMetadata(
            catalog="cat", schema="sch", table_name="products",
            columns=[
                ColumnInfo("product_id", "varchar", "NO", 1),
                ColumnInfo("name", "varchar", "YES", 2),
            ],
            properties={},
        )]
        suggestions = _generate_table_suggestions(meta)
        type_sugs = [s for s in suggestions if s.category == "data_type"]
        assert len(type_sugs) == 1
        assert "product_id" in type_sugs[0].suggestion

    def test_double_amount_suggests_decimal(self):
        meta = [TableMetadata(
            catalog="cat", schema="sch", table_name="transactions",
            columns=[
                ColumnInfo("amount", "double", "NO", 1),
            ],
            properties={},
        )]
        suggestions = _generate_table_suggestions(meta)
        type_sugs = [s for s in suggestions if s.category == "data_type"]
        assert len(type_sugs) == 1
        assert "DECIMAL" in type_sugs[0].suggestion

    def test_wide_table_no_sort_suggests_sort(self):
        cols = [ColumnInfo(f"col_{i}", "varchar", "YES", i) for i in range(15)]
        meta = [TableMetadata(
            catalog="cat", schema="sch", table_name="wide",
            columns=cols,
            properties={"sorted_by": "[]"},
        )]
        suggestions = _generate_table_suggestions(meta)
        sort_sugs = [s for s in suggestions if s.category == "sort"]
        assert len(sort_sugs) == 1

    def test_no_suggestions_when_everything_configured(self):
        meta = [TableMetadata(
            catalog="cat", schema="sch", table_name="good_table",
            columns=[
                ColumnInfo("id", "bigint", "NO", 1),
                ColumnInfo("name", "varchar(100)", "YES", 2),
            ],
            properties={"partitioning": "[day(created)]", "bucket_count": "16",
                         "sorted_by": "[id]"},
        )]
        suggestions = _generate_table_suggestions(meta)
        assert len(suggestions) == 0


# ── EXPLAIN ANALYZE Parsing ─────────────────────────────────────────────────


class TestParseExplainStages:
    def test_parses_fragment_with_metrics(self):
        text = (
            "Fragment 0 [SINGLE]\n"
            "    CPU: 123.00ms, Scheduled: 200.00ms, Blocked: 0.00ms\n"
            "    Peak Memory: 1.5MB\n"
            "    Input: 1000 rows (50KB), Output: 100 rows (5KB)\n"
        )
        stages = _parse_explain_stages(text)
        assert len(stages) == 1
        assert stages[0]["id"] == 0
        assert stages[0]["cpu_ms"] == 123.0
        assert stages[0]["wall_ms"] == 200.0
        assert stages[0]["memory_bytes"] == int(1.5 * 1024 * 1024)
        assert stages[0]["input_rows"] == 1000
        assert stages[0]["output_rows"] == 100

    def test_parses_multiple_fragments(self):
        text = (
            "Fragment 0 [SINGLE]\n"
            "    CPU: 10.00ms, Scheduled: 20.00ms\n"
            "    Input: 100 rows\n"
            "Fragment 1 [HASH]\n"
            "    CPU: 50.00ms, Scheduled: 80.00ms\n"
            "    Input: 5000 rows\n"
        )
        stages = _parse_explain_stages(text)
        assert len(stages) == 2
        assert stages[0]["id"] == 0
        assert stages[1]["id"] == 1
        assert stages[1]["cpu_ms"] == 50.0

    def test_handles_seconds_unit(self):
        text = (
            "Fragment 0 [SINGLE]\n"
            "    CPU: 1.23s, Scheduled: 2.00s\n"
        )
        stages = _parse_explain_stages(text)
        assert stages[0]["cpu_ms"] == 1230.0
        assert stages[0]["wall_ms"] == 2000.0

    def test_empty_text_returns_empty(self):
        assert _parse_explain_stages("") == []

    def test_no_fragments_returns_empty(self):
        text = "Query plan without fragment headers\nSome random text"
        assert _parse_explain_stages(text) == []

    def test_handles_microseconds_unit(self):
        """Trino 467 uses `us` (microseconds) in EXPLAIN ANALYZE output."""
        text = (
            "Fragment 1 [SINGLE]\n"
            "    CPU: 52.94us, Scheduled: 54.05us\n"
            "    Peak Memory: 132B\n"
            "    Input: 1 row (5B), Output: 1 row (5B)\n"
        )
        stages = _parse_explain_stages(text)
        assert stages[0]["cpu_ms"] == pytest.approx(0.05294)
        assert stages[0]["wall_ms"] == pytest.approx(0.05405)
        assert stages[0]["memory_bytes"] == 132
        assert stages[0]["input_rows"] == 1
        assert stages[0]["output_rows"] == 1

    def test_handles_nanoseconds_unit(self):
        text = (
            "Fragment 0 [SINGLE]\n"
            "    CPU: 500.00ns\n"
        )
        stages = _parse_explain_stages(text)
        assert stages[0]["cpu_ms"] == pytest.approx(0.0005)

    def test_first_match_wins_within_stage(self):
        """Nested operator metrics must not overwrite fragment-level aggregates."""
        text = (
            "Fragment 1 [SINGLE]\n"
            "    CPU: 52.94us, Scheduled: 54.05us, Input: 10 rows, Output: 10 rows\n"
            "    Peak Memory: 132B\n"
            "    Values[]\n"
            "        CPU: 0.00ns, Scheduled: 0.00ns, Output: 1 row (5B)\n"
            "        Input avg.: 1.00 rows\n"
        )
        stages = _parse_explain_stages(text)
        # Fragment-level 52.94us must survive the operator-level 0.00ns
        assert stages[0]["cpu_ms"] == pytest.approx(0.05294)
        assert stages[0]["wall_ms"] == pytest.approx(0.05405)
        assert stages[0]["memory_bytes"] == 132
        assert stages[0]["input_rows"] == 10
        assert stages[0]["output_rows"] == 10


class TestFetchExplainAnalyze:
    def test_returns_unavailable_on_error(self):
        mock_client = MagicMock(spec=McpClient)
        mock_client.call_tool.return_value = json.dumps({"error": "not supported"})
        result = _fetch_explain_analyze(mock_client, "SELECT 1")
        assert result.available is False

    def test_returns_unavailable_on_exception(self):
        mock_client = MagicMock(spec=McpClient)
        mock_client.call_tool.side_effect = RuntimeError("connection lost")
        result = _fetch_explain_analyze(mock_client, "SELECT 1")
        assert result.available is False

    def test_parses_successful_explain(self):
        mock_client = MagicMock(spec=McpClient)
        explain_output = (
            "Fragment 0 [SINGLE]\n"
            "    CPU: 50.00ms, Scheduled: 80.00ms\n"
            "    Peak Memory: 2.0MB\n"
            "    Input: 500 rows, Output: 50 rows\n"
        )
        mock_client.call_tool.return_value = json.dumps({
            "rows": [{"Query Plan": explain_output}],
            "columns": ["Query Plan"],
        })
        result = _fetch_explain_analyze(mock_client, "SELECT 1")
        assert result.available is True
        assert len(result.stages) == 1
        assert result.total_cpu_ms == 50.0


class TestExplainInReport:
    def _make_report_with_explain(self) -> EnhancementReport:
        return EnhancementReport(
            timestamp="2026-04-13 23:30:00",
            original_sql="SELECT * FROM t",
            original_result_sample=[],
            original_columns=[],
            original_row_count=0,
            original_metrics=RunMetrics(query_time_ms=100),
            enhanced_sql="SELECT id FROM t",
            enhanced_result_sample=[],
            enhanced_columns=[],
            enhanced_row_count=0,
            enhanced_metrics=RunMetrics(query_time_ms=60),
            metric_key="query_time_ms",
            baseline_value=100.0,
            best_value=60.0,
            improvement_abs=-40.0,
            improvement_pct=-40.0,
            iterations=[],
            data_consistent=True,
            data_consistency_reason="exact match",
            mcp_server_url="http://localhost:8811/mcp",
            verify_runs=3,
            original_explain=ExplainAnalyzeResult(
                raw_text="Fragment 0...",
                stages=[{"id": 0, "cpu_ms": 50, "wall_ms": 80, "memory_bytes": 2097152,
                         "input_rows": 500, "output_rows": 50}],
                total_cpu_ms=50, total_wall_ms=80, total_memory_bytes=2097152,
                total_input_rows=500, total_output_rows=50,
                available=True,
            ),
            enhanced_explain=ExplainAnalyzeResult(
                raw_text="Fragment 0...",
                stages=[{"id": 0, "cpu_ms": 20, "wall_ms": 30, "memory_bytes": 1048576,
                         "input_rows": 500, "output_rows": 50}],
                total_cpu_ms=20, total_wall_ms=30, total_memory_bytes=1048576,
                total_input_rows=500, total_output_rows=50,
                available=True,
            ),
        )

    def test_report_has_explain_section(self):
        report = self._make_report_with_explain()
        md = generate_report(report)
        assert "## EXPLAIN ANALYZE" in md
        assert "### Original Query Plan" in md
        assert "### Enhanced Query Plan" in md

    def test_report_shows_stage_table(self):
        report = self._make_report_with_explain()
        md = generate_report(report)
        assert "| 0 |" in md
        assert "500" in md

    def test_report_unavailable_explain(self):
        report = self._make_report_with_explain()
        report.original_explain = ExplainAnalyzeResult(
            raw_text="failed", available=False,
        )
        report.enhanced_explain = None
        md = generate_report(report)
        assert "not available" in md.lower()

    def test_zh_explain_headers(self):
        report = self._make_report_with_explain()
        md = generate_report(report, locale="zh")
        assert "原始查詢計畫" in md
        assert "優化後查詢計畫" in md


# ── v57 model-call budget guards ─────────────────────────────────────────────


def _fake_cost():
    return SimpleNamespace(
        available=False,
        reason="test",
        cost_scalar=None,
        rows_est=None,
        bytes_est=None,
        plan_json=None,
    )


def test_decompose_seed_default_does_not_call_fragment_llm(monkeypatch):
    monkeypatch.setattr(
        "genie.skills.mcp_trino.critical_path.analyze_critical_path",
        lambda _sql: SimpleNamespace(available=False, reason="test"),
    )

    def fail_if_called(_prompt):
        raise AssertionError("fragment LLM should not be called by default")

    sql = "WITH a AS (SELECT 1 AS id) SELECT id FROM a"
    recomposed_sql, fragments, candidates, _ = _produce_decompose_candidate(
        sql,
        fail_if_called,
        lambda _sql: _fake_cost(),
        run_static_gates=False,
    )

    assert recomposed_sql == sql
    assert fragments
    assert candidates
    assert all(not candidate.changed for candidate in candidates)


def test_decompose_seed_opt_in_cap_limits_fragment_llm(monkeypatch):
    """v58: cap test with 3 monster fragments and max_fragment_model_calls=1.

    Only 1 fragment should trigger the LLM optimize call; the remaining 2
    must be passthrough (over-cap).  This proves the cap actually constrains.
    """
    from unittest.mock import patch as _patch
    from genie.skills.mcp_trino.trino_optimize import (
        Fragment, RecomposeResult, RecomposeStatus, RewriteCandidate,
    )
    from genie.output.step_trace import StepStatus

    monkeypatch.setattr(
        "genie.skills.mcp_trino.critical_path.analyze_critical_path",
        lambda _sql: SimpleNamespace(available=False, reason="test"),
    )

    _fake_cost_obj = _fake_cost()

    # Build 3 monster fragments
    fragments = [
        Fragment(
            fragment_id=f"cte_{i}",
            sql=f"SELECT {i} AS id",
            role="cte",
            position_hint=i,
            subq_ordinal=None,
            is_independently_runnable=True,
            is_monster=True,
            monster_rank=i,
            findings=(),
            cost=_fake_cost_obj,
        )
        for i in range(1, 4)
    ]

    optimize_calls = []

    def mock_optimize(fragment, llm_fn, cp_guidance=None):
        optimize_calls.append(fragment.fragment_id)
        return RewriteCandidate(
            fragment_id=fragment.fragment_id,
            original_sql=fragment.sql,
            rewritten_sql=fragment.sql + " -- opt",
            action="rewrite",
            changed=True,
            admitted=True,
            rationale="test",
        )

    rr = RecomposeResult(
        sql="SELECT 1 -- recomposed",
        status=RecomposeStatus.OK,
        cross_fragment_findings=(),
        reverted_fragments=(),
        scan_ok_confident=True,
    )

    trace = []
    with _patch("genie.skills.mcp_trino.trino_optimize.decompose", return_value=fragments), \
         _patch("genie.skills.mcp_trino.trino_optimize.optimize", side_effect=mock_optimize), \
         _patch("genie.skills.mcp_trino.trino_optimize.recompose", return_value=rr), \
         _patch("genie.skills.trino_query.detection_scan.scan_sql",
                return_value=SimpleNamespace(scan_ok_confident=True, findings=())), \
         _patch("genie.skills.mcp_trino.write_analysis._column_safe_candidates",
                side_effect=lambda c: (c, [])), \
         _patch("genie.skills.mcp_trino.write_analysis._semantic_safe_candidates",
                side_effect=lambda c: (c, [])):
        _produce_decompose_candidate(
            "WITH a AS (SELECT 1) SELECT * FROM a",
            lambda _prompt: "[]",
            lambda _sql: _fake_cost(),
            run_static_gates=False,
            enable_fragment_rewrite=True,
            max_fragment_model_calls=1,
            step_trace=trace,
        )

    # Only 1 fragment should have been optimized (cap=1)
    assert len(optimize_calls) == 1, f"expected 1 optimize call, got {len(optimize_calls)}: {optimize_calls}"

    # The other 2 should be over-cap SKIPPED
    over_cap_events = [
        ev for ev in trace
        if ev.status == StepStatus.SKIPPED and ev.detail.get("action") == "over_cap"
    ]
    assert len(over_cap_events) == 2, f"expected 2 over-cap events, got {len(over_cap_events)}"


def test_plan_cost_loop_records_model_failure_without_raising():
    from genie.skills.mcp_trino.preflight import _plan_cost_loop_core

    provider = MagicMock()
    provider.complete_text.side_effect = RuntimeError("provider down")
    output = _output_mock()

    result = _plan_cost_loop_core(
        provider=provider,
        model="test-model",
        reasoning="default",
        sys_prompt="system",
        original_sql="SELECT 1",
        metric_key="query_time_ms",
        max_iterations=1,
        max_fallbacks=0,
        baseline_cost=1.0,
        baseline_sig=None,
        baseline_plan=None,
        baseline_rows_est=1,
        baseline_bytes_est=1,
        explain_runner=lambda _sql: None,
        measure_fn=lambda _sql, _label: None,
        metric_fn=lambda _measured: 1.0,
        row_equiv_fn=lambda _measured: (True, "exact match"),
        static_report=None,
        output=output,
    )

    assert result.winner_sql is None
    assert result.history == [
        {"iteration": 1, "status": "model_failed", "candidate_sql": None, "plan_cost": None}
    ]


def test_standard_loop_model_failure_returns_enhancement_report(monkeypatch):
    from genie.skills.mcp_trino import research as research_mod
    from genie.skills.mcp_trino.preflight import LongQueryGateResult

    monkeypatch.setenv("GENIE_V48_SEED_DECOMPOSE", "0")
    monkeypatch.setattr(
        "genie.skills.trino_query.sql_static.analyze",
        lambda _sql: SimpleNamespace(findings=[], summary="clean"),
    )
    monkeypatch.setattr(
        "genie.skills.trino_query.sql_static.summary_line",
        lambda _report: "clean",
    )
    monkeypatch.setattr(
        research_mod,
        "_fetch_per_node_memory_limit",
        lambda _client: SimpleNamespace(bytes=None, source="default-fallback"),
    )
    monkeypatch.setattr(
        research_mod,
        "_measure_mcp",
        lambda *_args, **_kwargs: MeasureResult(
            median_metric=100.0,
            samples=[100.0],
            row_count=1,
            rows=[{"x": 1}],
            columns=["x"],
            metrics=RunMetrics(query_time_ms=100.0, wall_time_ms=100.0, cpu_time_ms=50.0),
        ),
    )
    monkeypatch.setattr(research_mod, "_build_mcp_explain_runner", lambda _client: (lambda _sql: None))
    monkeypatch.setattr(research_mod, "_execute_via_mcp", lambda *_args, **_kwargs: {"error": None})
    monkeypatch.setattr(research_mod, "_fetch_explain_analyze", lambda *_args, **_kwargs: ExplainAnalyzeResult(raw_text="", available=False))
    monkeypatch.setattr(research_mod, "_assemble_mcp_directions", lambda *_args, **_kwargs: ([], []))
    monkeypatch.setattr("genie.skills.mcp_trino.preflight.plan_cost", lambda *_args, **_kwargs: (None, None, None))
    monkeypatch.setattr(
        "genie.skills.mcp_trino.preflight.check_long_query_gate",
        lambda **_kwargs: LongQueryGateResult(ok=True),
    )
    monkeypatch.setattr("genie.skills.mcp_trino.rule_gate.build_rule_gate_summary", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("genie.skills.mcp_trino.rule_gate.format_rule_gate_for_prompt", lambda _summary: "")
    monkeypatch.setattr("genie.skills.mcp_trino.rule_gate.render_rule_gate_summary", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("genie.skills.mcp_trino.pre_execution_diagnosis.attribute_directions", lambda *_args, **_kwargs: [])
    monkeypatch.setattr("genie.skills.mcp_trino.pre_execution_diagnosis.format_attribution_report", lambda _outcomes: "")

    provider = MagicMock()
    provider.complete_text.side_effect = RuntimeError("provider down")
    client = MagicMock(spec=McpClient)
    client.config = McpConfig(url="http://mcp.test/mcp", enabled=True, timeout=1)

    report = research_mod.run_mcp_enhancement(
        client=client,
        sql="SELECT 1 AS x",
        metric_key="query_time_ms",
        max_iterations=1,
        verify_runs=1,
        provider=provider,
        model="test-model",
        reasoning="default",
        output=_output_mock(),
        build_prompt=lambda *_args, **_kwargs: "",
        long_query_opt_in=True,
    )

    assert isinstance(report, EnhancementReport)
    assert report.enhanced_sql == report.original_sql
    assert [it.status for it in report.iterations] == ["model_failed"]
    assert "provider down" in report.iterations[0].hypothesis
