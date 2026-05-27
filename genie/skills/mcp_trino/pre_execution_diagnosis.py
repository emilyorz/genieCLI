"""Pure, deterministic query-diagnostics → optimization-directions mapper.

No I/O, no network, no query execution.  Consumes three already-computed
diagnostics and returns a ranked list of OptimizationDirection objects.

Public API
----------
pre_execution_diagnosis(sql, *, static_report, explain_cost,
                        table_metadata, peak_memory_bytes)
    -> list[OptimizationDirection]

All input arguments are optional (keyword-only, default None).  The function
never raises; malformed / missing inputs produce an empty contribution from
that contributor.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

# ---------------------------------------------------------------------------
# Module-level thresholds — single source of truth
# ---------------------------------------------------------------------------

LARGE_SCAN_BYTES: int = 1 * 1024**3       # 1 GiB — bytes_est above which reduce-scan emits
HIGH_PEAK_MEMORY_BYTES: int = 1 * 1024**3  # 1 GiB — peak/build-side above which memory-pressure emits
HIGH_SEVERITY_SCAN_MULTIPLIER: int = 10    # bytes_est above this × LARGE_SCAN_BYTES → reduce-scan is "high"

# ---------------------------------------------------------------------------
# Evidence-prefix → sort rank mapping (lower = higher priority)
# ---------------------------------------------------------------------------
_SOURCE_RANK: dict[str, int] = {
    "static": 0,
    "explain": 1,
    "runtime": 2,
    "metadata": 3,
}

_SEVERITY_RANK: dict[str, int] = {"high": 0, "medium": 1, "low": 2}

# ---------------------------------------------------------------------------
# Output dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class OptimizationDirection:
    """A single ranked optimization recommendation."""

    kind: str           # stable machine id (e.g. "fix-cartesian-join")
    severity: str       # "high" | "medium" | "low"
    rationale: str      # human-readable WHY (fed to an LLM prompt later)
    evidence: str       # provenance string (e.g. "static:cartesian-join@L4")
    target_metric: str  # which metric this direction aims to improve


# ---------------------------------------------------------------------------
# Internal ranking key helper
# ---------------------------------------------------------------------------


def _sort_key(d: OptimizationDirection) -> tuple[int, int, str, str]:
    severity_rank = _SEVERITY_RANK.get(d.severity, 3)
    evidence_prefix = d.evidence.split(":")[0] if ":" in d.evidence else d.evidence
    source_rank = _SOURCE_RANK.get(evidence_prefix, 9)
    # `evidence` as final tie-breaker → total order, independent of input order
    # (two findings with same rule_id at different lines, or two tables both
    #  partitioned, tie on the first three keys but differ in evidence).
    return (severity_rank, source_rank, d.kind, d.evidence)


# ---------------------------------------------------------------------------
# Contributor 1 — static analysis findings
# ---------------------------------------------------------------------------

_RULE_KIND_MAP: dict[str, str] = {
    "cartesian-join": "fix-cartesian-join",
    "select-star": "fix-select-star",
    "distinct-after-group-by": "fix-distinct-after-group-by",
    "order-by-in-subquery": "fix-order-by-in-subquery",
    "subquery-in-select": "fix-subquery-in-select",
    "predicate-pushdown": "fix-predicate-pushdown",
    "null-unsafe-equals": "fix-null-unsafe-equals",
    "redundant-cast": "fix-redundant-cast",
}


def _static_contributor(static_report: Any) -> list[OptimizationDirection]:
    """One direction per finding in static_report.findings."""
    if static_report is None:
        return []
    if getattr(static_report, "parse_error", None):
        return []

    findings = getattr(static_report, "findings", None) or []
    directions: list[OptimizationDirection] = []
    for f in findings:
        rule_id: str = getattr(f, "rule_id", "unknown")
        severity: str = getattr(f, "severity", "low")
        message: str = getattr(f, "message", "")
        suggestion: str = getattr(f, "suggestion", "")
        line: int = getattr(f, "line", 1)

        kind = _RULE_KIND_MAP.get(rule_id, f"static-{rule_id}")
        rationale = f"{message} {suggestion}".strip()
        evidence = f"static:{rule_id}@L{line}"

        directions.append(
            OptimizationDirection(
                kind=kind,
                severity=severity,
                rationale=rationale,
                evidence=evidence,
                target_metric="wall_time_ms",
            )
        )
    return directions


# ---------------------------------------------------------------------------
# Contributor 2 — explain-cost plan
# ---------------------------------------------------------------------------


def _max_non_leaf_output_bytes(node: Any) -> int | None:
    """Recursively find the max outputSizeInBytes on any non-leaf node."""
    if not isinstance(node, dict):
        return None
    children: list[Any] = node.get("children", [])
    if not children:
        return None  # leaf node — skip

    estimates: list[Any] = node.get("estimates", [])
    local_max: int | None = None
    for est in estimates:
        if isinstance(est, dict):
            val = est.get("outputSizeInBytes")
            if isinstance(val, (int, float)) and val > 0:
                as_int = int(val)
                if local_max is None or as_int > local_max:
                    local_max = as_int

    for child in children:
        child_max = _max_non_leaf_output_bytes(child)
        if child_max is not None:
            if local_max is None or child_max > local_max:
                local_max = child_max

    return local_max


def _explain_cost_contributor(explain_cost: Any) -> list[OptimizationDirection]:
    """Emit reduce-scan and/or memory-pressure from plan estimates."""
    if explain_cost is None:
        return []

    try:
        rows_est, bytes_est, raw_plan_json = explain_cost
    except (TypeError, ValueError):
        return []

    directions: list[OptimizationDirection] = []

    # reduce-scan: root-level bytes estimate
    if isinstance(bytes_est, (int, float)) and bytes_est > LARGE_SCAN_BYTES:
        severity = (
            "high"
            if bytes_est > HIGH_SEVERITY_SCAN_MULTIPLIER * LARGE_SCAN_BYTES
            else "medium"
        )
        directions.append(
            OptimizationDirection(
                kind="reduce-scan",
                severity=severity,
                rationale=(
                    "Query scans a large volume of data. Consider adding partition "
                    "filters, predicate pushdown, or using a smaller projection."
                ),
                evidence=f"explain:outputSizeInBytes={int(bytes_est)}",
                target_metric="physical_input_bytes",
            )
        )

    # memory-pressure: max non-leaf build-side from plan tree
    if isinstance(raw_plan_json, dict):
        try:
            max_build_bytes = _max_non_leaf_output_bytes(raw_plan_json)
            if max_build_bytes is not None and max_build_bytes > HIGH_PEAK_MEMORY_BYTES:
                directions.append(
                    OptimizationDirection(
                        kind="memory-pressure",
                        severity="high",
                        rationale=(
                            "An intermediate operator builds a large in-memory structure. "
                            "Consider broadcasting smaller tables, adding filters, or "
                            "rewriting joins to reduce build-side size."
                        ),
                        evidence=f"explain:build-side outputSizeInBytes={max_build_bytes}",
                        target_metric="peak_memory_bytes",
                    )
                )
        except Exception:
            pass  # never raise from malformed plan

    return directions


# ---------------------------------------------------------------------------
# Contributor 3 — table metadata
# ---------------------------------------------------------------------------


_EMPTY_PARTITION_VALUES: frozenset[str] = frozenset({"", "[]", "null", "none"})


def _partition_spec(properties: dict[str, str]) -> str:
    """Return the partition spec if the table is actually partitioned, else ''.

    Mirrors production (research.py:231-232): Iceberg exposes the spec under
    ``partitioning``; Hive under ``partitioned_by``. An empty value or the
    literal ``"[]"`` means NOT partitioned and must not emit a direction.
    """
    raw = properties.get("partitioning") or properties.get("partitioned_by") or ""
    if not isinstance(raw, str):
        raw = str(raw)
    if raw.strip().lower() in _EMPTY_PARTITION_VALUES:
        return ""
    return raw


def _metadata_contributor(table_metadata: Any) -> list[OptimizationDirection]:
    """Emit leverage-sort / leverage-partitioning from table properties."""
    if not table_metadata:
        return []

    directions: list[OptimizationDirection] = []
    for tm in table_metadata:
        table_name: str = getattr(tm, "table_name", "unknown")
        properties: dict[str, str] = getattr(tm, "properties", {}) or {}

        # leverage-sort
        sorted_by = properties.get("sorted_by") or properties.get("sort_order")
        if sorted_by:
            directions.append(
                OptimizationDirection(
                    kind="leverage-sort",
                    severity="low",
                    rationale=(
                        f"Table {table_name!r} is sorted; queries with matching "
                        "ORDER BY or range predicates may benefit from sort-aware reads."
                    ),
                    evidence=f"metadata:{table_name} sorted_by={sorted_by}",
                    target_metric="physical_input_bytes",
                )
            )

        # leverage-partitioning — mirror production semantics (research.py:231-232):
        # Iceberg/Hive expose the partition spec under the "partitioning" property;
        # an empty value or the literal "[]" means the table is NOT partitioned.
        partition_val = _partition_spec(properties)
        if partition_val:
            directions.append(
                OptimizationDirection(
                    kind="leverage-partitioning",
                    severity="low",
                    rationale=(
                        f"Table {table_name!r} is partitioned; add partition-column "
                        "predicates to prune unnecessary files."
                    ),
                    evidence=f"metadata:{table_name} partitioning={partition_val}",
                    target_metric="physical_input_bytes",
                )
            )

    return directions


# ---------------------------------------------------------------------------
# Contributor 4 — runtime peak memory
# ---------------------------------------------------------------------------


def _memory_contributor(peak_memory_bytes: Any) -> list[OptimizationDirection]:
    """Emit memory-pressure from actual runtime peak memory."""
    if peak_memory_bytes is None:
        return []
    if not isinstance(peak_memory_bytes, (int, float)):
        return []
    if peak_memory_bytes > HIGH_PEAK_MEMORY_BYTES:
        return [
            OptimizationDirection(
                kind="memory-pressure",
                severity="high",
                rationale=(
                    "The query exceeded the peak-memory threshold at runtime. "
                    "Consider rewriting large joins, adding spill hints, or "
                    "filtering earlier to reduce in-memory state."
                ),
                evidence=f"runtime:peak_memory_bytes={int(peak_memory_bytes)}",
                target_metric="peak_memory_bytes",
            )
        ]
    return []


# ---------------------------------------------------------------------------
# Prompt formatting — turn ranked directions into an LLM-ready block
# ---------------------------------------------------------------------------


def format_directions_for_prompt(
    directions: list[OptimizationDirection],
    *,
    limit: int = 6,
) -> str:
    """Render ranked directions as a numbered prompt block, or "" if none.

    Pure and deterministic: the input list is already total-ordered by
    ``pre_execution_diagnosis``, so the output depends only on the input.
    Returns an empty string when there are no directions, so callers can
    cheaply gate prompt injection on truthiness.
    """
    if not directions:
        return ""

    lines = [
        "Pre-execution diagnosis (ranked optimization directions — "
        "apply highest-severity first):",
    ]
    for i, d in enumerate(directions[:limit], 1):
        lines.append(
            f"{i}. [{d.severity}] {d.kind} (target: {d.target_metric}) — "
            f"{d.rationale} [{d.evidence}]"
        )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Report rendering — standalone Markdown directed report (zero query cost)
# ---------------------------------------------------------------------------


def format_directions_report(
    directions: list[OptimizationDirection],
    *,
    sql: str,
    reason: str,
    model: str = "",
) -> str:
    """Render ranked directions as a standalone Markdown report.

    Used by the long-query / `--diagnose-only` path: when the iteration loop is
    skipped (no real query, no EXPLAIN ANALYZE), the diagnosis is still emitted
    as a directed report so the user gets actionable directions at zero query
    cost instead of a bare abort. Pure and deterministic; never raises.
    """
    from datetime import datetime

    lines = [
        "# Trino Query Pre-execution Diagnosis Report (zero-cost directed)",
        "",
        f"**Date:** {datetime.now().strftime('%Y-%m-%d %H:%M')}",
    ]
    if model:
        lines.append(f"**Model:** {model}")
    lines += [
        f"**Mode:** diagnosis-only — iteration loop skipped: {reason}",
        "",
        "## Why this report instead of an iteration run",
        "",
        f"- {reason}",
        "- The optimizer would burn one real query per iteration; for a slow "
        "baseline that is prohibitively expensive.",
        "- Static analysis + EXPLAIN (FORMAT JSON) plan cost + table metadata "
        "still surface ranked optimization directions at **zero query cost**.",
        "",
        "## Original SQL",
        "",
        "```sql",
        sql.rstrip(),
        "```",
        "",
        "## Ranked optimization directions",
        "",
    ]

    if not directions:
        lines += [
            "_No directions surfaced. The static analyzer found no structural "
            "issues, EXPLAIN cost was unavailable or below threshold, and no "
            "partition/sort metadata was present._",
            "",
            "Next step: confirm an EXPLAIN runner is reachable, or pass "
            "`--long-query` to run the full iteration loop anyway.",
            "",
        ]
        return "\n".join(lines)

    lines += ["| # | Severity | Kind | Target metric | Rationale | Evidence |",
              "|---|---|---|---|---|---|"]
    for i, d in enumerate(directions, 1):
        rationale = d.rationale.replace("|", "\\|")
        lines.append(
            f"| {i} | {d.severity} | {d.kind} | {d.target_metric} | "
            f"{rationale} | {d.evidence} |"
        )
    lines += [
        "",
        "## Next steps",
        "",
        "1. Apply the highest-severity direction first (the table above is ranked by severity).",
        "2. Re-run `/trino-research` on the rewritten query; if the baseline is "
        "now under the long-query threshold the full iteration loop runs.",
        "3. To force the iteration loop on the original slow query, pass "
        "`--long-query`.",
        "",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def pre_execution_diagnosis(
    sql: str,
    *,
    static_report: Any = None,
    explain_cost: Any = None,
    table_metadata: Any = None,
    peak_memory_bytes: int | None = None,
) -> list[OptimizationDirection]:
    """Combine diagnostics into a ranked list of optimization directions.

    Parameters
    ----------
    sql:
        The SQL being diagnosed (currently unused in ranking but kept for
        future rule extensions that may need the raw text).
    static_report:
        A StaticAnalysisReport (duck-typed) or None.
    explain_cost:
        ``(rows_est, bytes_est, raw_plan_json)`` tuple or None.
        ``rows_est`` and ``bytes_est`` may each be None.
    table_metadata:
        List of TableMetadata (duck-typed) or None.
    peak_memory_bytes:
        Actual peak memory from a completed query run, or None.

    Returns
    -------
    list[OptimizationDirection]
        Deterministically sorted; never raises.
    """
    directions: list[OptimizationDirection] = []
    directions.extend(_static_contributor(static_report))
    directions.extend(_explain_cost_contributor(explain_cost))
    directions.extend(_metadata_contributor(table_metadata))
    directions.extend(_memory_contributor(peak_memory_bytes))

    # Stable deterministic sort: (severity_rank, source_rank, kind)
    directions.sort(key=_sort_key)
    return directions


__all__ = [
    "OptimizationDirection",
    "pre_execution_diagnosis",
    "format_directions_for_prompt",
    "format_directions_report",
    "LARGE_SCAN_BYTES",
    "HIGH_PEAK_MEMORY_BYTES",
]
