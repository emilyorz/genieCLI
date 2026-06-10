"""Tests for v28 mode dispatch in _run_optimization_loop + no-data path."""
from __future__ import annotations

import subprocess
import sys
from unittest.mock import MagicMock, patch

import pytest

from genie.skills.mcp_trino.preflight import detect_no_data_reason
from genie.skills.trino_query.research import (
    _format_static_findings,
    _no_data_report,
    _run_no_data_path,
    _run_optimization_loop,
    run_trino_research,
)
from genie.skills.trino_query.sql_static import analyze as static_analyze
from genie.skills.mcp_trino.write_analysis import classify_write_operation


# ── detect_no_data_reason ─────────────────────────────────────────────────────

def test_detect_returns_empty_result_for_zero_rows():
    assert detect_no_data_reason(baseline_row_count=0) == "empty_result"


def test_detect_returns_none_for_positive_rows():
    assert detect_no_data_reason(baseline_row_count=42) is None


def test_detect_table_not_found_marker():
    exc = Exception("Query failed: TABLE_NOT_FOUND for hive.default.foo")
    assert detect_no_data_reason(baseline_exc=exc) == "table_not_found"


def test_detect_schema_not_found_marker():
    exc = Exception("SCHEMA_NOT_FOUND: hive.missing")
    assert detect_no_data_reason(baseline_exc=exc) == "table_not_found"


def test_detect_does_not_misclassify_unrelated_errors():
    exc = ConnectionError("connection refused at trino:8080")
    assert detect_no_data_reason(baseline_exc=exc) is None


def test_detect_handles_does_not_exist_phrase():
    exc = Exception("Catalog 'foo' does not exist")
    assert detect_no_data_reason(baseline_exc=exc) == "table_not_found"


@pytest.mark.parametrize("text", [
    "Session property query_max_execution_time does not exist",
    "Session property query_max_memory does not exist",
    "Function my_udf does not exist",
    "Role 'analyst' does not exist",
    "Column 'foo' does not exist",
    "Type my_type does not exist",
    "Materialized view 'm' does not exist",
    "Procedure p does not exist",
    "Property x does not exist",
    "MCP query failed: Session property X does not exist",
])
def test_detect_flips_non_container_does_not_exist_to_none(text):
    """S2: non-container subjects must NOT be classified as table_not_found."""
    assert detect_no_data_reason(baseline_exc=RuntimeError(text)) is None


@pytest.mark.parametrize("text", [
    "Table 'c.s.t' does not exist",
    "View 'a.v' does not exist",
    "Schema 'db.bad' does not exist",
    "line 1:8: Catalog 'foo' does not exist",
    "Query failed: TABLE_NOT_FOUND for hive.default.foo",
    "SCHEMA_NOT_FOUND: hive.missing",
    "CATALOG_NOT_FOUND: catalog 'hive' not found",
    "Table x not found",
])
def test_detect_holds_container_not_found_as_table_not_found(text):
    """S2: container subjects must still classify as table_not_found (hold/regression)."""
    assert detect_no_data_reason(baseline_exc=Exception(text)) == "table_not_found"


# ── _format_static_findings ───────────────────────────────────────────────────

def test_format_static_findings_empty():
    report = static_analyze("SELECT a, b FROM t WHERE a IS NOT NULL")
    assert _format_static_findings(report) == ""


def test_format_static_findings_renders_bullets():
    report = static_analyze("SELECT * FROM t WHERE col = NULL")
    text = _format_static_findings(report)
    assert "select-star" in text
    assert "null-unsafe-equals" in text
    assert "→" in text  # suggestion arrow


# ── write-operation analysis-only dispatch ───────────────────────────────────

def _output_mock():
    output = MagicMock()
    output.print = MagicMock()
    output.progress = MagicMock()
    output.error = MagicMock()
    return output


def _read_write_analysis_report(tmp_path):
    return next((tmp_path / "report").glob("trino-research-write-analysis-*.md")).read_text()


def test_write_analysis_module_import_does_not_load_mcp_client():
    code = """
import sys
import genie.skills.mcp_trino.write_analysis as wa
assert wa.classify_write_operation("EXPLAIN INSERT INTO x SELECT * FROM t").keyword == "INSERT"
for name in (
    "genie.skills.mcp_trino.client",
    "genie.skills.mcp_trino.research",
    "genie.skills.trino_query.connection",
):
    assert name not in sys.modules, f"{name} was imported"
"""
    subprocess.run([sys.executable, "-c", code], check=True)


