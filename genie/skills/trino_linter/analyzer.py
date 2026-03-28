"""SQL parse + rule dispatch + result assembly for the Trino linter."""
from __future__ import annotations

from dataclasses import dataclass, field

from .rules import ALL_RULES, Finding


@dataclass
class LintResult:
    findings: list[Finding]
    score: str          # A / B / C / D / F
    summary: str        # "N high, N medium, N low"
    parse_error: str | None = None

    def to_dict(self) -> dict:
        return {
            "findings": [
                {
                    "severity": f.severity,
                    "line": f.line,
                    "rule": f.rule,
                    "message": f.message,
                    "suggestion": f.suggestion,
                }
                for f in self.findings
            ],
            "score": self.score,
            "summary": self.summary,
            "parse_error": self.parse_error,
        }


def _compute_score(findings: list[Finding], parse_error: str | None) -> str:
    if parse_error:
        return "F"
    highs = sum(1 for f in findings if f.severity == "high")
    mediums = sum(1 for f in findings if f.severity == "medium")
    if highs >= 3:
        return "F"
    if highs >= 1:
        return "D"
    if mediums >= 3:
        return "C"
    if mediums >= 1:
        return "B"
    return "A"


def _make_summary(findings: list[Finding]) -> str:
    highs = sum(1 for f in findings if f.severity == "high")
    mediums = sum(1 for f in findings if f.severity == "medium")
    lows = sum(1 for f in findings if f.severity == "low")
    return f"{highs} high, {mediums} medium, {lows} low"


def analyze(sql: str) -> LintResult:
    """Parse *sql* and run all lint rules. Never raises — returns structured result."""
    if not sql or not sql.strip():
        return LintResult(findings=[], score="A", summary="0 high, 0 medium, 0 low")

    try:
        import sqlglot
        import sqlglot.errors as sge

        # Try strict parse first — catches truly invalid SQL
        parse_exc = None
        try:
            statements = sqlglot.parse(sql, read="trino", error_level=sge.ErrorLevel.RAISE)
        except sge.ParseError as exc:
            parse_exc = exc
            # Fall back to lenient parse (handles valid-but-unsupported-dialect syntax)
            try:
                statements = sqlglot.parse(sql, read="trino", error_level=sge.ErrorLevel.IGNORE)
            except Exception:
                statements = []

        # If strict parse failed and lenient parse yielded no meaningful query statements,
        # declare failure (e.g. completely invalid input like "THIS IS NOT SQL @@@")
        if parse_exc is not None and not any(
            s is not None and isinstance(s, (
                sqlglot.exp.Select, sqlglot.exp.Insert, sqlglot.exp.Update,
                sqlglot.exp.Delete, sqlglot.exp.Create, sqlglot.exp.Drop,
            ))
            for s in statements
        ):
            return LintResult(
                findings=[],
                score="F",
                summary="0 high, 0 medium, 0 low",
                parse_error=f"SQL parse failed: {parse_exc}",
            )
    except Exception as exc:
        return LintResult(
            findings=[],
            score="F",
            summary="0 high, 0 medium, 0 low",
            parse_error=f"SQL parse failed: {exc}",
        )

    all_findings: list[Finding] = []
    for rule_fn in ALL_RULES:
        try:
            all_findings.extend(rule_fn(sql, statements))
        except Exception:
            # Rule failures must never crash the linter
            pass

    all_findings.sort(key=lambda f: f.line)

    score = _compute_score(all_findings, None)
    summary = _make_summary(all_findings)

    return LintResult(findings=all_findings, score=score, summary=summary)
