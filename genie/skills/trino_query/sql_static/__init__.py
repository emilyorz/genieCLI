"""Static SQL analysis for /trino-research no-data path + has-data injection.

Independent of the Oracle-residual linter at ``genie.core.lint_*``. Focused on
Trino query-shape issues that hold regardless of whether the table has rows.

Public API:
    analyze(sql) -> StaticAnalysisReport
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class Finding:
    severity: str       # "high" | "medium" | "low"
    rule_id: str        # e.g. "cartesian-join", "select-star"
    message: str
    suggestion: str
    line: int = 1


@dataclass
class StaticAnalysisReport:
    findings: list[Finding] = field(default_factory=list)
    parse_error: str | None = None

    @property
    def summary(self) -> str:
        h = sum(1 for f in self.findings if f.severity == "high")
        m = sum(1 for f in self.findings if f.severity == "medium")
        low = sum(1 for f in self.findings if f.severity == "low")
        return f"{h} high, {m} medium, {low} low"

    @property
    def by_severity(self) -> dict[str, list[Finding]]:
        out: dict[str, list[Finding]] = {"high": [], "medium": [], "low": []}
        for f in self.findings:
            out.setdefault(f.severity, []).append(f)
        return out

    def to_dict(self) -> dict:
        return {
            "findings": [
                {
                    "severity": f.severity,
                    "rule_id": f.rule_id,
                    "message": f.message,
                    "suggestion": f.suggestion,
                    "line": f.line,
                }
                for f in self.findings
            ],
            "summary": self.summary,
            "parse_error": self.parse_error,
        }


def _load_rules() -> list:
    from .rules import (
        r1_cartesian_join,
        r2_select_star,
        r3_distinct_after_group_by,
        r4_order_by_in_subquery,
        r5_subquery_in_select,
        r6_predicate_pushdown,
        r7_null_unsafe_equals,
        r8_redundant_cast,
    )
    return [
        r1_cartesian_join.apply,
        r2_select_star.apply,
        r3_distinct_after_group_by.apply,
        r4_order_by_in_subquery.apply,
        r5_subquery_in_select.apply,
        r6_predicate_pushdown.apply,
        r7_null_unsafe_equals.apply,
        r8_redundant_cast.apply,
    ]


def analyze(sql: str) -> StaticAnalysisReport:
    """Parse *sql* once, run all rules, never raise."""
    if not sql or not sql.strip():
        return StaticAnalysisReport()

    try:
        import sqlglot
        import sqlglot.errors as sge
        import sqlglot.expressions as exp
    except ImportError:
        return StaticAnalysisReport(
            parse_error=(
                "sql_static dependency missing: sqlglot is not installed. "
                "Install with: pip install 'sqlglot>=23.0'"
            )
        )

    parse_exc = None
    try:
        statements = sqlglot.parse(sql, read="trino", error_level=sge.ErrorLevel.RAISE)
    except sge.ParseError as exc:
        parse_exc = exc
        try:
            statements = sqlglot.parse(sql, read="trino", error_level=sge.ErrorLevel.IGNORE)
        except Exception:
            statements = []

    parseable = [
        s for s in statements
        if s is not None and isinstance(
            s, (exp.Select, exp.Insert, exp.Update, exp.Delete, exp.Create, exp.Drop, exp.With)
        )
    ]

    if parse_exc is not None and not parseable:
        return StaticAnalysisReport(parse_error=f"SQL parse failed: {parse_exc}")

    findings: list[Finding] = []
    for rule_fn in _load_rules():
        try:
            findings.extend(rule_fn(sql, statements))
        except Exception as exc:
            logger.debug("static rule %s failed: %s", rule_fn.__module__, exc)

    findings.sort(key=lambda f: (f.line, f.rule_id))
    return StaticAnalysisReport(findings=findings)


def summary_line(report: "StaticAnalysisReport | None") -> str:
    """One-line human/log summary of a static analysis report.

    Used to surface the (otherwise silent) static-analysis step in the
    `/trino-research` console output so the user can see the rules ran and what
    they found — and so it doubles as a debug log.
    """
    if report is None:
        return "skipped"
    if getattr(report, "parse_error", None):
        return f"parse error ({report.parse_error})"
    if not report.findings:
        return "no issues found"
    rules = ", ".join(sorted({f.rule_id for f in report.findings}))
    return f"{report.summary} — {rules}"


__all__ = ["Finding", "StaticAnalysisReport", "analyze", "summary_line"]
