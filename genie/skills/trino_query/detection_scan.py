"""detection_scan — pure static SQL scan with 4-action tier classification.

Maps sql_static findings through the rule_gate tier table and returns
DetectionFinding objects.  No MCP, no cluster, no network.  Never raises.

Public API:
    scan_sql(sql: str) -> list[DetectionFinding]
"""
from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class DetectionFinding:
    rule_id: str    # e.g. "cartesian-join", "select-star"; sentinels: "none", "parse-error"
    action: str     # one of: "block" | "rewrite" | "advise" | "pass"
    severity: str   # "high" | "medium" | "low"
    message: str
    suggestion: str
    line: int


# Regex to extract line number from evidence string "static:<rule_id>@L<n>"
_EVIDENCE_LINE_RE = re.compile(r"@L(\d+)$")


def _line_from_evidence(evidence: str) -> int:
    """Extract line number from a static evidence string, default 1."""
    m = _EVIDENCE_LINE_RE.search(evidence or "")
    if m:
        try:
            line = int(m.group(1))
            return max(1, line)
        except (ValueError, TypeError):
            pass
    return 1


def scan_sql(sql: str) -> list[DetectionFinding]:
    """Scan *sql* (whole query OR bare fragment) and return DetectionFindings.

    Behavior:
    - Calls sql_static.analyze(sql).
    - If zero parseable statements AND no parse_error, the input is a fragment.
      Re-analyzes using a wrapper chosen by the fragment's PARSED SHAPE:
        * boolean/condition node  -> ``SELECT 1 WHERE <frag>``
        * relation/query node     -> ``SELECT * FROM (<frag>) _frag``
          (any select-star finding from the synthetic scaffold is dropped)
        * parse_one raises or neither wrapper analyzes -> parse-error
    - Builds DetectionFindings via rule_gate.build_rule_gate_summary.
    - Clean SQL (zero items) -> ONE injected ``none``/``pass`` finding.
    - Parse error -> ONE ``parse-error``/``advise`` finding.
    - NEVER raises.
    """
    from genie.skills.trino_query.sql_static import analyze

    # Function-local import to guard against any future circular-import risk
    # (detection_scan lives in trino_query/; rule_gate lives in mcp_trino/).
    from genie.skills.mcp_trino.rule_gate import build_rule_gate_summary

    try:
        report = analyze(sql)
    except Exception as exc:
        return [DetectionFinding(
            rule_id="parse-error",
            action="advise",
            severity="low",
            message=str(exc),
            suggestion="",
            line=1,
        )]

    # Check if we have a parse error already
    if report.parse_error:
        return [DetectionFinding(
            rule_id="parse-error",
            action="advise",
            severity="low",
            message=report.parse_error,
            suggestion="",
            line=1,
        )]

    # If zero parseable statements and no parse_error -> fragment path
    # Detect by checking whether any statements were found
    # sql_static.analyze returns findings only for parseable top-level statements.
    # We need to re-analyze with a wrapper if there are no findings AND no parse_error
    # but also no parseable statements in the raw parse.
    # We detect "fragment" by trying to parse it as a full statement ourselves.
    fragment_mode = _is_fragment(sql)
    if fragment_mode:
        report = _analyze_fragment(sql, analyze)
        if report is None:
            # parse failure on fragment wrapping
            return [DetectionFinding(
                rule_id="parse-error",
                action="advise",
                severity="low",
                message="fragment could not be analyzed",
                suggestion="",
                line=1,
            )]
        if report.parse_error:
            return [DetectionFinding(
                rule_id="parse-error",
                action="advise",
                severity="low",
                message=report.parse_error,
                suggestion="",
                line=1,
            )]

    # Build rule gate summary
    try:
        summary = build_rule_gate_summary(static_report=report)
    except Exception:
        summary = None

    # Extract items
    items = []
    if summary is not None:
        try:
            items = list(summary.items)
        except Exception:
            items = []

    if not items:
        # Inject a single PASS finding
        return [DetectionFinding(
            rule_id="none",
            action="pass",
            severity="low",
            message="no anti-patterns detected",
            suggestion="",
            line=1,
        )]

    findings = []
    for item in items:
        line = _line_from_evidence(getattr(item, "evidence", "") or "")
        # Clamp wrapper-induced offsets: fragments get line 1
        if fragment_mode:
            line = 1
        findings.append(DetectionFinding(
            rule_id=str(getattr(item, "rule_id", "unknown")),
            action=str(getattr(item, "action", "advise")),
            severity=str(getattr(item, "severity", "low")),
            message=str(getattr(item, "message", "")),
            suggestion=str(getattr(item, "suggestion", "")),
            line=line,
        ))
    return findings


