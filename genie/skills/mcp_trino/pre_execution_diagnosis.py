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

import math
import os
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from genie.skills.trino_query.sql_static.rule_ids import (
    RULE_CARTESIAN_JOIN,
    RULE_JOIN_FIRST_FILTER_LATE,
    RULE_JOIN_KEY_COMPUTED,
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

# Bytes above which a REPLICATED (broadcast) join's build side is a memory-blowup risk.
# Each worker materialises a full copy of the build side; 1 GiB per worker threatens
# per-node OOM at cluster scale — same order as HIGH_PEAK_MEMORY_BYTES (line 43).
BROADCAST_BUILD_BLOWUP_BYTES: int = 1 * 1024**3            # 1 GiB

# Engineering anchor ~100 MiB (order-of-magnitude alignment with Trino's broadcast
# heuristic; no version-pinned source). Bytes below which a PARTITIONED join's build
# side is a broadcast candidate. Unit: BYTES only (IL-U3/FM-10).
BROADCAST_CANDIDATE_SMALL_SIDE_BYTES: int = 100 * 1024**2  # 100 MiB

# Rows below which a PARTITIONED join's build side is a broadcast candidate (bytes
# fallback when build-side bytes is absent). 1M rows × ~100 B/row ≈ 100 MiB —
# consistent with the bytes threshold. Unit: ROWS only (FM-10) — never compared
# against a _BYTES value.
BROADCAST_CANDIDATE_SMALL_SIDE_ROWS: int = 1_000_000       # 1M rows


def _resolve_memory_pressure_fraction() -> float:
    """Effective warning fraction. GENIE_TRINO_MEMORY_PRESSURE_FRACTION overrides
    the MEMORY_PRESSURE_FRACTION default (0.5) at CALL TIME (monkeypatch-testable;
    lets Sam tune without code change — sam-decisions.md:6). Clamped to (0, 1].
    Bad / out-of-range env → default."""
    raw = os.environ.get("GENIE_TRINO_MEMORY_PRESSURE_FRACTION")
    if raw is None:
        return MEMORY_PRESSURE_FRACTION
    try:
        frac = float(raw)
    except (ValueError, TypeError):
        return MEMORY_PRESSURE_FRACTION
    if not math.isfinite(frac) or frac <= 0 or frac > 1:
        return MEMORY_PRESSURE_FRACTION  # clamp-to-default on out-of-range / non-finite
    return frac


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
        return max(1, int(peak_memory_limit_bytes * _resolve_memory_pressure_fraction()))
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
    RULE_JOIN_FIRST_FILTER_LATE: "fix-join-first-filter-late",
    RULE_JOIN_KEY_COMPUTED: "fix-join-key-computed",
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


# ---------------------------------------------------------------------------
# F1 — join-distribution parser helpers
# ---------------------------------------------------------------------------

_DIST_TOKEN_MAP: dict[str, str] = {
    "PARTITIONED": "PARTITIONED",
    "REPLICATED":  "REPLICATED",
    "REPLICATE":   "REPLICATED",   # normalise singular → REPLICATED
}
# Note: _DIST_TOKEN_MAP and _REMOTE_EXCHANGE_MAP below are NEW constants introduced
# by F1; neither is a pre-existing pattern (IL-U13 / IL-U11).


def _encoding_a_distribution(node_name: str) -> str | None:
    """Extract join distribution from name-bracket via token-split.

    MANDATED approach (FM-18): split on r'[,_\\s]+', exact-token membership.
    The \\b word-boundary regex (re.search(r'\\b(PARTITIONED|REPLICATED)\\b', …)) is
    FORBIDDEN — '_' is a \\w char so \\bPARTITIONED\\b silently fails on INNER_PARTITIONED.
    Token-split also avoids the REPLICATE-in-REPLICATED substring trap.
    """
    start = node_name.find("[")
    if start == -1:
        return None
    bracket = node_name[start + 1:].rstrip("]")
    for tok in re.split(r"[,_\s]+", bracket):
        mapped = _DIST_TOKEN_MAP.get(tok.strip().upper())
        if mapped is not None:
            return mapped
    return None


def _encoding_b_distribution(node: dict) -> str | None:
    """Extract join distribution from descriptor dual-probe (version-hedging).

    Priority: distributionType first, then distribution.
    Priority is version-hedging, not authoritative; either non-None value wins;
    first-wins is deterministic. No Trino version citation available — develop
    must NOT treat distributionType as canonical (spec §2.B).
    """
    desc = node.get("descriptor") or {}
    raw = desc.get("distributionType") or desc.get("distribution")
    if not isinstance(raw, str) or not raw:
        return None
    upper = raw.upper().strip()
    if "PARTITION" in upper:   # PARTITIONED / REPARTITION substrings
        return "PARTITIONED"
    if "REPLIC" in upper:      # REPLICATE / REPLICATED substrings
        return "REPLICATED"
    return None


_REMOTE_EXCHANGE_MAP: dict[str, str] = {
    "REPARTITION": "PARTITIONED",   # FM-19: shuffle-partitioned join
    "REPLICATE":   "REPLICATED",
    "REPLICATED":  "REPLICATED",
}


def _remoteexchange_child_distribution(node: dict) -> str | None:
    """Detect RemoteExchange parent-context distribution (Encoding C).

    Walk is top-down (spec §2.C). When a RemoteExchange[…] node has a mapped bracket,
    its distribution is propagated to direct and transitive descendants until a closer
    RemoteExchange overrides it.

    NOTE: existing with_aggregate.json has 'RemoteExchange' (no bracket) → returns None
    here → existing plans unaffected. The gap this closes is distribution-bracket
    content, not RemoteExchange nodes per se (spec §0.3 / explore attempt-2).
    """
    name = node.get("name") or ""
    if not name.startswith("RemoteExchange"):
        return None
    start = name.find("[")
    if start == -1:
        return None                    # bare RemoteExchange — no bracket, no distribution
    bracket = name[start + 1:].rstrip("]").strip().upper()
    return _REMOTE_EXCHANGE_MAP.get(bracket)


def _resolve_node_distribution(node: dict, parent_dist: str | None) -> str:
    """Priority A → B → C(parent) → 'UNKNOWN' (spec §2.D)."""
    return (
        _encoding_a_distribution(node.get("name", ""))
        or _encoding_b_distribution(node)
        or parent_dist
        or "UNKNOWN"
    )


def _extract_side_estimates(
    children: list, idx: int
) -> tuple[float | None, float | None]:
    """Read (rows, bytes) from children[idx].estimates[0] with full guard (FM-1/FM-2/FM-8).

    FM-2: None = absent; 0.0 = a present zero (NOT dropped).
    FM-1: inf/nan → None (rejected before any comparison or int()).
    FM-8: null or non-dict child → (None, None), no crash.
    D-bool (IL-U4): bool is an int subclass; True→1.0 would cause a false S2. Rejected.
    """
    if idx >= len(children):
        return None, None
    child = children[idx]
    if not isinstance(child, dict):            # FM-8: null / non-dict child guard
        return None, None
    estimates = child.get("estimates") or []
    est = next((e for e in estimates if isinstance(e, dict)), None)
    if est is None:
        return None, None

    def _safe(val: object) -> float | None:
        if val is None:                        # FM-2: None = absent
            return None
        if isinstance(val, bool):              # D-bool / IL-U4: bool is int subclass, reject
            return None
        if not isinstance(val, (int, float)):
            return None
        if not math.isfinite(val):             # FM-1: reject inf/nan
            return None
        return float(val)

    return _safe(est.get("outputRowCount")), _safe(est.get("outputSizeInBytes"))


_JOIN_BASE_NAMES: frozenset[str] = frozenset({"Join", "SemiJoin", "HashJoin", "MergeJoin"})
# CrossJoin excluded: handled by RULE_CARTESIAN_JOIN. "CROSS" never appears in output.

_JOIN_TYPE_MAP: dict[str, str] = {
    "INNER": "INNER", "LEFT": "LEFT", "RIGHT": "RIGHT", "FULL": "FULL", "SEMI": "SEMI",
}

_MAX_PLAN_DEPTH: int = 50   # FM-16 recursion guard


def _extract_join_facts(raw_plan_json: Any) -> list[dict]:
    """Top-down walk; return one dict per join node across ALL fragments. Fail-open.

    v1 limitation: dict-wrapped fragment lists (e.g. {"fragments":[…]}) are walked as a
    single root; cross-fragment joins under non-children keys are not detected. Fail-open.

    FM-6: reads node['children'][0]/[1] from the raw plan (NEVER from plan_signature
    output), to preserve probe(0)/build(1) identity.
    """
    if raw_plan_json is None:                       # FM-20: is None, NOT isinstance(dict)
        return []
    roots = raw_plan_json if isinstance(raw_plan_json, list) else [raw_plan_json]
    results: list[dict] = []

    def _walk(node: Any, depth: int, parent_dist: str | None) -> None:
        if depth > _MAX_PLAN_DEPTH or not isinstance(node, dict):
            return
        name = node.get("name", "")
        child_dist = _remoteexchange_child_distribution(node)
        effective = child_dist if child_dist is not None else parent_dist  # closest ancestor wins

        base = name.split("[", 1)[0].strip()
        if base in _JOIN_BASE_NAMES:
            distribution = _resolve_node_distribution(node, parent_dist)
            desc = node.get("descriptor") or {}
            join_type = _JOIN_TYPE_MAP.get(str(desc.get("type", "")).upper(), "UNKNOWN")
            children = node.get("children") or []
            p_rows, p_bytes = _extract_side_estimates(children, 0)
            b_rows, b_bytes = _extract_side_estimates(children, 1)
            results.append({
                "node_name": name,
                "join_type": join_type,
                "distribution": distribution,
                "probe_side_rows": p_rows,
                "probe_side_bytes": p_bytes,
                "build_side_rows": b_rows,
                "build_side_bytes": b_bytes,
                "has_estimates": any(v is not None for v in (p_rows, p_bytes, b_rows, b_bytes)),
            })
        for child in node.get("children") or []:
            _walk(child, depth + 1, effective)

    try:
        for root in roots:
            _walk(root, 0, None)
    except Exception:
        return []                                   # FM-23: absolute fail-open
    return results


def _diagnose_join_facts(join_facts: list[dict]) -> list[OptimizationDirection]:
    """Map per-join facts to optimization directions (F2 signal rules, spec §3).

    Mutual exclusivity by distribution:
    - REPLICATED → only S1 can fire
    - PARTITIONED → only S2 can fire (bytes path XOR rows fallback)
    - UNKNOWN → only S3 can fire

    Lower-bound guard (IL-S2 / defect-class 3): S2 requires 0 < x < threshold.
    A 0-value build side is degenerate (nothing to broadcast); must not fire S2.

    FM-22 injection safety: all rationale and evidence strings are numeric/structured
    prose. node_safe strips bracket content before any interpolation.
    """
    directions: list[OptimizationDirection] = []
    for fact in join_facts:
        dist    = fact["distribution"]
        b_bytes = fact["build_side_bytes"]
        b_rows  = fact["build_side_rows"]
        # FM-22: strip bracket / criteria from node_name before any evidence interpolation
        node_safe = fact["node_name"].split("[", 1)[0].strip()

        if dist == "REPLICATED":
            if b_bytes is not None and b_bytes > BROADCAST_BUILD_BLOWUP_BYTES:
                directions.append(OptimizationDirection(
                    kind="join-broadcast-blowup",
                    severity="high",
                    rationale=(
                        f"Build side ({b_bytes / 1024**3:.1f} GiB) is replicated to every "
                        "worker; at cluster scale each per-worker copy risks OOM. Consider "
                        "a partitioned (hash-repartition) join or filtering the build side."
                    ),
                    evidence=f"explain:join-broadcast-blowup build_side_bytes={int(b_bytes)}",
                    target_metric="peak_memory_bytes",
                ))
        elif dist == "PARTITIONED":
            if b_bytes is not None and 0 < b_bytes < BROADCAST_CANDIDATE_SMALL_SIDE_BYTES:
                directions.append(OptimizationDirection(
                    kind="join-broadcast-candidate",
                    severity="medium",
                    rationale=(
                        f"Build side ({b_bytes / 1024**2:.0f} MiB) is small enough for a "
                        "broadcast join; switching to REPLICATED removes the hash-repartition "
                        "shuffle and may reduce wall time."
                    ),
                    evidence=f"explain:join-broadcast-candidate build_side_bytes={int(b_bytes)}",
                    target_metric="wall_time_ms",
                ))
            elif (
                b_bytes is None
                and b_rows is not None
                and 0 < b_rows < BROADCAST_CANDIDATE_SMALL_SIDE_ROWS
            ):
                directions.append(OptimizationDirection(
                    kind="join-broadcast-candidate",
                    severity="medium",
                    rationale=(
                        f"Build side ({int(b_rows):,} rows) is small enough for a broadcast "
                        "join; switching to REPLICATED removes the hash-repartition shuffle "
                        "and may reduce wall time."
                    ),
                    evidence=f"explain:join-broadcast-candidate build_side_rows={int(b_rows)}",
                    target_metric="wall_time_ms",
                ))
        else:  # UNKNOWN → S3
            directions.append(OptimizationDirection(
                kind="join-stats-gap",
                severity="low",
                rationale=(
                    "Join distribution is unknown from the EXPLAIN plan; distribution and "
                    "size analysis cannot be performed. Consider running ANALYZE on the "
                    "join tables to refresh statistics."
                ),
                evidence=f"explain:join-stats-gap node={node_safe}",
                target_metric="query_stats",
            ))
    return directions


def _join_diagnosis_contributor(explain_cost: Any) -> list[OptimizationDirection]:
    """Emit join-distribution signals from the already-fetched EXPLAIN plan.

    Receives the same 3-tuple as _explain_cost_contributor (rows_est, bytes_est,
    raw_plan_json). Mirrors the unpack pattern at _explain_cost_contributor.
    Fail-open: returns [] on any error. Zero additional cluster round-trips.
    """
    try:
        if explain_cost is None:
            return []
        try:
            _, _, raw_plan_json = explain_cost          # mirrors _explain_cost_contributor unpack
        except (TypeError, ValueError):                 # FM-15: never change tuple shape
            return []
        if raw_plan_json is None:                       # FM-20: is None ONLY, not isinstance(dict)
            return []
        join_facts = _extract_join_facts(raw_plan_json)
        if not join_facts:
            return []                                   # no facts → no signal (IL-S5)
        return _diagnose_join_facts(join_facts)
    except Exception:
        return []                                       # FM-23: absolute fail-open


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
    if len(directions) > limit:
        extra = len(directions) - limit
        lines.append(f"(+{extra} more directions in the full report)")
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
    directions.extend(_join_diagnosis_contributor(explain_cost))   # F3: join-distribution signals
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
    "BROADCAST_BUILD_BLOWUP_BYTES",
    "BROADCAST_CANDIDATE_SMALL_SIDE_BYTES",
    "BROADCAST_CANDIDATE_SMALL_SIDE_ROWS",
]