@pytest.mark.parametrize(
    ("sql", "kind", "keyword"),
    [
        ("CREATE TABLE x AS SELECT * FROM t", "ctas", "CREATE"),
        ("INSERT INTO x SELECT * FROM t", "insert", "INSERT"),
        ("UPDATE x SET a = 1", "update", "UPDATE"),
        ("DELETE FROM x WHERE id = 1", "delete", "DELETE"),
        ("MERGE INTO x USING y ON x.id = y.id WHEN MATCHED THEN UPDATE SET a = y.a", "merge", "MERGE"),
        ("DROP TABLE x", "ddl", "DROP"),
        ("ALTER TABLE x ADD COLUMN y bigint", "ddl", "ALTER"),
        ("TRUNCATE TABLE x", "ddl", "TRUNCATE"),
        ("RENAME TABLE old_name TO new_name", "ddl", "RENAME"),
        ("GRANT SELECT ON TABLE x TO ROLE y", "ddl", "GRANT"),
        ("REVOKE SELECT ON TABLE x FROM ROLE y", "ddl", "REVOKE"),
        ("EXPLAIN INSERT INTO x SELECT * FROM t", "insert", "INSERT"),
        ("EXPLAIN CREATE TABLE x AS SELECT * FROM t", "ctas", "CREATE"),
        ("CALL some_proc(1)", "call", "CALL"),
        ("COMMIT", "transaction", "COMMIT"),
        ("ROLLBACK", "transaction", "ROLLBACK"),
        ("WITH s AS (SELECT * FROM t) INSERT INTO x SELECT * FROM s", "insert", "INSERT"),
        ("SELECT 1; RENAME TABLE old_name TO new_name", "unsafe_multi_statement", "RENAME"),
        ("SELECT 1; REVOKE SELECT ON TABLE x FROM ROLE y", "unsafe_multi_statement", "REVOKE"),
    ],
)
def test_write_operation_classifier_positive_cases(sql, kind, keyword):
    op = classify_write_operation(sql)
    assert op is not None
    assert op.kind == kind
    assert op.keyword == keyword


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM t",
        "WITH s AS (SELECT * FROM t) SELECT * FROM s",
        "SHOW TABLES",
        "DESCRIBE t",
        "DESC t",
        "EXPLAIN SELECT * FROM t",
        "/* RENAME TABLE old_name TO new_name */ SELECT 1",
        "-- REVOKE SELECT ON TABLE x FROM ROLE y\nSELECT 1",
    ],
)
def test_write_operation_classifier_negative_cases(sql):
    assert classify_write_operation(sql) is None