def _is_fragment(sql: str) -> bool:
    """Return True if *sql* looks like a fragment (not a full parseable statement)."""
    if not sql or not sql.strip():
        return False
    try:
        import sqlglot
        import sqlglot.errors as sge
        import sqlglot.expressions as exp

        statements = sqlglot.parse(sql, read="trino", error_level=sge.ErrorLevel.IGNORE)
        parseable = [
            s for s in (statements or [])
            if s is not None and isinstance(
                s, (exp.Select, exp.Insert, exp.Update, exp.Delete,
                    exp.Create, exp.Drop, exp.With)
            )
        ]
        return len(parseable) == 0
    except Exception:
        return False


def _analyze_fragment(sql: str, analyze_fn):
    """Wrap a fragment in the appropriate scaffold and re-analyze.

    Choose wrapper by PARSED SHAPE of the fragment (not "first that parses"):
    - boolean/condition node -> SELECT 1 WHERE <frag>
    - relation/query node    -> SELECT * FROM (<frag>) _frag
      (drop any select-star finding from the synthetic scaffold)
    - parse_one raises or neither -> return a parse-error report
    """
    try:
        import sqlglot
        import sqlglot.expressions as exp
        from genie.skills.trino_query.sql_static import StaticAnalysisReport
    except ImportError:
        return None

    # Classify fragment shape
    frag_node = None
    try:
        frag_node = sqlglot.parse_one(sql.strip(), read="trino")
    except Exception:
        frag_node = None

    if frag_node is None:
        # Can't parse at all — return parse error
        return StaticAnalysisReport(parse_error=f"SQL parse failed: fragment could not be parsed")

    # Determine shape
    _CONDITION_TYPES = (
        exp.EQ, exp.NEQ, exp.And, exp.Or, exp.Is, exp.Not, exp.Paren,
        exp.GT, exp.GTE, exp.LT, exp.LTE,
        exp.Between, exp.In, exp.Like, exp.ILike,
        exp.Condition, exp.Predicate,
    )
    _RELATION_TYPES = (
        exp.Select, exp.Subquery, exp.Table, exp.Join, exp.With,
        exp.Union, exp.Intersect, exp.Except,
    )

    is_condition = isinstance(frag_node, _CONDITION_TYPES)
    is_relation = isinstance(frag_node, _RELATION_TYPES)

    if is_condition:
        # Predicate path: wrap as WHERE clause
        wrapped = f"SELECT 1 WHERE {sql.strip()}"
        try:
            report = analyze_fn(wrapped)
            return report
        except Exception as exc:
            return StaticAnalysisReport(parse_error=str(exc))
    elif is_relation:
        # Relation path: wrap as subquery
        wrapped = f"SELECT * FROM ({sql.strip()}) _frag"
        try:
            report = analyze_fn(wrapped)
            if report and report.findings:
                # Drop select-star findings that come from the synthetic scaffold
                from genie.skills.trino_query.sql_static.rule_ids import RULE_SELECT_STAR
                filtered = [
                    f for f in report.findings
                    if f.rule_id != RULE_SELECT_STAR
                ]
                from genie.skills.trino_query.sql_static import StaticAnalysisReport as SAR
                return SAR(findings=filtered, parse_error=report.parse_error)
            return report
        except Exception as exc:
            return StaticAnalysisReport(parse_error=str(exc))
    else:
        # Unknown shape — try predicate first, then relation, then give up
        for wrapper_fmt, drop_select_star in [
            (f"SELECT 1 WHERE {sql.strip()}", False),
            (f"SELECT * FROM ({sql.strip()}) _frag", True),
        ]:
            try:
                report = analyze_fn(wrapper_fmt)
                if report and not report.parse_error:
                    if drop_select_star and report.findings:
                        from genie.skills.trino_query.sql_static.rule_ids import RULE_SELECT_STAR
                        from genie.skills.trino_query.sql_static import StaticAnalysisReport as SAR
                        filtered = [f for f in report.findings if f.rule_id != RULE_SELECT_STAR]
                        return SAR(findings=filtered, parse_error=None)
                    return report
            except Exception:
                continue
        return StaticAnalysisReport(parse_error="fragment could not be analyzed with any wrapper")


__all__ = ["DetectionFinding", "scan_sql"]
