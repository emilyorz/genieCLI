"""Rule-first gate for /trino-research.

This module deliberately does not rewrite SQL.  It classifies deterministic
signals into a small action taxonomy that can be rendered for humans and fed to
the optimizer prompt before the AI proposes a candidate.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from rich.markup import escape

from genie.skills.trino_query.sql_static.rule_ids import (
    RULE_CARTESIAN_JOIN,
    RULE_JOIN_FIRST_FILTER_LATE,
    RULE_NULL_UNSAFE_EQUALS,
    RULE_PREDICATE_NOT_PUSHED_TO_CTE,
    RULE_REDUNDANT_CAST_CHAIN,
    RULE_REDUNDANT_DISTINCT_AFTER_GROUP_BY,
    RULE_SELECT_STAR,
    RULE_SUBQUERY_IN_SELECT_PUSHABLE_TO_JOIN,
    RULE_UNNECESSARY_ORDER_BY_IN_SUBQUERY,
)

logger = logging.getLogger(__name__)


ACTION_BLOCK = "block"
ACTION_REWRITE = "rewrite"
ACTION_ADVISE = "advise"
ACTION_PASS = "pass"

_ACTION_RANK = {
    ACTION_BLOCK: 0,
    ACTION_REWRITE: 1,
    ACTION_ADVISE: 2,
    ACTION_PASS: 3,
}

_SEVERITY_RANK = {"high": 0, "medium": 1, "low": 2}


@dataclass(frozen=True)
class RuleGateItem:
    action: str
    severity: str
    source: str
    rule_id: str
    message: str
    suggestion: str
    evidence: str
    confidence: str = "medium"


@dataclass(frozen=True)
class RuleGateSummary:
    items: tuple[RuleGateItem, ...]

    @property
    def counts(self) -> dict[str, int]:
        counts = {
            ACTION_BLOCK: 0,
            ACTION_REWRITE: 0,
            ACTION_ADVISE: 0,
            ACTION_PASS: 0,
        }
        for item in self.items:
            counts[item.action] = counts.get(item.action, 0) + 1
        if not self.items:
            counts[ACTION_PASS] = 1
        return counts

    @property
    def has_findings(self) -> bool:
        return bool(self.items)

    @property
    def should_auto_iterate(self) -> bool:
        # v31 keeps BLOCK non-fatal: it blocks semantic auto-repair, not the
        # research run. Future versions can make this stricter behind a flag.
        return True


_STATIC_ACTIONS: dict[str, tuple[str, str, str]] = {
    RULE_CARTESIAN_JOIN: (
        ACTION_BLOCK,
        "high",
        "Do not invent missing join predicates automatically; inspect join intent.",
    ),
    RULE_NULL_UNSAFE_EQUALS: (
        ACTION_BLOCK,
        "high",
        "Correct NULL semantics before tuning; do not silently rewrite results.",
    ),
    RULE_REDUNDANT_DISTINCT_AFTER_GROUP_BY: (
        ACTION_REWRITE,
        "high",
        "Candidate rewrite: remove redundant DISTINCT, then verify equivalence.",
    ),
    RULE_UNNECESSARY_ORDER_BY_IN_SUBQUERY: (
        ACTION_REWRITE,
        "medium",
        "Candidate rewrite: remove subquery ORDER BY when no LIMIT/top-N depends on it.",
    ),
    RULE_PREDICATE_NOT_PUSHED_TO_CTE: (
        ACTION_REWRITE,
        "medium",
        "Candidate rewrite: push predicate into the CTE/subquery and verify.",
    ),
    RULE_REDUNDANT_CAST_CHAIN: (
        ACTION_REWRITE,
        "high",
        "Candidate rewrite: remove redundant cast chain and verify.",
    ),
    RULE_JOIN_FIRST_FILTER_LATE: (
        ACTION_REWRITE,
        "medium",
        "Candidate rewrite: pre-filter the relevant input before the large join when semantics permit; verify equivalence.",
    ),
    RULE_SELECT_STAR: (
        ACTION_ADVISE,
        "medium",
        "Prefer an explicit projection only when the output column contract is preserved.",
    ),
    RULE_SUBQUERY_IN_SELECT_PUSHABLE_TO_JOIN: (
        ACTION_ADVISE,
        "medium",
        "Consider join/pre-aggregation rewrite, but preserve scalar-subquery semantics.",
    ),
}

_DIRECTION_ACTIONS: dict[str, tuple[str, str, str]] = {
    "materialize-cte-steps": (
        ACTION_ADVISE,
        "medium",
        "Step materialization is advisory only; do not emit CTAS/DDL in normal mode.",
    ),
    "reduce-raw-rescan": (
        ACTION_ADVISE,
        "high",
        "Prioritize reducing repeated raw/source scans over small curated-table rereads.",
    ),
    "reduce-scan": (
        ACTION_ADVISE,
        "medium",
        "Reduce scan volume with partition filters, predicate pushdown, or projection pruning.",
    ),
    "memory-pressure": (
        ACTION_ADVISE,
        "medium",
        "Investigate build-side size, skew, spill, and high-cardinality operators.",
    ),
    "leverage-partitioning": (
        ACTION_ADVISE,
        "medium",
        "Use direct partition predicates when they preserve result semantics.",
    ),
    "leverage-sort": (
        ACTION_ADVISE,
        "low",
        "Leverage sort/order only when the query naturally filters or orders on that key.",
    ),
}


def _sort_key(item: RuleGateItem) -> tuple[int, int, str, str, str]:
    return (
        _ACTION_RANK.get(item.action, 9),
        _SEVERITY_RANK.get(item.severity, 9),
        item.source,
        item.rule_id,
        item.evidence,
    )


def _static_items(static_report: Any) -> list[RuleGateItem]:
    if static_report is None or getattr(static_report, "parse_error", None):
        return []

    items: list[RuleGateItem] = []
    for finding in getattr(static_report, "findings", None) or []:
        rule_id = str(getattr(finding, "rule_id", "unknown"))
        mapping = _STATIC_ACTIONS.get(rule_id)
        if mapping is None:
            # Loud-on-unknown: a static rule_id with no gate action almost always
            # means a producer/consumer id drift (the v32 bug). Warn so it is
            # caught, but fail open to a safe advisory rather than dropping it.
            logger.warning(
                "rule_gate: static rule_id %r has no _STATIC_ACTIONS entry; "
                "defaulting to advise (possible rule_id drift)",
                rule_id,
            )
            mapping = (ACTION_ADVISE, "low", "Review this finding before changing SQL.")
        action, confidence, gate_suggestion = mapping
        severity = str(getattr(finding, "severity", "low"))
        line = int(getattr(finding, "line", 1) or 1)
        message = str(getattr(finding, "message", "") or rule_id)
        finding_suggestion = str(getattr(finding, "suggestion", "") or "")
        suggestion = gate_suggestion
        if finding_suggestion:
            suggestion = f"{gate_suggestion} Original suggestion: {finding_suggestion}"
        items.append(
            RuleGateItem(
                action=action,
                severity=severity,
                source="static",
                rule_id=rule_id,
                message=message,
                suggestion=suggestion,
                evidence=f"static:{rule_id}@L{line}",
                confidence=confidence,
            )
        )
    return items


def _direction_items(directions: list[Any] | None) -> list[RuleGateItem]:
    items: list[RuleGateItem] = []
    for direction in directions or []:
        evidence = str(getattr(direction, "evidence", "") or "")
        if evidence.startswith("static:"):
            continue
        kind = str(getattr(direction, "kind", "unknown"))
        action, confidence, gate_suggestion = _DIRECTION_ACTIONS.get(
            kind,
            (ACTION_ADVISE, "low", "Use as advisory context only."),
        )
        rationale = str(getattr(direction, "rationale", "") or kind)
        items.append(
            RuleGateItem(
                action=action,
                severity=str(getattr(direction, "severity", "low")),
                source="diagnosis",
                rule_id=kind,
                message=rationale,
                suggestion=gate_suggestion,
                evidence=evidence or f"diagnosis:{kind}",
                confidence=confidence,
            )
        )
    return items


def build_rule_gate_summary(static_report: Any = None, directions: list[Any] | None = None) -> RuleGateSummary:
    """Build a deterministic rule-gate summary from existing diagnostics."""
    try:
        items = _static_items(static_report) + _direction_items(directions)
    except Exception:
        # The rule gate is a pre-AI filter, not a correctness oracle. If an
        # upstream diagnostic object is malformed, fail open and keep the
        # research run alive without adding rule-gate context.
        items = []
    return RuleGateSummary(tuple(sorted(items, key=_sort_key)))


def format_rule_gate_for_prompt(summary: RuleGateSummary, *, limit: int = 8) -> str:
    """Render a compact prompt block. Empty summaries return an empty string."""
    if not summary.has_findings:
        return ""
    counts = summary.counts
    lines = [
        "Rule-based gate (pre-AI; result equivalence remains authoritative):",
        (
            f"Counts: block={counts[ACTION_BLOCK]}, "
            f"rewrite={counts[ACTION_REWRITE]}, advise={counts[ACTION_ADVISE]}"
        ),
        "Semantics:",
        "- block: do not semantic-repair automatically; surface the issue or preserve results.",
        "- rewrite: safe rewrite candidate class, but v31 does not auto-mutate SQL.",
        "- advise: context for AI; do not overfit if evidence is weak.",
        "- side effects: do not emit CTAS/materialized-view DDL in the normal read-only loop.",
        "Top findings:",
    ]
    for item in summary.items[:limit]:
        lines.append(
            f"- [{item.action}/{item.severity}/{item.confidence}] "
            f"{item.rule_id}: {item.suggestion} ({item.evidence})"
        )
    return "\n".join(lines)


def render_rule_gate_summary(output: Any, summary: RuleGateSummary, *, limit: int = 6) -> None:
    """Render a compact HumanSink-compatible block."""
    if output is None or not summary.has_findings:
        return

    counts = summary.counts
    output.print(
        "  [bold cyan]rule gate[/bold cyan]  "
        f"[dim]block[/dim]={counts[ACTION_BLOCK]}  "
        f"[dim]rewrite[/dim]={counts[ACTION_REWRITE]}  "
        f"[dim]advise[/dim]={counts[ACTION_ADVISE]}"
    )
    styles = {
        ACTION_BLOCK: "red",
        ACTION_REWRITE: "yellow",
        ACTION_ADVISE: "cyan",
    }
    for item in summary.items[:limit]:
        style = styles.get(item.action, "dim")
        rule = escape(item.rule_id[:26])
        suggestion = escape(item.suggestion)
        output.print(
            f"    [{style}]{item.action:<7}[/{style}] "
            f"[dim]{rule:<26}[/dim] {suggestion}"
        )


__all__ = [
    "ACTION_ADVISE",
    "ACTION_BLOCK",
    "ACTION_PASS",
    "ACTION_REWRITE",
    "RuleGateItem",
    "RuleGateSummary",
    "build_rule_gate_summary",
    "format_rule_gate_for_prompt",
    "render_rule_gate_summary",
]
