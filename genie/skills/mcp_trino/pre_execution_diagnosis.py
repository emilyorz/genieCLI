"""Pure, deterministic query-diagnostics → optimization-directions mapper.

No I/O, no network, no query execution.  Consumes three already-computed
diagnostics and returns a ranked list of OptimizationDirection objects.

Public API
----------
pre_execution_diagnosis(sql, *, static_report, explain_cost,
                        table_metadata, peak_memory_bytes,
                        peak_memory_limit_bytes)
    -> list[OptimizationDirection]

All input arguments are optional (keyword-only, default None).  The function
never raises; malformed / missing inputs produce an empty contribution from
that contributor.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from genie.skills.trino_query.sql_static.rule_ids import (
    RULE_CARTESIAN_JOIN,
    RULE_NULL_UNSAFE_EQUALS,
    RULE_PREDICATE_NOT_PUSHED_TO_CTE,
    RULE_REDUNDANT_CAST_CHAIN,
    RULE_REDUNDANT_DISTINCT_AFTER_GROUP_BY,
    RULE_SELECT_STAR,
    RULE_SUBQUERY_IN_SELECT_PUSHABLE_TO_JOIN,
    RULE_UNNECESSARY_ORDER_BY_IN_SUBQUERY,
)

# ---------------------------------------------------------------------------
# Module-level thresholds — single source of truth
# ---------------------------------------------------------------------------

LARGE_SCAN_BYTES: int = 1 * 1024**3       # 1 GiB — bytes_est above which reduce-scan emits
HIGH_PEAK_MEMORY_BYTES: int = 1 * 1024**3  # 1 GiB fallback when no cluster/session memory limit is supplied
MEMORY_PRESSURE_FRACTION: float = 0.5      # warn once peak/build-side exceeds this fraction of the per-node limit
HIGH_SEVERITY_SCAN_MULTIPLIER: int = 10    # bytes_est above this × LARGE_SCAN_BYTES → reduce-scan is "high"
COMPLEX_CTE_COUNT: int = 3                 # chained WITH steps above this are worth diagnosis
HEAVY_CTE_COUNT: int = 2                   # JOIN/GROUP/window/set-heavy CTE steps above this trigger materialization advice
REPEATED_RAW_SCAN_COUNT: int = 2            # same likely-raw table seen at least this many times


def _memory_pressure_threshold(peak_memory_limit_bytes: Any = None) -> int:
    """Return the memory-pressure threshold.

    If callers know the effective Trino ``query.max-memory-per-node`` value,
    derive the warning threshold as a documented fraction of that per-worker
    limit. Otherwise keep the historical 1 GiB fallback.

    Trino reference:
    https://trino.io/docs/current/admin/properties-resource-management.html#query-max-memory-per-node
    """
    if isinstance(peak_memory_limit_bytes, bool):
        return HIGH_PEAK_MEMORY_BYTES
    if isinstance(peak_memory_limit_bytes, (int, float)) and peak_memory_limit_bytes > 0:
        return max(1, int(peak_memory_limit_bytes * MEMORY_PRESSURE_FRACTION))
    return HIGH_PEAK_MEMORY_BYTES

# ---------------------------------------------------------------------------
# Evidence-prefix → sort rank mapping (lower = higher priority)
# ---------------------------------------------------------------------------
_SOURCE_RANK: dict[str, int] = {
    "static": 0,
    "sql-shape": 1,
    "explain": 2,
    "runtime": 3,
    "metadata": 4,
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
    RULE_CARTESIAN_JOIN: "fix-cartesian-join",
    RULE_SELECT_STAR: "fix-select-star",
    RULE_REDUNDANT_DISTINCT_AFTER_GROUP_BY: "fix-distinct-after-group-by",
    RULE_UNNECESSARY_ORDER_BY_IN_SUBQUERY: "fix-order-by-in-subquery",
    RULE_SUBQUERY_IN_SELECT_PUSHABLE_TO_JOIN: "fix-subquery-in-select",
    RULE_PREDICATE_NOT_PUSHED_TO_CTE: "fix-predicate-pushdown",
    RULE_NULL_UNSAFE_EQUALS: "fix-null-unsafe-equals",
    RULE_REDUNDANT_CAST_CHAIN: "fix-redundant-cast",
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
# Contributor 1b — SQL shape heuristics
# ---------------------------------------------------------------------------

_RAW_TABLE_TOKENS: frozenset[str] = frozenset({
    "raw",
    "ods",
    "source",
    "stg",
    "stage",
    "staging",
    "fact",
    "events",
    "event",
    "logs",
    "log",
})

_CURATED_TABLE_TOKENS: frozenset[str] = frozenset({
    "curated",
    "presum",
    "summary",
    "summarized",
    "aggregate",
    "agg",
    "dim",
    "dimension",
})


def _name_tokens(name: str) -> set[str]:
    return {tok for tok in re.split(r"[^a-z0-9]+", name.lower()) if tok}


def _looks_like_raw_table(table_name: str) -> bool:
    """Best-effort naming heuristic; metadata and EXPLAIN remain authoritative."""
    tokens = _name_tokens(table_name)
    if tokens & _CURATED_TABLE_TOKENS:
        return False
    return bool(tokens & _RAW_TABLE_TOKENS)


def _table_name(table: Any) -> str:
    parts = [
        getattr(table, "catalog", "") or "",
        getattr(table, "db", "") or "",
        getattr(table, "name", "") or "",
    ]
    return ".".join(str(part) for part in parts if part)


def _sql_shape_contributor(sql: str) -> list[OptimizationDirection]:
    """Emit advisory directions from SQL shape without executing the query.

    These are intentionally conservative. They do not rewrite SQL and they do
    not authorize side-effecting CTAS; they only give the optimizer a sharper
    diagnosis block when the query shape resembles real Trino pain points.
    """
    if not isinstance(sql, str) or not sql.strip():
        return []

    try:
        import sqlglot
        from sqlglot import exp

        tree = sqlglot.parse_one(sql, read="trino")
    except Exception:
        return []

    directions: list[OptimizationDirection] = []

    ctes = list(tree.find_all(exp.CTE))
    heavy_ctes = [
        cte for cte in ctes
        if (
            cte.find(exp.Join)
            or cte.find(exp.Group)
            or cte.find(exp.Window)
            or cte.find(exp.Union)
        )
    ]
    if len(ctes) >= COMPLEX_CTE_COUNT and len(heavy_ctes) >= HEAVY_CTE_COUNT:
        directions.append(
            OptimizationDirection(
                kind="materialize-cte-steps",
                severity="high",
                rationale=(
                    "The SQL has multiple heavy WITH steps. Trino inlines WITH "
                    "relations, so a deep CTE chain can expand into an expensive "
                    "single plan. Recommend step materialization via managed CTAS "
                    "or materialized views only as an advisory or explicit "
                    "multi-statement mode; do not auto-convert a read-only SELECT "
                    "into side-effecting DDL."
                ),
                evidence=(
                    f"sql-shape:cte_count={len(ctes)},"
                    f"heavy_ctes={len(heavy_ctes)}"
                ),
                target_metric="query_time_ms",
            )
        )

    cte_aliases = {str(cte.alias).lower() for cte in ctes if cte.alias}
    table_counts: dict[str, int] = {}
    for table in tree.find_all(exp.Table):
        name = _table_name(table)
        if not name or name.lower() in cte_aliases:
            continue
        key = name.lower()
        if _looks_like_raw_table(key):
            table_counts[key] = table_counts.get(key, 0) + 1

    repeated_raw = sorted(
        (name, count)
        for name, count in table_counts.items()
        if count >= REPEATED_RAW_SCAN_COUNT
    )
    if repeated_raw:
        name, count = repeated_raw[0]
        directions.append(
            OptimizationDirection(
                kind="reduce-raw-rescan",
                severity="medium",
                rationale=(
                    "The same likely raw/source table is referenced multiple "
                    "times. Prioritize avoiding repeated raw scans; repeated "
                    "reads of small curated/presum/dimension tables are usually "
                    "a lower-priority cost."
                ),
                evidence=f"sql-shape:repeated_raw_scan={name}x{count}",
                target_metric="physical_input_bytes",
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


def _explain_cost_contributor(
    explain_cost: Any,
    *,
    peak_memory_limit_bytes: Any = None,
) -> list[OptimizationDirection]:
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
    memory_threshold = _memory_pressure_threshold(peak_memory_limit_bytes)
    if isinstance(raw_plan_json, dict):
        try:
            max_build_bytes = _max_non_leaf_output_bytes(raw_plan_json)
            if max_build_bytes is not None and max_build_bytes > memory_threshold:
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


def _memory_contributor(
    peak_memory_bytes: Any,
    *,
    peak_memory_limit_bytes: Any = None,
) -> list[OptimizationDirection]:
    """Emit memory-pressure from actual runtime peak memory."""
    if peak_memory_bytes is None:
        return []
    if not isinstance(peak_memory_bytes, (int, float)):
        return []
    memory_threshold = _memory_pressure_threshold(peak_memory_limit_bytes)
    if peak_memory_bytes > memory_threshold:
        return [
            OptimizationDirection(
                kind="memory-pressure",
                severity="high",
                rationale=(
                    "The query exceeded the peak-memory threshold at runtime. "
                    "Inspect EXPLAIN ANALYZE for skew, spill, and large build-side "
                    "state; reduce memory with narrower projections, earlier "
                    "filters, smaller build sides, or partitioned joins when the "
                    "build side does not fit per worker."
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
# Report rendering — standalone Markdown directed report
# ---------------------------------------------------------------------------


def format_directions_report(
    directions: list[OptimizationDirection],
    *,
    sql: str,
    reason: str,
    model: str = "",
    baseline_already_ran: bool = False,
) -> str:
    """Render ranked directions as a standalone Markdown report.

    Used by the long-query / `--diagnose-only` path: when the iteration loop is
    skipped, the diagnosis is still emitted as a directed report so the user
    gets actionable directions instead of a bare abort. In `--diagnose-only`
    mode no baseline query ran; in long-query gate-trip mode the baseline
    already ran and this report avoids additional candidate executions and
    EXPLAIN ANALYZE. Pure and deterministic; never raises.
    """
    from datetime import datetime
    if baseline_already_ran:
        title = "# Trino Query Pre-execution Diagnosis Report (iteration skipped)"
        mode = "long-query gate — baseline measured, iteration loop skipped"
        cost_note = (
            "The baseline has already been measured. This report avoids "
            "additional candidate executions and EXPLAIN ANALYZE."
        )
        signal_note = (
            "Static analysis + EXPLAIN (FORMAT JSON) plan cost + table metadata "
            "still surface ranked optimization directions without further query executions."
        )
    else:
        title = "# Trino Query Pre-execution Diagnosis Report (zero-cost directed)"
        mode = "diagnosis-only — no baseline query, no iteration loop"
        cost_note = "No baseline query or candidate execution was run."
        signal_note = (
            "Static analysis + EXPLAIN (FORMAT JSON) plan cost + table metadata "
            "still surface ranked optimization directions at **zero query cost**."
        )

    lines = [
        title,
        "",
        f"**Date:** {datetime.now().strftime('%Y-%m-%d %H:%M')}",
    ]
    if model:
        lines.append(f"**Model:** {model}")
    lines += [
        f"**Mode:** {mode}: {reason}",
        "",
        "## Why this report instead of an iteration run",
        "",
        f"- {reason}",
        "- The optimizer would burn one real query per iteration; for a slow "
        "baseline that is prohibitively expensive.",
        f"- {cost_note}",
        f"- {signal_note}",
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
# Observational efficacy / attribution (v32 T2)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DirectionOutcome:
    """Observational record of whether a direction's target metric moved.

    Attribution is observational, not causal: we only report whether the metric
    the direction *targets* improved between baseline and best. When two or more
    directions target the same metric, none can claim sole credit — they are
    flagged ``co_attributed``.
    """

    kind: str
    target_metric: str
    baseline_value: float | None
    best_value: float | None
    delta: float | None          # best - baseline (negative == improved, lower is better)
    observed_moved: bool
    co_attributed: bool


def attribute_directions(
    directions: list[OptimizationDirection],
    baseline_metrics: dict[str, float] | None,
    best_metrics: dict[str, float] | None,
    *,
    epsilon: float = 0.0,
) -> list[DirectionOutcome]:
    """Map each direction to whether its target metric improved (observational).

    ``baseline_metrics`` / ``best_metrics`` are ``{metric_name: value}`` maps.
    A direction is ``observed_moved`` when its target metric dropped by more than
    ``epsilon`` (lower is better). Directions sharing a target metric are
    ``co_attributed`` so the report never assigns per-direction causal credit.
    Never raises; missing metrics yield ``delta=None`` and ``observed_moved=False``.
    """
    if not directions:
        return []
    baseline_metrics = baseline_metrics or {}
    best_metrics = best_metrics or {}
    counts: dict[str, int] = {}
    for d in directions:
        counts[d.target_metric] = counts.get(d.target_metric, 0) + 1

    outcomes: list[DirectionOutcome] = []
    for d in directions:
        tm = d.target_metric
        base = baseline_metrics.get(tm)
        best = best_metrics.get(tm)
        delta: float | None = None
        moved = False
        if isinstance(base, (int, float)) and isinstance(best, (int, float)):
            delta = float(best) - float(base)
            moved = delta < -epsilon
        outcomes.append(
            DirectionOutcome(
                kind=d.kind,
                target_metric=tm,
                baseline_value=float(base) if isinstance(base, (int, float)) else None,
                best_value=float(best) if isinstance(best, (int, float)) else None,
                delta=delta,
                observed_moved=moved,
                co_attributed=counts[tm] > 1,
            )
        )
    return outcomes


def format_attribution_report(outcomes: list[DirectionOutcome]) -> str:
    """Render direction outcomes as a compact block, or "" when empty."""
    if not outcomes:
        return ""

    def _fmt(v: float | None) -> str:
        return "n/a" if v is None else f"{v:.0f}"

    lines = [
        "Direction efficacy (observational — did the targeted metric move?):",
        "| Direction | Target metric | Baseline | Best | Delta | Moved | Attribution |",
        "|---|---|---|---|---|---|---|",
    ]
    for o in outcomes:
        delta = "n/a" if o.delta is None else f"{o.delta:+.0f}"
        moved = "yes" if o.observed_moved else "no"
        attribution = "co-attributed" if o.co_attributed else "sole"
        lines.append(
            f"| {o.kind} | {o.target_metric} | {_fmt(o.baseline_value)} | "
            f"{_fmt(o.best_value)} | {delta} | {moved} | {attribution} |"
        )
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
    peak_memory_limit_bytes: int | None = None,
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
    peak_memory_limit_bytes:
        Effective Trino ``query.max-memory-per-node`` in bytes, or None. When
        supplied, memory-pressure uses ``MEMORY_PRESSURE_FRACTION`` of this
        value; when None, the historical 1 GiB fallback is preserved.

    Returns
    -------
    list[OptimizationDirection]
        Deterministically sorted; never raises.
    """
    directions: list[OptimizationDirection] = []
    directions.extend(_static_contributor(static_report))
    directions.extend(_sql_shape_contributor(sql))
    directions.extend(
        _explain_cost_contributor(
            explain_cost,
            peak_memory_limit_bytes=peak_memory_limit_bytes,
        )
    )
    directions.extend(_metadata_contributor(table_metadata))
    directions.extend(
        _memory_contributor(
            peak_memory_bytes,
            peak_memory_limit_bytes=peak_memory_limit_bytes,
        )
    )

    # Stable deterministic sort: (severity_rank, source_rank, kind)
    directions.sort(key=_sort_key)
    return directions


__all__ = [
    "OptimizationDirection",
    "DirectionOutcome",
    "pre_execution_diagnosis",
    "attribute_directions",
    "format_directions_for_prompt",
    "format_directions_report",
    "format_attribution_report",
    "LARGE_SCAN_BYTES",
    "HIGH_PEAK_MEMORY_BYTES",
    "MEMORY_PRESSURE_FRACTION",
]