def test_direct_write_analysis_skips_measure_explain_and_loop(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    output = _output_mock()
    provider = MagicMock()
    provider.complete_text.return_value = "Advisory: review target table lifecycle."

    with patch("genie.skills.trino_query.research._run_optimization_loop", side_effect=AssertionError("loop called")), \
         patch("genie.skills.trino_query.research._measure", side_effect=AssertionError("measure called")), \
         patch("genie.skills.trino_query.research._execute_sql", side_effect=AssertionError("execute called")):
        run_trino_research(
            provider, {}, "test-model", "default", output, lambda *a, **kw: "",
            sql_text="CREATE TABLE x AS SELECT * FROM t",
            safe_limit=100,
            diagnose_only=True,
            metric="cpu_time_ms",
            iterations=1,
            runs=1,
        )

    reports = list((tmp_path / "report").glob("trino-research-write-analysis-*.md"))
    assert len(reports) == 1
    md = reports[0].read_text()
    assert "write-analysis" in md
    assert "| SQL executed | no |" in md
    assert "advisory, unverified" in md
    assert "Safe-limit was not applied" in md
    # LLM is used for advisory (single-shot rewrite + v40 per-fragment decompose);
    # the point is it is invoked for text generation, not how many times.
    provider.complete_text.assert_called()


def test_direct_write_analysis_renders_suggested_sql_with_one_semicolon(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    output = _output_mock()
    provider = MagicMock()
    provider.complete_text.return_value = (
        "Safer draft:\n"
        "```sql\n"
        "CREATE TABLE scratch.result AS\n"
        "SELECT order_id FROM source;;\n"
        "```\n"
        "Validate manually."
    )

    with patch("genie.skills.trino_query.research._run_optimization_loop", side_effect=AssertionError("loop called")), \
         patch("genie.skills.trino_query.research._measure", side_effect=AssertionError("measure called")), \
         patch("genie.skills.trino_query.research._execute_sql", side_effect=AssertionError("execute called")):
        run_trino_research(
            provider, {}, "test-model", "default", output, lambda *a, **kw: "",
            sql_text="CREATE TABLE x AS SELECT * FROM t",
            metric="cpu_time_ms",
            iterations=1,
            runs=1,
        )

    md = _read_write_analysis_report(tmp_path)
    assert md.index("## Suggested SQL Command (advisory, not executed)") < md.index("## Advisory Suggestions")
    assert "```sql\nCREATE TABLE scratch.result AS\nSELECT order_id FROM source;\n```" in md
    assert "SELECT order_id FROM source;;" not in md.split("## Advisory Suggestions", 1)[0]


def test_write_analysis_shows_spinner_during_llm_call(tmp_path, monkeypatch):
    """The advisory LLM call must run inside output.status() so interactive CTAS
    (Ctrl-D) shows a live spinner instead of looking frozen. Regression guard for
    the 2026-06-08 fix — the write-analysis path had no spinner around the call.
    """
    import contextlib

    monkeypatch.chdir(tmp_path)
    from genie.skills.mcp_trino.write_analysis import run_write_analysis_only

    events = []

    class _SpyOutput:
        def print(self, *a, **k): pass
        def progress(self, *a, **k): pass
        def error(self, *a, **k): pass

        @contextlib.contextmanager
        def status(self, message):
            events.append(("status_enter", message))
            try:
                yield
            finally:
                events.append(("status_exit", message))

    provider = MagicMock()

    def _complete(_req):
        events.append(("llm_call", None))
        return "Draft:\n```sql\nCREATE TABLE scratch.r AS SELECT a FROM s\n```\n- pushed filter down"

    provider.complete_text.side_effect = _complete

    run_write_analysis_only(
        provider, {}, "test-model", "default",
        "CREATE TABLE x AS SELECT a FROM s",
        _SpyOutput(), lambda *a, **kw: "", route="chat",
    )

    kinds = [e[0] for e in events]
    # The advisory single-shot LLM call must be wrapped by the spinner: the first
    # three events are enter → call → exit. (v40 then makes further decompose LLM
    # calls AFTER the spinner closes; those are not asserted here.)
    assert kinds[:3] == ["status_enter", "llm_call", "status_exit"], kinds
    md = _read_write_analysis_report(tmp_path)
    assert "## Suggested SQL Command (advisory, not executed)" in md
    assert "CREATE TABLE scratch.r AS SELECT a FROM s" in md


# ── v38: write/no-table runs the non-executing directed-diagnosis pipeline ─────

def test_ctas_write_analysis_runs_directed_diagnosis_on_inner_select(tmp_path, monkeypatch):
    """CTAS extracts its inner SELECT and runs the SAME non-executing optimization
    steps (R1-R10 + SQL-shape, ranked) the read-only diagnose-only path uses. The
    report must surface a Ranked Optimization Directions section computed on the
    inner query — not just a static dump. v38 feature.
    """
    monkeypatch.chdir(tmp_path)
    from genie.skills.mcp_trino.write_analysis import run_write_analysis_only

    output = _output_mock()
    provider = MagicMock()
    provider.complete_text.return_value = (
        "Draft:\n```sql\nCREATE TABLE m.result AS SELECT a, b FROM big WHERE a > 0\n```\n- pruned"
    )

    # select-star inside a CTAS → R2 fires on the INNER select
    result = run_write_analysis_only(
        provider, {}, "test-model", "default",
        "CREATE TABLE m.result AS SELECT * FROM big JOIN dim ON big.k = dim.k",
        output, lambda *a, **kw: "", route="chat",
    )

    assert result["analysis_target"] == "ctas_inner_select"
    assert result["analyzed_sql"].strip().upper().startswith("SELECT")
    md = result["report_markdown"]
    assert "## Ranked Optimization Directions" in md
    assert "computed on the CTAS inner SELECT" in md
    # a real direction row from the inner SELECT, not the CREATE wrapper
    assert "select-star" in md
    # safety contract unchanged
    assert "| SQL executed | no |" in md
    assert "| Verified optimization | no |" in md


def test_non_ctas_write_runs_diagnosis_on_whole_statement(tmp_path, monkeypatch):
    """A non-CTAS write (INSERT ... SELECT) is not unwrapped; the whole statement
    feeds the non-executing diagnosis without crashing, and the report still has a
    Ranked Optimization Directions section.
    """
    monkeypatch.chdir(tmp_path)
    from genie.skills.mcp_trino.write_analysis import run_write_analysis_only

    output = _output_mock()
    provider = MagicMock()
    provider.complete_text.return_value = "Advisory prose with no fenced SQL."

    result = run_write_analysis_only(
        provider, {}, "test-model", "default",
        "INSERT INTO dst SELECT * FROM src",
        output, lambda *a, **kw: "", route="chat",
    )

    assert result["analysis_target"] == "whole_statement"
    md = result["report_markdown"]
    assert "## Ranked Optimization Directions" in md
    assert "## Rule Gate" in md
    assert "| SQL executed | no |" in md


def test_write_analysis_diagnosis_uses_no_live_inputs(tmp_path, monkeypatch):
    """The directed diagnosis must NOT depend on a cluster. With no provider and a
    clean inner query, the path still completes and the rule gate reports the
    non-executing wording (never the old hard-coded 'normal optimizer gates were
    not run' string).
    """
    monkeypatch.chdir(tmp_path)
    from genie.skills.mcp_trino.write_analysis import run_write_analysis_only

    output = _output_mock()
    result = run_write_analysis_only(
        None, {}, "test-model", "default",
        "CREATE TABLE m.result AS SELECT id FROM t WHERE id IS NOT NULL",
        output, lambda *a, **kw: "", route="chat",
    )
    md = result["report_markdown"]
    assert "## Ranked Optimization Directions" in md
    assert "normal optimizer gates were not run" not in md
    assert "non-executing" in md.lower()


@pytest.mark.parametrize(
    "sql",
    [
        "RENAME TABLE old_name TO new_name",
        "REVOKE SELECT ON TABLE x FROM ROLE y",
    ],
)
def test_direct_rename_revoke_dispatch_to_write_analysis(tmp_path, monkeypatch, sql):
    monkeypatch.chdir(tmp_path)
    output = _output_mock()
    with patch("genie.skills.trino_query.research._run_optimization_loop", side_effect=AssertionError("loop called")), \
         patch("genie.skills.trino_query.research._measure", side_effect=AssertionError("measure called")), \
         patch("genie.skills.trino_query.research._execute_sql", side_effect=AssertionError("execute called")):
        run_trino_research(
            None, {}, "test-model", "default", output, lambda *a, **kw: "",
            sql_text=sql,
            metric="cpu_time_ms",
            iterations=1,
            runs=1,
        )
    md = _read_write_analysis_report(tmp_path)
    assert "| Kind | ddl |" in md
    assert ("| Keyword | RENAME |" in md) or ("| Keyword | REVOKE |" in md)


def test_direct_read_only_preserves_optimization_loop(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    output = _output_mock()
    fake_result = {
        "status": "completed",
        "baseline_metric": 10.0,
        "best_metric": 10.0,
        "total_improvement": 0.0,
        "improvement_pct": 0.0,
        "iterations": 0,
        "kept": 0,
        "baseline_rows": 1,
        "history": [],
        "best_sql": "SELECT * FROM t",
        "original_sql": "SELECT * FROM t",
    }
    with patch("genie.skills.trino_query.research._run_optimization_loop", return_value=fake_result) as loop:
        run_trino_research(
            None, {}, "test-model", "default", output, lambda *a, **kw: "",
            sql_text="SELECT * FROM t",
            metric="cpu_time_ms",
            iterations=1,
            runs=1,
        )
    loop.assert_called_once()


def test_chat_file_write_analysis_helper_does_not_touch_mcp(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    sql_file = tmp_path / "write.sql"
    sql_file.write_text("REVOKE SELECT ON TABLE x FROM ROLE y")
    output = _output_mock()

    from genie.chat import _try_run_trino_write_analysis_from_file

    with patch("genie.skills.mcp_trino.client.load_mcp_config", side_effect=AssertionError("load_mcp_config called")), \
         patch("genie.skills.mcp_trino.client.McpClient", side_effect=AssertionError("McpClient constructed")):
        handled = _try_run_trino_write_analysis_from_file(
            None, {}, "test-model", "default", output, lambda *a, **kw: "",
            {"sql_file": str(sql_file), "safe_limit": 100},
            route="chat",
        )

    assert handled is True
    md = _read_write_analysis_report(tmp_path)
    assert "| Keyword | REVOKE |" in md
    assert "Safe-limit was not applied" in md


def test_chat_file_write_analysis_prose_only_has_no_suggested_sql_section(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    sql_file = tmp_path / "write.sql"
    sql_file.write_text("INSERT INTO x SELECT * FROM y")
    output = _output_mock()

    from genie.chat import _try_run_trino_write_analysis_from_file

    provider = MagicMock()
    provider.complete_text.return_value = "Use a scratch destination and verify row counts manually."

    handled = _try_run_trino_write_analysis_from_file(
        provider, {}, "test-model", "default", output, lambda *a, **kw: "",
        {"sql_file": str(sql_file)},
        route="chat",
    )

    assert handled is True
    md = _read_write_analysis_report(tmp_path)
    assert "## Suggested SQL Command (advisory, not executed)" not in md
    assert "No complete SQL command was extracted from advisory text." in md


def test_chat_loop_file_write_analysis_startup_does_not_touch_mcp(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    sql_file = tmp_path / "write.sql"
    sql_file.write_text("RENAME TABLE old_name TO new_name")
    output = _output_mock()
    inputs = iter([f"/trino-research --file {sql_file}", "/exit"])

    from genie.chat import _chat_loop

    with patch("genie.input._read_input", side_effect=lambda *a, **kw: next(inputs)), \
         patch("genie.skills.trino_query.connection.status_line", return_value="not probed"), \
         patch("genie.skills.mcp_trino.client.load_mcp_config", side_effect=AssertionError("load_mcp_config called")) as load_mcp_config, \
         patch("genie.skills.mcp_trino.client.McpClient", side_effect=AssertionError("McpClient constructed")) as mcp_client:
        _chat_loop(
            None, {}, "test-model", "default", True, output, lambda *a, **kw: ""
        )

    load_mcp_config.assert_not_called()
    mcp_client.assert_not_called()
    md = _read_write_analysis_report(tmp_path)
    assert "| Keyword | RENAME |" in md
    assert "| MCP/Trino reached | no |" in md


def test_chat_file_read_only_helper_preserves_mcp_strictness(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    sql_file = tmp_path / "read.sql"
    sql_file.write_text("SELECT * FROM t")
    output = _output_mock()

    from genie.chat import _try_run_trino_write_analysis_from_file

    handled = _try_run_trino_write_analysis_from_file(
        None, {}, "test-model", "default", output, lambda *a, **kw: "",
        {"sql_file": str(sql_file)},
        route="chat",
    )

    assert handled is False
    assert not (tmp_path / "report").exists()


# ── _no_data_report ───────────────────────────────────────────────────────────

def test_no_data_report_marks_table_not_found():
    report = static_analyze("SELECT * FROM missing_table")
    md = _no_data_report(
        sql="SELECT * FROM missing_table",
        reason="table_not_found",
        static_report=report,
        llm_finishing=None,
        model="test-model",
    )
    assert "no-data path" in md
    assert "does not exist" in md
    assert "select-star" in md
    assert "Original SQL" in md


def test_no_data_report_renders_optimized_sql_section():
    """The optimized SQL must appear as its own copy-paste-ready block (advisory)."""
    report = static_analyze("SELECT DISTINCT a, count(*) FROM t GROUP BY a")
    opt = "SELECT a, count(*) FROM t GROUP BY a"
    md = _no_data_report(
        sql="SELECT DISTINCT a, count(*) FROM t GROUP BY a",
        reason="table_not_found",
        static_report=report,
        llm_finishing="Diagnosis...\n```sql\n" + opt + "\n```\nchecks...",
        model="test-model",
        optimized_sql=opt,
    )
    assert "## Optimized SQL" in md
    assert "UNVERIFIED" in md
    assert opt in md.split("## Optimized SQL", 1)[1].split("```sql", 1)[1]


def test_no_data_report_no_optimized_section_when_absent():
    """No optimized SQL extracted → no Optimized SQL section (backward-compatible)."""
    report = static_analyze("SELECT * FROM t")
    md = _no_data_report(
        sql="SELECT * FROM t", reason="table_not_found", static_report=report,
        llm_finishing=None, model="test-model",
    )
    assert "## Optimized SQL" not in md


def test_no_data_report_marks_empty_result():
    report = static_analyze("SELECT a FROM t")
    md = _no_data_report(
        sql="SELECT a FROM t",
        reason="empty_result",
        static_report=report,
        llm_finishing=None,
        model="test-model",
    )
    assert "0 rows" in md


def test_no_data_report_includes_llm_finishing_when_provided():
    report = static_analyze("SELECT * FROM t")
    md = _no_data_report(
        sql="SELECT * FROM t",
        reason="empty_result",
        static_report=report,
        llm_finishing="Recommended: list columns explicitly.",
        model="test-model",
    )
    assert "LLM finishing pass" in md
    assert "list columns explicitly" in md


def test_no_data_report_handles_clean_sql_with_no_findings():
    report = static_analyze("SELECT a, b FROM t WHERE a IS NOT NULL")
    md = _no_data_report(
        sql="SELECT a, b FROM t WHERE a IS NOT NULL",
        reason="empty_result",
        static_report=report,
        llm_finishing=None,
        model="test-model",
    )
    assert "No structural issues detected" in md


# ── _run_no_data_path (provider mocking, no LLM call) ─────────────────────────

def test_run_no_data_path_returns_no_data_status():
    output = MagicMock()
    report = static_analyze("SELECT * FROM t")
    result = _run_no_data_path(
        provider=None,  # provider=None skips LLM finishing
        model="test-model",
        reasoning="default",
        original_sql="SELECT * FROM t",
        no_data_reason="empty_result",
        static_report=report,
        baseline_exc=None,
        output=output,
    )
    assert result["status"] == "no_data"
    assert result["reason"] == "empty_result"
    assert result["best_sql"] == "SELECT * FROM t"
    assert any(f["rule_id"] == "select-star" for f in result["static_findings"])
    assert "report_markdown" in result and result["report_markdown"]


def test_run_no_data_path_still_optimizes_when_no_findings():
    """No static findings must NOT skip the optimizer.

    In the no-data path genieCLI is a pure static optimizer: a clean query the
    analyzer found nothing wrong with still gets one LLM finishing pass (and its
    live spinner), so the user always receives a copy-paste rewrite. Regression
    guard for the 2026-06-08 fix — previously the LLM pass was gated on
    `has_findings`, leaving clean queries silent with an Original-only report.
    """
    output = MagicMock()
    provider = MagicMock()
    provider.complete_text.return_value = (
        "Diagnosis: query is already well-shaped.\n"
        "```sql\nSELECT a FROM t WHERE a IS NOT NULL AND a > 0\n```\n"
        "Checks: confirm the table exists."
    )
    report = static_analyze("SELECT a FROM t WHERE a IS NOT NULL")
    assert not report.findings  # precondition: clean query, zero findings
    result = _run_no_data_path(
        provider=provider,
        model="test-model",
        reasoning="default",
        original_sql="SELECT a FROM t WHERE a IS NOT NULL",
        no_data_reason="empty_result",
        static_report=report,
        baseline_exc=None,
        output=output,
    )
    provider.complete_text.assert_called_once()
    assert result["llm_finishing"] is not None
    # Optimized SQL is extracted and surfaced for copy-paste, even with no findings.
    assert result["advisory_optimized_sql"] is not None
    assert "a > 0" in result["advisory_optimized_sql"]
    assert result["best_sql"] == result["advisory_optimized_sql"]
    assert "## Optimized SQL (advisory" in result["report_markdown"]


def test_run_no_data_path_skips_llm_when_provider_none():
    """provider=None is the only path that skips the LLM finishing pass."""
    output = MagicMock()
    report = static_analyze("SELECT a FROM t WHERE a IS NOT NULL")
    result = _run_no_data_path(
        provider=None,
        model="test-model",
        reasoning="default",
        original_sql="SELECT a FROM t WHERE a IS NOT NULL",
        no_data_reason="empty_result",
        static_report=report,
        baseline_exc=None,
        output=output,
    )
    assert result["llm_finishing"] is None
    assert result["advisory_optimized_sql"] is None


def test_run_no_data_path_calls_llm_when_findings_present():
    output = MagicMock()
    provider = MagicMock()
    provider.complete_text.return_value = "Diagnosis: rewrite needed."
    report = static_analyze("SELECT * FROM t WHERE col = NULL")
    result = _run_no_data_path(
        provider=provider,
        model="test-model",
        reasoning="default",
        original_sql="SELECT * FROM t WHERE col = NULL",
        no_data_reason="empty_result",
        static_report=report,
        baseline_exc=None,
        output=output,
    )
    provider.complete_text.assert_called_once()
    assert result["llm_finishing"] == "Diagnosis: rewrite needed."
    assert "rewrite needed" in result["report_markdown"]


def test_run_no_data_path_survives_llm_exception():
    output = MagicMock()
    provider = MagicMock()
    provider.complete_text.side_effect = RuntimeError("LLM provider down")
    report = static_analyze("SELECT * FROM t")
    result = _run_no_data_path(
        provider=provider,
        model="test-model",
        reasoning="default",
        original_sql="SELECT * FROM t",
        no_data_reason="empty_result",
        static_report=report,
        baseline_exc=None,
        output=output,
    )
    # LLM crash must not break the report
    assert result["status"] == "no_data"
    assert result["llm_finishing"] is None


# ── _run_optimization_loop dispatch (mocking _measure) ────────────────────────

def test_loop_dispatches_to_no_data_when_baseline_returns_zero_rows():
    """row_count == 0 → no-data path runs, iteration loop never entered."""
    output = MagicMock()
    fake_baseline = {
        "median": 100.0,
        "samples": [100.0],
        "row_count": 0,
        "rows": [],
        "metrics": MagicMock(wall_time_ms=50, cpu_time_ms=40, total_splits=1, processed_rows=0),
    }
    with patch("genie.skills.trino_query.research._measure", return_value=fake_baseline):
        result = _run_optimization_loop(
            provider=None,
            model="test-model",
            reasoning="default",
            original_sql="SELECT * FROM t",
            metric_key="cpu_time_ms",
            max_iterations=5,
            verify_runs=1,
            output=output,
            build_prompt=lambda *a, **kw: "",
        )
    assert result["status"] == "no_data"
    assert result["reason"] == "empty_result"


def test_loop_dispatches_to_no_data_when_baseline_raises_table_not_found():
    output = MagicMock()
    exc = Exception("Query failed: TABLE_NOT_FOUND for catalog.schema.bogus")
    with patch("genie.skills.trino_query.research._measure", side_effect=exc):
        result = _run_optimization_loop(
            provider=None,
            model="test-model",
            reasoning="default",
            original_sql="SELECT * FROM bogus",
            metric_key="cpu_time_ms",
            max_iterations=5,
            verify_runs=1,
            output=output,
            build_prompt=lambda *a, **kw: "",
        )
    assert result["status"] == "no_data"
    assert result["reason"] == "table_not_found"
    assert "TABLE_NOT_FOUND" in (result.get("baseline_error") or "")


def test_loop_propagates_real_failure_when_not_no_data_shape():
    """Connection refused / generic errors must NOT be misclassified as no-data."""
    output = MagicMock()
    exc = ConnectionError("connection refused")
    with patch("genie.skills.trino_query.research._measure", side_effect=exc):
        result = _run_optimization_loop(
            provider=None,
            model="test-model",
            reasoning="default",
            original_sql="SELECT * FROM t",
            metric_key="cpu_time_ms",
            max_iterations=5,
            verify_runs=1,
            output=output,
            build_prompt=lambda *a, **kw: "",
        )
    assert result["status"] == "failed"
    assert "connection refused" in result["error"]
