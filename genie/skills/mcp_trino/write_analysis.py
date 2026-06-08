"""Offline write-operation analysis helpers for Trino research.

This module is intentionally import-safe: it must not import MCP clients,
Trino drivers, config loaders, or other live execution surfaces at module load.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

from genie.core.sql_extraction import extract_sql_from_reply


@dataclass(frozen=True)
class WriteOperation:
    """Offline classification for SQL that must not enter optimizer execution."""
    kind: str
    keyword: str
    reason: str
    statement_count: int = 1


_WRITE_KEYWORDS = {
    "INSERT": "insert",
    "MERGE": "merge",
    "UPDATE": "update",
    "DELETE": "delete",
    "CALL": "call",
    "COMMIT": "transaction",
    "ROLLBACK": "transaction",
}
_DDL_KEYWORDS = {"CREATE", "DROP", "ALTER", "TRUNCATE", "RENAME", "GRANT", "REVOKE"}
_READONLY_STARTERS = {"SELECT", "WITH", "SHOW", "DESCRIBE", "DESC", "EXPLAIN"}
_SIDE_EFFECT_RE = re.compile(
    r"\b(INSERT|MERGE|UPDATE|DELETE|CREATE|DROP|ALTER|TRUNCATE|RENAME|GRANT|REVOKE|CALL|COMMIT|ROLLBACK)\b",
    flags=re.IGNORECASE,
)


def _strip_sql_comments(sql: str) -> str:
    sql = re.sub(r"/\*.*?\*/", " ", sql, flags=re.DOTALL)
    sql = re.sub(r"--[^\n\r]*", " ", sql)
    return sql


def _split_sql_statements(sql: str) -> list[str]:
    statements = []
    current = []
    quote: str | None = None
    i = 0
    while i < len(sql):
        ch = sql[i]
        if quote:
            current.append(ch)
            if ch == quote:
                if i + 1 < len(sql) and sql[i + 1] == quote:
                    current.append(sql[i + 1])
                    i += 1
                else:
                    quote = None
        elif ch in ("'", '"'):
            quote = ch
            current.append(ch)
        elif ch == ";":
            part = "".join(current).strip()
            if part:
                statements.append(part)
            current = []
        else:
            current.append(ch)
        i += 1
    part = "".join(current).strip()
    if part:
        statements.append(part)
    return statements


def _first_sql_keyword(statement: str) -> str:
    match = re.search(r"\b([A-Za-z_][A-Za-z0-9_]*)\b", statement)
    return match.group(1).upper() if match else ""


def _strip_explain_prefix(statement: str) -> str:
    """Return the SQL target of EXPLAIN when it is statically visible."""
    rest = re.sub(r"^\s*EXPLAIN\b", "", statement, count=1, flags=re.IGNORECASE).lstrip()
    rest = re.sub(r"^(?:ANALYZE|VERBOSE)\b", "", rest, count=1, flags=re.IGNORECASE).lstrip()
    while rest.startswith("("):
        depth = 0
        quote: str | None = None
        for idx, ch in enumerate(rest):
            if quote:
                if ch == quote:
                    quote = None
                continue
            if ch in ("'", '"'):
                quote = ch
            elif ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
                if depth == 0:
                    rest = rest[idx + 1:].lstrip()
                    break
        else:
            return rest
    return rest


def _classify_single_write_statement(statement: str, statement_count: int = 1) -> WriteOperation | None:
    compact = " ".join(statement.strip().split())
    if not compact:
        return None
    upper = compact.upper()
    first = _first_sql_keyword(compact)

    if first == "EXPLAIN":
        target = _strip_explain_prefix(compact)
        target_op = _classify_single_write_statement(target, statement_count)
        if target_op is not None:
            return WriteOperation(
                target_op.kind,
                target_op.keyword,
                f"EXPLAIN target is side-effecting: {target_op.reason}",
                statement_count,
            )
        return None

    ctas_patterns = (
        r"^CREATE\s+(?:OR\s+REPLACE\s+)?TABLE\b.*\bAS\s+SELECT\b",
        r"^CREATE\s+(?:OR\s+REPLACE\s+)?TABLE\b.*\bAS\s+WITH\b",
    )
    if any(re.search(pattern, upper, flags=re.DOTALL) for pattern in ctas_patterns):
        return WriteOperation("ctas", "CREATE", "CREATE TABLE AS SELECT writes table data", statement_count)

    if first == "WITH":
        match = re.search(r"\)\s*(INSERT|MERGE|UPDATE|DELETE)\b", upper)
        if match:
            keyword = match.group(1)
            return WriteOperation(
                _WRITE_KEYWORDS[keyword],
                keyword,
                f"CTE-leading {keyword} is side-effecting",
                statement_count,
            )
        match = re.search(r"\b(INSERT|MERGE|UPDATE|DELETE)\b", upper)
        if match:
            keyword = match.group(1)
            return WriteOperation(
                _WRITE_KEYWORDS[keyword],
                keyword,
                f"CTE-leading SQL contains side-effecting {keyword}",
                statement_count,
            )
        return None

    if first in _WRITE_KEYWORDS:
        return WriteOperation(
            _WRITE_KEYWORDS[first],
            first,
            f"{first} is a side-effecting Trino operation",
            statement_count,
        )
    if first in _DDL_KEYWORDS:
        return WriteOperation("ddl", first, f"{first} is DDL or privilege control", statement_count)
    if first in _READONLY_STARTERS:
        return None
    match = _SIDE_EFFECT_RE.search(upper)
    if match:
        keyword = match.group(1).upper()
        kind = _WRITE_KEYWORDS.get(keyword, "ddl")
        return WriteOperation(kind, keyword, f"SQL contains side-effecting keyword {keyword}", statement_count)
    return None


def classify_write_operation(sql: str) -> WriteOperation | None:
    """Return write-operation metadata for side-effecting SQL; read-only returns None."""
    cleaned = _strip_sql_comments(sql or "")
    statements = _split_sql_statements(cleaned)
    if not statements:
        return None
    statement_count = len(statements)
    write_ops = [
        op for op in (
            _classify_single_write_statement(statement, statement_count)
            for statement in statements
        )
        if op is not None
    ]
    if statement_count > 1 and write_ops:
        first_write = write_ops[0]
        return WriteOperation(
            "unsafe_multi_statement",
            first_write.keyword,
            "Multi-statement SQL contains a side-effecting operation",
            statement_count,
        )
    return write_ops[0] if write_ops else None


def _static_findings_dict(static_report) -> dict:
    if static_report is None:
        return {"summary": "skipped", "parse_error": None, "findings": []}
    return static_report.to_dict() if hasattr(static_report, "to_dict") else {
        "summary": getattr(static_report, "summary", "unknown"),
        "parse_error": getattr(static_report, "parse_error", None),
        "findings": [],
    }


def _write_analysis_prompt(
    sql: str,
    operation: WriteOperation,
    static_report,
    *,
    directions_block: str = "",
    rule_gate_block: str = "",
    inner_sql: str | None = None,
) -> str:
    static = _static_findings_dict(static_report)
    parts = [
        "You are reviewing side-effecting Trino SQL. This SQL will not be executed, "
        "EXPLAINed, benchmarked, or MCP-validated by this tool. Provide advisory, "
        "unverified suggestions only. Do not claim equivalence or safety.\n",
        f"Operation: kind={operation.kind}, keyword={operation.keyword}, reason={operation.reason}",
        f"Static analysis: {static.get('summary')}",
        f"Parse warning: {static.get('parse_error') or 'none'}",
    ]
    if inner_sql is not None:
        parts.append(
            "\nThis is a CREATE TABLE AS SELECT. The deterministic optimization "
            "directions below were computed on its INNER query:\n"
            f"```sql\n{inner_sql.rstrip()}\n```"
        )
    if directions_block:
        parts.append(
            "\nDeterministic optimization directions (ranked, zero-cost static "
            "diagnosis — base your rewrite on these, do not invent new ones):\n"
            f"{directions_block}"
        )
    if rule_gate_block:
        parts.append(f"\n{rule_gate_block}")
    parts.append(f"\nFull SQL:\n```sql\n{sql.rstrip()}\n```\n")
    parts.append(
        "Respond with TWO parts in this order:\n"
        "1. A complete, copy-paste-ready optimized rewrite of the WHOLE statement "
        "in a single ```sql fenced block. Preserve the side-effecting shape "
        "(e.g. for CREATE TABLE AS SELECT keep the CREATE TABLE ... AS wrapper and "
        "optimize the inner SELECT: join order, predicate/filter pushdown, CTE "
        "materialization vs re-scan, projection pruning). Apply the ranked "
        "directions above where they fit. If the statement is already well-shaped, "
        "return it unchanged and say so. Always include the fenced SQL block.\n"
        "2. Below the SQL block, a short bullet list of the changes and any checks "
        "the user must perform before running it (it is unverified)."
    )
    return "\n".join(parts)


def _normalize_display_sql(sql: str) -> str:
    return sql.rstrip().rstrip(";") + ";"


def render_write_analysis_report(result: dict) -> str:
    operation = result["operation"]
    static = result["static_analysis"]
    lines = [
        "# Trino Write-Operation Analysis",
        "",
        "## Safety Status",
        "",
        "| Field | Value |",
        "|---|---|",
        "| Mode | write-analysis |",
        "| SQL executed | no |",
        "| EXPLAIN run | no |",
        "| MCP/Trino reached | no |",
        "| Verified optimization | no |",
        "| Advice status | advisory, unverified |",
        "",
        "## Operation",
        "",
        "| Field | Value |",
        "|---|---|",
        f"| Kind | {operation['kind']} |",
        f"| Keyword | {operation['keyword']} |",
        f"| Reason | {operation['reason']} |",
        f"| Statement count | {operation['statement_count']} |",
        f"| Route | {result.get('route') or ''} |",
        f"| Source | {result.get('sql_source') or 'sql_text'} |",
        "",
        "## Deterministic Findings",
        "",
    ]
    if static.get("parse_error"):
        lines += [f"Parse warning: {static['parse_error']}", ""]
    if not static.get("findings"):
        lines += ["No deterministic findings from static analysis.", ""]
    else:
        lines += [
            f"Summary: {static.get('summary', 'unknown')}",
            "",
            "| # | Severity | Rule | Line | Message | Suggestion |",
            "|---|---|---|---|---|---|",
        ]
        for idx, finding in enumerate(static["findings"], 1):
            msg = str(finding.get("message", "")).replace("|", "\\|")
            sug = str(finding.get("suggestion", "")).replace("|", "\\|")
            lines.append(
                f"| {idx} | {finding.get('severity', '')} | {finding.get('rule_id', '')} | "
                f"{finding.get('line', '')} | {msg} | {sug} |"
            )
        lines.append("")

    # Ranked optimization directions — the SAME non-executing directed diagnosis
    # the read-only --diagnose-only path produces (R1-R9 static + SQL-shape, then
    # ranked). For CTAS the directions are computed on the inner SELECT.
    target = result.get("analysis_target")
    if target == "ctas_inner_select":
        lines += [
            "## Ranked Optimization Directions",
            "",
            "_Directions below were computed on the CTAS inner SELECT (the part worth optimizing)._",
            "",
        ]
    else:
        lines += ["## Ranked Optimization Directions", ""]
    rule_gate = result.get("rule_gate_summary")
    gate_items = list(getattr(rule_gate, "items", ()) or ()) if rule_gate else []
    if gate_items:
        lines += [
            "| # | Action | Severity | Rule / Kind | Direction | Evidence |",
            "|---|---|---|---|---|---|",
        ]
        for idx, item in enumerate(gate_items, 1):
            sug = str(getattr(item, "suggestion", "")).replace("|", "\\|")
            rid = str(getattr(item, "rule_id", "")).replace("|", "\\|")
            ev = str(getattr(item, "evidence", "")).replace("|", "\\|")
            lines.append(
                f"| {idx} | {getattr(item, 'action', '')} | {getattr(item, 'severity', '')} | "
                f"{rid} | {sug} | {ev} |"
            )
        lines.append("")
    else:
        lines += [
            "No structural optimization directions surfaced from non-executing analysis.",
            "",
        ]

    lines += [
        "## Rule Gate",
        "",
    ]
    if rule_gate is not None and getattr(rule_gate, "has_findings", False):
        counts = rule_gate.counts
        from genie.skills.mcp_trino.rule_gate import (
            ACTION_ADVISE,
            ACTION_BLOCK,
            ACTION_REWRITE,
        )
        lines += [
            f"Pre-AI rule gate (non-executing; result equivalence remains authoritative): "
            f"block={counts[ACTION_BLOCK]}, rewrite={counts[ACTION_REWRITE]}, "
            f"advise={counts[ACTION_ADVISE]}.",
            "",
            "These gate the advisory rewrite below; no SQL was auto-mutated and nothing was executed.",
            "",
        ]
    else:
        lines += [
            "Non-executing rule gate produced no findings. Static directions above are advisory inputs only.",
            "",
        ]
    suggested_sql = result.get("suggested_sql")
    if suggested_sql:
        lines += [
            "## Suggested SQL Command (advisory, not executed)",
            "",
            "This SQL was extracted from advisory LLM output. It was not executed, EXPLAINed, benchmarked, MCP-validated, or row-equivalence verified.",
            "",
            "```sql",
            _normalize_display_sql(str(suggested_sql)),
            "```",
            "",
        ]
    lines += [
        "## Advisory Suggestions",
        "",
    ]
    if result.get("llm_advice"):
        lines += [str(result["llm_advice"]).rstrip(), ""]
        if not suggested_sql:
            lines += ["No complete SQL command was extracted from advisory text.", ""]
    else:
        llm_error = result.get("llm_error")
        if llm_error:
            lines += [f"LLM advice unavailable: {llm_error}", ""]
        else:
            lines += ["LLM advice unavailable; deterministic findings only.", ""]
    if result.get("safe_limit"):
        lines += [
            "Safe-limit was not applied because write-analysis never wraps write operations.",
            "",
        ]
    lines += [
        "Suggestions were not executed, benchmarked, EXPLAINed, MCP-validated, or row-equivalence verified.",
        "",
        "## Original SQL",
        "",
        "```sql",
        result["original_sql"].rstrip(),
        "```",
        "",
    ]
    return "\n".join(lines)


def save_write_analysis_report(result: dict, report_dir: Path | None = None) -> Path:
    target_dir = report_dir or (Path.cwd() / "report")
    target_dir.mkdir(parents=True, exist_ok=True)
    report_path = target_dir / f"trino-research-write-analysis-{datetime.now().strftime('%Y%m%d-%H%M%S')}.md"
    report_path.write_text(result["report_markdown"])
    return report_path


def print_write_analysis_summary(output, result: dict) -> None:
    static_summary = result.get("static_analysis", {}).get("summary") or "skipped"
    output.print("== Trino Write-Operation Analysis ==")
    output.print("Mode: write-analysis (no SQL executed)")
    output.print(f"Operation: {result['operation']['kind']}")
    output.print(f"Static analysis: {static_summary}")
    if result.get("safe_limit"):
        output.print("Safe-limit: not applied to write-analysis")
    output.print(f"Report saved: {result.get('report_path')}")


def run_write_analysis_only(
    provider,
    cfg,
    model: str,
    reasoning: str,
    sql: str,
    output,
    build_prompt,
    *,
    sql_source: str | None = None,
    route: str = "chat",
    report_dir: Path | None = None,
    safe_limit: Optional[int] = None,
) -> dict:
    """Run offline static + optional LLM advice for write SQL; touch no live Trino/MCP."""
    from genie.core.provider import CompletionRequest
    from genie.core.sql_extraction import extract_ctas_inner_select
    from genie.session.manager import new_msg
    from genie.skills.trino_query.sql_static import analyze as static_analyze
    from genie.skills.mcp_trino.pre_execution_diagnosis import (
        format_directions_for_prompt,
        format_directions_report,
        pre_execution_diagnosis,
    )
    from genie.skills.mcp_trino.rule_gate import (
        build_rule_gate_summary,
        format_rule_gate_for_prompt,
    )

    operation = classify_write_operation(sql)
    if operation is None:
        raise ValueError("run_write_analysis_only called with read-only SQL")

    # CTAS optimization value lives in the inner SELECT. Strip the
    # CREATE TABLE ... AS wrapper so the read-only optimization steps analyze the
    # query, not the DDL shell. Falls back to the whole statement when not a CTAS
    # or the inner query can't be isolated.
    inner_sql = extract_ctas_inner_select(sql) or sql
    analysis_is_ctas_inner = inner_sql != sql

    try:
        static_report = static_analyze(inner_sql)
    except Exception:
        static_report = None

    # Non-executing directed diagnosis: the SAME zero-cost optimization pipeline
    # the read-only --diagnose-only path uses. All live-cluster inputs are None
    # (explain_cost / table_metadata / peak_memory) so the contributors that need
    # Trino degrade to empty — but R1-R9 static + SQL-shape (CTE depth, repeated
    # raw scans) directions are produced, then ranked. No cluster is touched.
    try:
        directions = pre_execution_diagnosis(
            inner_sql,
            static_report=static_report,
            explain_cost=None,
            table_metadata=None,
            peak_memory_bytes=None,
        )
    except Exception:
        directions = []
    try:
        rule_gate = build_rule_gate_summary(static_report, directions)
    except Exception:
        rule_gate = None
    diagnosis_reason = (
        "write-operation (CTAS inner SELECT): non-executing directed diagnosis"
        if analysis_is_ctas_inner
        else "write-operation: non-executing directed diagnosis (no query executed)"
    )
    try:
        directed_report_md = format_directions_report(
            directions, sql=inner_sql, reason=diagnosis_reason, model=model,
        )
    except Exception:
        directed_report_md = ""

    llm_advice = None
    llm_error = None
    suggested_sql = None
    if provider is not None:
        try:
            req = CompletionRequest(
                messages=[
                    new_msg("system", "You provide advisory-only Trino SQL review."),
                    new_msg("user", _write_analysis_prompt(
                        sql, operation, static_report,
                        directions_block=format_directions_for_prompt(directions),
                        rule_gate_block=(
                            format_rule_gate_for_prompt(rule_gate) if rule_gate else ""
                        ),
                        inner_sql=inner_sql if analysis_is_ctas_inner else None,
                    )),
                ],
                model=model,
                reasoning=reasoning,
            )
            # Live spinner: the advisory LLM call is the long, silent step after
            # Ctrl-D in interactive mode. Without it the CLI looks frozen until
            # the report is written. status() is a no-op on MachineSink.
            if output is not None and hasattr(output, "status"):
                with output.status("Reviewing write operation and drafting an optimized rewrite (LLM, this can take a while)..."):
                    llm_advice = provider.complete_text(req)
            else:
                llm_advice = provider.complete_text(req)
            if llm_advice:
                suggested_sql = extract_sql_from_reply(str(llm_advice)) or None
        except Exception as exc:
            llm_error = str(exc)

    result = {
        "status": "write_analysis",
        "mode": "write-analysis",
        "route": route,
        "sql_source": sql_source,
        "operation": asdict(operation),
        "executed": False,
        "verified": False,
        "advisory_only": True,
        "live_dependencies_touched": False,
        "static_analysis": _static_findings_dict(static_report),
        "directed_report_markdown": directed_report_md,
        "rule_gate_summary": rule_gate,
        "analysis_target": "ctas_inner_select" if analysis_is_ctas_inner else "whole_statement",
        "analyzed_sql": inner_sql,
        "llm_advice": llm_advice,
        "llm_error": llm_error,
        "suggested_sql": suggested_sql,
        "report_path": None,
        "original_sql": sql,
        "safe_limit": safe_limit,
    }
    result["report_markdown"] = render_write_analysis_report(result)
    report_path = save_write_analysis_report(result, report_dir=report_dir)
    result["report_path"] = str(report_path)
    result["report_markdown"] = render_write_analysis_report(result)
    report_path.write_text(result["report_markdown"])
    if output is not None:
        print_write_analysis_summary(output, result)
    return result
