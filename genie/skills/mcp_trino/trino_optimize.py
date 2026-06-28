"""trino_optimize — monster-driven SQL optimization pipeline.

Five public functions: baseline / decompose / optimize / recompose / verify.
All injected callables (ExplainRunner, QueryRunner, LlmFn, CostReaderFn)
isolate cluster and LLM access so the pipeline is pure-logic and unit-testable.

No public function raises; all degrade to typed unavailable/unverified outcomes.
"""
from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import dataclass
from enum import Enum
from typing import Callable, Optional, Sequence

# ---------------------------------------------------------------------------
# §5.5  Import aliasing (shadow-free; resolves B4)
# ---------------------------------------------------------------------------
from genie.skills.mcp_trino.cost_reader import CostReading, read_cost
from genie.skills.mcp_trino.p_strategies import render_menu as _render_p_menu
from genie.skills.mcp_trino.research import rows_equivalent as _check_rows_equivalent
from genie.skills.trino_query.detection_scan import DetectionFinding, scan_sql

# ---------------------------------------------------------------------------
# §0.1  Injected callable types
# ---------------------------------------------------------------------------
ExplainRunner = Callable[[str], Optional[str]]   # (sql) -> raw EXPLAIN text | None
QueryRunner = Callable[[str], Optional[list]]    # (sql) -> list[rows] | None
LlmFn = Callable[[str], str]                     # (prompt) -> completion text
CostReaderFn = Callable[[str], "CostReading"]    # (sql) -> CostReading


# ---------------------------------------------------------------------------
# §0.2  ScanConfidence + ScanOutcome + scan_with_confidence  (closes A5, C2)
# ---------------------------------------------------------------------------

class ScanConfidence(str, Enum):
    CONFIRMED = "confirmed"  # ≥2 findings, OR single real (non-sentinel) rule_id
    UNCERTAIN = "uncertain"  # exactly 1 synthetic-pass finding, or empty list (fail-closed)
    ERROR = "error"          # scan_fn raised


@dataclass(frozen=True)
class ScanOutcome:
    findings: tuple[DetectionFinding, ...]  # parameterized (B1)
    confidence: ScanConfidence
    has_block: bool    # any finding.action == "block"
    has_advisory: bool # any finding.action in {"rewrite","advise"} with real rule_id


def scan_with_confidence(
    sql: str,
    scan_fn: Optional[Callable[[str], list[DetectionFinding]]] = None,
) -> ScanOutcome:
    """Wrap scan_sql(sql), classify confidence by finding signature. Never raises."""
    _scan = scan_fn if scan_fn is not None else scan_sql
    try:
        findings_list = _scan(sql)
    except Exception:
        return ScanOutcome(findings=(), confidence=ScanConfidence.ERROR,
                           has_block=False, has_advisory=False)

    findings: tuple[DetectionFinding, ...] = tuple(findings_list)

    # Confidence logic (precise; resolves C2 — empty list → UNCERTAIN, not CONFIRMED)
    if len(findings) == 0:
        # Contract violation (scan_sql guarantees ≥1), but fail-closed: UNCERTAIN
        confidence = ScanConfidence.UNCERTAIN
    elif (len(findings) == 1
          and findings[0].rule_id in {"none", ""}
          and findings[0].action == "pass"):
        # Synthetic-pass signature: indistinguishable clean vs swallowed exception
        confidence = ScanConfidence.UNCERTAIN
    else:
        confidence = ScanConfidence.CONFIRMED

    _real_rule_ids = {"none", "", "parse-error"}
    has_block = any(f.action == "block" for f in findings)
    has_advisory = any(
        f.action in {"rewrite", "advise"} and f.rule_id not in _real_rule_ids
        for f in findings
    )
    return ScanOutcome(findings=findings, confidence=confidence,
                       has_block=has_block, has_advisory=has_advisory)


# ---------------------------------------------------------------------------
# §1  New dataclasses (this module owns them)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Baseline:
    sql: str
    cost: CostReading
    plan_signature: Optional[str]   # SHA-256 hex; None if no plan
    row_anchor: Optional[int]       # populated by baseline() iff count_runner given
    available: bool
    reason: str                     # "ok" | "no_runner" | "cost_unavailable"


@dataclass(frozen=True)
class Fragment:
    fragment_id: str                           # stable, collision-free key (§5.3)
    sql: str
    role: str                                  # "cte" | "subquery" | "scan" | "root"
    position_hint: int
    subq_ordinal: Optional[int]                # monotonic counter for unnamed; None for named
    is_independently_runnable: bool
    is_monster: bool
    monster_rank: Optional[int]                # 1 = worst; None when not monster
    findings: tuple[DetectionFinding, ...]     # parameterized (B1)
    cost: CostReading


@dataclass(frozen=True)
class RewriteCandidate:
    fragment_id: str
    original_sql: str
    rewritten_sql: str   # == original_sql when passthrough / blocked / llm-unavailable
    action: str          # "rewrite" | "advise" | "pass" | "block"
    changed: bool
    admitted: bool
    rationale: str


@dataclass(frozen=True)
class VerifyResult:
    """Authoritative verify output (§1; supersedes thin prior-spec shape)."""
    verdict: "VerifyVerdict"
    recompose_result: "RecomposeResult"
    rows_equivalent: bool             # measured row-equivalence; False when unmeasured
    equiv_diff_reason: str
    baseline_cost: Optional[int]
    candidate_cost: Optional[int]
    cost_improvement_pct: Optional[float]
    unverified_reason: Optional[str]
    advisory_findings: tuple[DetectionFinding, ...]  # parameterized (C5); forwarded on every verdict
    scan_uncertain: bool


# ---------------------------------------------------------------------------
# §2.4  RecomposeStatus + RecomposeResult (defined before recompose() for forward refs)
# ---------------------------------------------------------------------------

class RecomposeStatus(str, Enum):
    OK                   = "ok"
    PARSE_ERROR          = "parse_error"
    CROSS_FRAGMENT_BLOCK = "block"
    CROSS_FRAGMENT_ADVISE = "advise"
    SCAN_UNCERTAIN       = "scan_uncertain"


@dataclass(frozen=True)
class RecomposeResult:
    sql: str                                            # reassembled OR original_sql on unsafe
    status: RecomposeStatus
    cross_fragment_findings: tuple[DetectionFinding, ...]
    reverted_fragments: tuple[str, ...]
    scan_ok_confident: bool


# ---------------------------------------------------------------------------
# §3  VerifyVerdict (4 values; resolves A3/A9)
# ---------------------------------------------------------------------------

class VerifyVerdict(str, Enum):
    SHIP                = "SHIP"
    NO_SHIP             = "NO_SHIP"
    UNVERIFIED          = "UNVERIFIED"
    NO_COST_IMPROVEMENT = "NO_COST_IMPROVEMENT"


# ---------------------------------------------------------------------------
# §5  Private helpers
# ---------------------------------------------------------------------------

def _fragment_key(fragment: Fragment) -> str:
    """Stable recompose key. Named CTEs use alias; unnamed use monotonic ordinal (B3)."""
    if fragment.subq_ordinal is None:
        # Named CTE
        return fragment.fragment_id
    # Unnamed fragment: collision-free via global monotonic subq_ordinal
    return f"__{fragment.role}_{fragment.subq_ordinal}__"


def _should_skip(node) -> bool:
    """Return True if node is nested inside a CTE body, UNION arm, With clause,
    or inside another Exists/In. Used by BOTH _extract_fragments and _apply_rewrites
    to maintain ordinal symmetry (I3). Never raises."""
    _SKIP_CLASSES = {"Exists", "In", "CTE", "Union", "With"}
    parent = getattr(node, "parent", None)
    while parent is not None:
        if type(parent).__name__ in _SKIP_CLASSES:
            return True
        parent = getattr(parent, "parent", None)
    return False


def _subquery_ancestor_kind(node) -> str:
    """Classify the nearest enclosing Subquery for *node*.

    Returns 'join' if that Subquery sits directly under a Join (JOIN derived-table),
    'from' if it sits under any other parent (FROM derived-table / non-JOIN), or ''
    if there is no Subquery ancestor. O(depth) parent-chain walk; no new imports;
    never raises. Used by Change ② to detect positional correlated EXISTS/IN patterns.
    Branch condition for the Priority 4.5 SCOPE-GUARD: fragment carries a positional
    finding injected by Change ③ during extraction (positional_kind != '').
    """
    try:
        p = getattr(node, "parent", None)
        while p is not None:
            if type(p).__name__ == "Subquery":
                par = getattr(p, "parent", None)
                return "join" if (par is not None and type(par).__name__ == "Join") else "from"
            p = getattr(p, "parent", None)
    except Exception:
        pass
    return ""


def _is_correlated_exists(inner_select, cte_names: set) -> bool:
    """Return True iff the subquery is correlated (references an outer-scope table via
    a qualifier not defined in its own FROM/JOIN) OR references a CTE alias (cannot run
    standalone without the WITH context). Uses qualifier-based Method A (spec §5.2).
    Never raises."""
    import sqlglot.expressions as exp
    try:
        # Collect inner FROM tables (arg key is "from_" with underscore — NOT "from")
        # "from" returns None; "from_" returns the FROM clause.
        from_node = inner_select.args.get("from_")
        inner_tables: set[str] = set()
        if from_node is not None:
            for tbl in from_node.find_all(exp.Table):
                inner_tables.add(str(tbl.name).lower())
        # Add JOIN tables
        for join in (inner_select.args.get("joins") or []):
            for tbl in join.find_all(exp.Table):
                inner_tables.add(str(tbl.name).lower())
        # Check CTE-alias reference: if any inner body table is a CTE alias,
        # the subquery cannot run standalone.
        if inner_tables & {n.lower() for n in cte_names}:
            return True
        # Check qualified column references: col.table is the str property (not Identifier)
        # col.table == "" when unqualified; non-empty and not in inner_tables → outer ref
        for col in inner_select.find_all(exp.Column):
            if col.table and col.table.lower() not in inner_tables:
                return True
        return False
    except Exception:
        # Conservative: treat as correlated on any failure
        return True


# ---------------------------------------------------------------------------
# v51b: Safe-class predicate helpers + deterministic EXISTS→IN transform
# ---------------------------------------------------------------------------

def _collect_and_leaves(node):
    """Walk the shallow AND-chain of *node*, returning individual predicate leaves.

    Does NOT recurse into nested subqueries (mitigates FP-2 for EQ classification;
    OR/AggFunc deep-checks in _sound_equi_corr_pairs remain deep on purpose — bail direction).
    """
    import sqlglot.expressions as exp
    leaves = []
    # Walk direct children that are AND nodes; anything else is a leaf
    stack = [node]
    while stack:
        cur = stack.pop()
        if isinstance(cur, exp.And):
            stack.extend(cur.args.values())
        else:
            leaves.append(cur)
    return leaves


def _collect_inner_tables(inner_select) -> set:
    """Return the set of real table names (NAME-ONLY, not aliases) from the inner SELECT.

    Mirrors _is_correlated_exists logic at trino_optimize.py:231-239. Aliased inner
    tables (FROM xxx t) are NOT collected under the alias — aliased correlation bails to ADVISE
    via the both-outer branch in _sound_equi_corr_pairs (safe; documented non-goal).
    Never raises.
    """
    import sqlglot.expressions as exp
    inner_tables: set[str] = set()
    try:
        from_node = inner_select.args.get("from_")
        if from_node is not None:
            for tbl in from_node.find_all(exp.Table):
                inner_tables.add(str(tbl.name).lower())
        for join in (inner_select.args.get("joins") or []):
            for tbl in join.find_all(exp.Table):
                inner_tables.add(str(tbl.name).lower())
    except Exception:
        pass
    return inner_tables


def _sound_equi_corr_pairs(exists_node):
    """Classification predicate for the proven-safe rewrite class (C0–C6c).

    Returns (inner_col, outer_col, non_corr_preds) when the EXISTS satisfies all
    conditions; returns None to bail to ADVISE.

    Conditions checked (C0 verified by caller before entering):
    C1: positive EXISTS (not NOT EXISTS)
    C2: EXISTS is NOT inside an OR predicate
    C2b: EXISTS body WHERE has no OR anywhere (conservative; safe bail direction)
    C3: exactly one eq-predicate pair is correlated
    C4: no aggregates in body
    C5: no DISTINCT / GROUP BY / HAVING / ORDER BY / LIMIT in body
    C6: both correlation columns are qualified (col.table != ""), exactly one pair,
        and one side is inner-only while the other is outer-only
    C6c: every column reference in non-correlation WHERE leaves must be qualified
         AND reference an inner-scope table (col.table in inner_tables)
    Never raises — any exception returns None.
    """
    import sqlglot.expressions as exp
    try:
        # C0: caller ensures this is an exp.Exists — skip; verified by iteration in caller.

        # C1: positive EXISTS only (NOT EXISTS has different semantics → anti-join)
        parent = getattr(exists_node, "parent", None)
        if isinstance(parent, exp.Not):
            return None

        # C2: EXISTS must NOT be inside an OR predicate (row-filter semantics change)
        p = parent
        while p is not None:
            if isinstance(p, exp.Or):
                return None
            p = getattr(p, "parent", None)

        inner_select = exists_node.this
        if inner_select is None:
            return None

        # C2b: no OR anywhere in inner WHERE (over-conservative, safe bail direction — FP-2)
        inner_where = inner_select.args.get("where")
        if inner_where is not None:
            if inner_where.find(exp.Or):
                return None

        # C4: no aggregates in body
        if inner_select.find(exp.AggFunc):
            return None

        # C5: no DISTINCT / GROUP BY / HAVING / ORDER BY / LIMIT / OFFSET in body
        # (OFFSET omission was a row-equivalence bug — adds per-row row-skip semantics that
        #  change the EXISTS truth value; FETCH FIRST maps to 'limit' and is already caught)
        if (
            inner_select.args.get("distinct")
            or inner_select.args.get("group")
            or inner_select.args.get("having")
            or inner_select.args.get("order")
            or inner_select.args.get("limit")
            or inner_select.args.get("offset")
        ):
            return None

        # C5b: no CTE (WITH) inside EXISTS body — dropped in rewrite → undefined table
        if inner_select.args.get("with_"):
            return None

        # C5c: no JOINs in EXISTS body — conservative bail; multi-table semantics complex
        if inner_select.args.get("joins"):
            return None

        # C5d: no TABLESAMPLE — non-deterministic per-row vs single-scan semantics diverge
        if inner_select.find(exp.TableSample):
            return None

        # C6: classify correlation pairs
        inner_tables = _collect_inner_tables(inner_select)

        if inner_where is None:
            return None

        where_leaves = _collect_and_leaves(inner_where.this if hasattr(inner_where, "this") else inner_where)

        corr_pairs = []
        non_corr_preds = []

        for leaf in where_leaves:
            if not isinstance(leaf, exp.EQ):
                # Non-EQ leaf: check all column refs are inner-scoped (C6c)
                for col in leaf.find_all(exp.Column):
                    tbl = (col.table or "").lower()
                    if not tbl or tbl not in inner_tables:
                        return None
                non_corr_preds.append(leaf)
                continue

            # EQ leaf: classify as correlation pair or non-corr pred
            lhs, rhs = leaf.left, leaf.right
            lhs_tbl = (lhs.table if isinstance(lhs, exp.Column) else "").lower()
            rhs_tbl = (rhs.table if isinstance(rhs, exp.Column) else "").lower()

            lhs_is_col = isinstance(lhs, exp.Column)
            rhs_is_col = isinstance(rhs, exp.Column)

            lhs_inner = lhs_is_col and lhs_tbl and lhs_tbl in inner_tables
            rhs_inner = rhs_is_col and rhs_tbl and rhs_tbl in inner_tables
            lhs_outer = lhs_is_col and lhs_tbl and lhs_tbl not in inner_tables
            rhs_outer = rhs_is_col and rhs_tbl and rhs_tbl not in inner_tables

            # One side non-Column (literal, expression, etc.): treat as non-corr pred
            # if the Column side is inner-scoped (C6c: must be qualified + inner)
            if not lhs_is_col and rhs_inner:
                non_corr_preds.append(leaf)
            elif not rhs_is_col and lhs_inner:
                non_corr_preds.append(leaf)
            elif not lhs_is_col and not rhs_is_col:
                # Neither side is a column — treat as non-corr (constant pred)
                non_corr_preds.append(leaf)
            elif not lhs_is_col and rhs_is_col and not rhs_inner:
                # Non-col = outer-col: bail (C6c)
                return None
            elif not rhs_is_col and lhs_is_col and not lhs_inner:
                # Non-col = outer-col: bail (C6c)
                return None
            # Both unqualified → C6 fails
            elif lhs_is_col and rhs_is_col and not lhs_tbl and not rhs_tbl:
                return None
            # Correlation pair: exactly one side inner, one side outer, both qualified
            elif lhs_inner and rhs_outer:
                corr_pairs.append((lhs, rhs))  # (inner_col, outer_col)
            elif rhs_inner and lhs_outer:
                corr_pairs.append((rhs, lhs))  # (inner_col, outer_col)
            elif lhs_inner and rhs_inner:
                # Both inner: treat as non-corr pred (inner-scoped EQ)
                non_corr_preds.append(leaf)
            else:
                # Both unqualified, both outer, or mixed unqualified → bail (C6 / C6c)
                return None

        # Exactly one correlation pair required (C6)
        if len(corr_pairs) != 1:
            return None

        inner_col, outer_col = corr_pairs[0]

        # C6c: validate non-corr preds — every column ref must be inner-scoped
        for leaf in non_corr_preds:
            for col in leaf.find_all(exp.Column):
                tbl = (col.table or "").lower()
                if not tbl or tbl not in inner_tables:
                    return None

        return (inner_col, outer_col, non_corr_preds)

    except Exception:
        return None


def _try_decorrelate_exists(sql: str):
    """Deterministic EXISTS→IN transform for the proven-safe class.

    Parses *sql* (full query), deep-copies the AST, iterates all exp.Exists nodes, applies
    _sound_equi_corr_pairs, and for each safe node replaces the EXISTS in-place with an
    exp.In(this=outer_col.copy(), query=exp.Subquery(this=new_inner_select)).
    Returns the re-serialized full query (dialect="trino") if ≥1 rewrite occurred, else None.
    Any exception → returns None (fail-safe to ADVISE, never raises).
    """
    try:
        import sqlglot
        import sqlglot.expressions as exp

        tree = sqlglot.parse_one(sql, read="trino")
        if tree is None:
            return None

        tree = copy.deepcopy(tree)
        rewrites = 0

        # Snapshot list() so in-place node.replace() during iteration is safe
        for exists_node in list(tree.find_all(exp.Exists)):
            result = _sound_equi_corr_pairs(exists_node)
            if result is None:
                continue

            inner_col, outer_col, non_corr_preds = result

            # Build the IN subquery: SELECT inner_col FROM inner_table [WHERE non_corr_preds]
            inner_select = exists_node.this  # the original SELECT inside EXISTS
            if inner_select is None:
                continue

            # Collect FROM tables from inner_select to build a minimal inner FROM
            inner_from = inner_select.args.get("from_")
            inner_joins = inner_select.args.get("joins") or []
            if inner_from is None:
                continue

            # Build new inner SELECT: SELECT inner_col FROM ... [WHERE non_corr_preds]
            new_selections = [inner_col.copy()]
            new_where = None
            if non_corr_preds:
                # Rebuild the AND-chain from non-corr predicates
                combined = non_corr_preds[0].copy()
                for pred in non_corr_preds[1:]:
                    combined = exp.And(this=combined, expression=pred.copy())
                new_where = exp.Where(this=combined)

            new_inner_select = exp.Select(
                expressions=new_selections,
            )
            new_inner_select.set("from_", inner_from.copy())
            if inner_joins:
                new_inner_select.set("joins", [j.copy() for j in inner_joins])
            if new_where is not None:
                new_inner_select.set("where", new_where)

            # Build: outer_col IN (SELECT inner_col FROM ...)
            in_node = exp.In(
                this=outer_col.copy(),
                query=exp.Subquery(this=new_inner_select),
            )

            # Replace the EXISTS node in the parent with the IN node
            exists_node.replace(in_node)
            rewrites += 1

        if rewrites == 0:
            return None

        return tree.sql(dialect="trino")

    except Exception:
        return None


def _apply_rewrites(original_sql: str, active_rewrites: dict[str, str]) -> str:
    """Pure AST substitution. Never raises — returns original_sql on any failure."""
    if not active_rewrites:
        return original_sql
    try:
        import sqlglot
        # A "__root__" fragment IS the whole query: its rewrite is the whole
        # optimized SQL, so it becomes the base that any CTE/subquery rewrites
        # then apply on top of. (Without this, a single-root-fragment query —
        # the whole query is the monster — would never get its rewrite stitched
        # back: the CTE walk below finds no matching alias and the root rewrite
        # is silently dropped.)
        base_sql = active_rewrites.get("__root__", original_sql)
        tree = sqlglot.parse_one(base_sql, read="trino")
        if tree is None:
            return original_sql

        # Guard: when both __root__ and __subquery_N__ are present, the tree was
        # re-based on the root rewrite (base_sql above), so original ordinals are
        # stale; skip the subquery pass entirely (safe-degrade, I5 / TR-COLL).
        has_root_rewrite = "__root__" in active_rewrites
        has_subq_rewrite = any(k.startswith("__subquery_") for k in active_rewrites)
        _skip_subq_pass = has_root_rewrite and has_subq_rewrite
        _subq_ordinal = 0

        # Replace CTE bodies and subquery bodies whose fragment key matches
        for node in tree.walk():
            # Named CTEs: node is a CTE with alias matching a key
            node_class = type(node).__name__
            if node_class == "CTE":
                alias_node = node.args.get("alias")
                alias = str(alias_node) if alias_node is not None else ""
                if alias in active_rewrites:
                    replacement_sql = active_rewrites[alias]
                    try:
                        new_body = sqlglot.parse_one(replacement_sql, read="trino")
                        if new_body is not None:
                            node.set("this", new_body)
                    except Exception:
                        return original_sql
            elif node_class in ("Exists", "In") and not _skip_subq_pass:
                # ---- NEW: unnamed predicate-subquery branch ----
                if _should_skip(node):
                    continue  # SKIP-FILTER: symmetric with _extract_fragments (I3)
                # TICK-CONDITION (§4.3.1): byte-identical to extract
                if node_class == "In":
                    q = node.args.get("query")
                    if q is None or type(q).__name__ != "Subquery" or q.this is None:
                        continue  # value-list IN → no tick, mirrors extract
                else:  # Exists
                    if node.this is None:
                        continue
                key = f"__subquery_{_subq_ordinal}__"
                if key in active_rewrites:
                    try:
                        repl = sqlglot.parse_one(active_rewrites[key], read="trino")
                    except Exception:
                        repl = None
                    if repl is not None:
                        if type(repl).__name__ == "Subquery":  # unwrap guard
                            repl = repl.this
                        if repl is not None and type(repl).__name__ == "Select":
                            if node_class == "Exists":
                                node.set("this", repl)
                            else:  # In: mutate the Subquery wrapper's body
                                q = node.args.get("query")
                                if q is not None:  # guard: q may be None for value-list IN
                                    q.set("this", repl)
                _subq_ordinal += 1  # tick ONLY for non-skipped, TICK-CONDITION-matching nodes

        serialized = tree.sql(dialect="trino")
        if not serialized:
            return original_sql
        return serialized
    except Exception:
        return original_sql


def _revert_until_clean(
    original_sql: str,
    candidates: Sequence[RewriteCandidate],
    initial_scan_outcome: ScanOutcome,
    scan_fn: Optional[Callable[[str], list[DetectionFinding]]] = None,
) -> RecomposeResult:
    """Greedy reverse-order revert until whole-query scan finds no block. Never raises."""
    # Collect admitted-changed candidates (the ones that were actually rewritten)
    changed_candidates = [c for c in candidates if c.admitted and c.changed]
    if not changed_candidates:
        # Nothing to revert → still blocked
        return RecomposeResult(
            sql=original_sql,
            status=RecomposeStatus.CROSS_FRAGMENT_BLOCK,
            cross_fragment_findings=initial_scan_outcome.findings,
            reverted_fragments=(),
            scan_ok_confident=False,
        )

    # Build current rewrite map from ALL admitted+changed
    current_map: dict[str, str] = {c.fragment_id: c.rewritten_sql for c in changed_candidates}
    reverted: list[str] = []

    # Walk in reverse order (last rewritten first)
    for candidate in reversed(changed_candidates):
        del current_map[candidate.fragment_id]
        reverted.append(candidate.fragment_id)

        reassembled = _apply_rewrites(original_sql, current_map)
        outcome = scan_with_confidence(reassembled, scan_fn)

        if outcome.confidence in {ScanConfidence.UNCERTAIN, ScanConfidence.ERROR}:
            # Keep reverting (conservative)
            continue

        if not outcome.has_block:
            # Clean enough — CONFIRMED and no block
            status = (RecomposeStatus.CROSS_FRAGMENT_ADVISE
                      if outcome.has_advisory
                      else RecomposeStatus.OK)
            return RecomposeResult(
                sql=reassembled,
                status=status,
                cross_fragment_findings=outcome.findings,
                reverted_fragments=tuple(reverted),
                scan_ok_confident=True,
            )
        # Still has block — continue reverting

    # All reverts exhausted — still blocked
    return RecomposeResult(
        sql=original_sql,
        status=RecomposeStatus.CROSS_FRAGMENT_BLOCK,
        cross_fragment_findings=initial_scan_outcome.findings,
        reverted_fragments=tuple(reverted),
        scan_ok_confident=False,
    )


def _normalize_rows_by_column_name(
    rows: list,
    exclude_column_names: tuple[str, ...] = (),
) -> tuple[list, tuple[int, ...]]:
    """Map name-based exclusions to positional indices after key-sorting dict rows.

    Returns (normalized_rows, exclude_indices).
    Non-dict rows + non-empty names → raises ValueError (B8).
    Empty rows → returns ([], ()).
    """
    if not rows:
        return ([], ())

    first = rows[0]
    if not isinstance(first, dict):
        if exclude_column_names:
            raise ValueError(
                "name-based exclusion requires dict rows; got positional (list/tuple) rows"
                " — cannot map names to indices safely"
            )
        return (rows, ())

    # Sort keys alphabetically for stable column order on both sides
    sorted_keys = sorted(first.keys())
    normalized = [{k: row.get(k) for k in sorted_keys} for row in rows]

    if not exclude_column_names:
        return (normalized, ())

    # Map each excluded name to its 0-based position in sorted key order
    key_pos = {k: i for i, k in enumerate(sorted_keys)}
    indices = tuple(key_pos[n] for n in exclude_column_names if n in key_pos)
    return (normalized, indices)


# ---------------------------------------------------------------------------
# §2.1  baseline()
# ---------------------------------------------------------------------------

def baseline(
    sql: str,
    explain_runner: Optional[ExplainRunner],
    count_runner: Optional[QueryRunner] = None,
) -> Baseline:
    """Measure baseline cost + plan_signature + optional row_anchor. Never raises."""
    cost = read_cost(sql, explain_runner)

    plan_signature: Optional[str] = None
    if cost.plan_json is not None:
        try:
            # Structural-only canonicalization: strip row/byte estimates
            canonical = _canonicalize_plan(cost.plan_json)
            digest_input = json.dumps(canonical, sort_keys=True)
            plan_signature = hashlib.sha256(digest_input.encode()).hexdigest()
        except Exception:
            plan_signature = None

    row_anchor: Optional[int] = None
    if count_runner is not None:
        try:
            count_sql = f"SELECT count(*) FROM ({sql}) _anchor"
            result = count_runner(count_sql)
            if result and len(result) > 0:
                first_row = result[0]
                if isinstance(first_row, dict):
                    val = next(iter(first_row.values()), None)
                else:
                    val = first_row[0] if first_row else None
                if val is not None:
                    row_anchor = int(val)
        except Exception:
            row_anchor = None

    if cost.available:
        reason = "ok"
    else:
        reason = cost.reason

    return Baseline(
        sql=sql,
        cost=cost,
        plan_signature=plan_signature,
        row_anchor=row_anchor,
        available=cost.available,
        reason=reason,
    )


def _canonicalize_plan(plan_json: object) -> object:
    """Strip row/byte estimates from plan JSON to produce a structurally stable hash input."""
    _estimate_keys = {"outputRowCount", "outputSizeInBytes", "cumulativeUserMemory",
                      "cpuCost", "memoryCost", "networkCost", "totalCost",
                      "rowCount", "sizeInBytes"}
    if isinstance(plan_json, dict):
        return {k: _canonicalize_plan(v) for k, v in plan_json.items()
                if k not in _estimate_keys}
    if isinstance(plan_json, list):
        return [_canonicalize_plan(item) for item in plan_json]
    return plan_json


# ---------------------------------------------------------------------------
# §2.2  decompose()
# ---------------------------------------------------------------------------

def decompose(
    sql: str,
    llm: LlmFn,
    cost_reader_fn: CostReaderFn,
    *,
    use_llm_ranking: bool = True,
) -> list[Fragment]:
    """Decompose SQL into rankable fragments. Never raises."""
    try:
        import sqlglot
        fragments_raw = _extract_fragments(sql)
    except Exception:
        # Fallback: treat entire query as a single root fragment
        findings = tuple(scan_sql(sql))
        try:
            cost = cost_reader_fn(sql)
        except Exception:
            cost = CostReading(None, None, None, None, available=False, reason="cost_unavailable")
        frag = Fragment(
            fragment_id="__root_0__",
            sql=sql,
            role="root",
            position_hint=0,
            subq_ordinal=0,
            is_independently_runnable=True,
            is_monster=False,
            monster_rank=None,
            findings=findings,
            cost=cost,
        )
        return [frag]

    # Build fragments with cost + findings
    built_fragments: list[Fragment] = []
    for raw in fragments_raw:
        frag_sql = raw["sql"]
        # Change ③: inject synthetic positional finding BEFORE scan_sql findings so it
        # leads the tuple and drives _heuristic_monster_ids promotion automatically.
        frag_findings_list = list(scan_sql(frag_sql))
        _pos_kind = raw.get("positional_kind", "")
        if _pos_kind:
            _label = (
                "JOIN derived-table subquery" if _pos_kind == "join"
                else "FROM derived-table subquery"
            )
            frag_findings_list.insert(0, DetectionFinding(
                rule_id="correlated-exists-per-row",
                action="advise",
                severity="high",
                message=(
                    f"correlated EXISTS/IN evaluated per-row inside {_label}; "
                    f"cost is positional (inner_cost x enclosing row count)"
                ),
                suggestion=(
                    "candidate for decorrelation to semi-join; "
                    "rewritten automatically to a semi-join IN on the executing path "
                    "when the correlation is soundly resolvable "
                    "(qualified equi-correlation, single outer table, inner-only residual predicates); "
                    "advisory-only here because no live cluster is available to verify row-equivalence"
                ),
                line=0,
            ))
        frag_findings = tuple(frag_findings_list)

        if raw["is_independently_runnable"]:
            try:
                frag_cost = cost_reader_fn(frag_sql)
            except Exception:
                frag_cost = CostReading(None, None, None, None, available=False,
                                        reason="cost_unavailable")
        else:
            frag_cost = CostReading(None, None, None, None, available=False,
                                    reason="not_independently_runnable")

        built_fragments.append(Fragment(
            fragment_id=raw["fragment_id"],
            sql=frag_sql,
            role=raw["role"],
            position_hint=raw["position_hint"],
            subq_ordinal=raw["subq_ordinal"],
            is_independently_runnable=raw["is_independently_runnable"],
            is_monster=False,  # to be set by LLM ranking below
            monster_rank=None,
            findings=frag_findings,
            cost=frag_cost,
        ))

    # Build ranked monster list heuristically; LLM refinement is opt-in for read paths.
    heuristic_monsters = _heuristic_monster_ids(built_fragments)

    monster_ids: list[str] = heuristic_monsters
    if use_llm_ranking:
        try:
            llm_response = llm(_build_monster_prompt(built_fragments, heuristic_monsters))
            parsed_monsters = _parse_monster_response(llm_response, built_fragments)
            if parsed_monsters:
                monster_ids = parsed_monsters
        except Exception:
            # LLM raising → fall back to heuristic
            monster_ids = heuristic_monsters

    # Assign is_monster + monster_rank
    final_fragments: list[Fragment] = []
    rank_counter = 1
    # Monsters first
    for frag in built_fragments:
        if frag.fragment_id in monster_ids:
            final_fragments.append(Fragment(
                fragment_id=frag.fragment_id,
                sql=frag.sql,
                role=frag.role,
                position_hint=frag.position_hint,
                subq_ordinal=frag.subq_ordinal,
                is_independently_runnable=frag.is_independently_runnable,
                is_monster=True,
                monster_rank=rank_counter,
                findings=frag.findings,
                cost=frag.cost,
            ))
            rank_counter += 1
    # Then non-monsters in source order
    for frag in built_fragments:
        if frag.fragment_id not in monster_ids:
            final_fragments.append(Fragment(
                fragment_id=frag.fragment_id,
                sql=frag.sql,
                role=frag.role,
                position_hint=frag.position_hint,
                subq_ordinal=frag.subq_ordinal,
                is_independently_runnable=frag.is_independently_runnable,
                is_monster=False,
                monster_rank=None,
                findings=frag.findings,
                cost=frag.cost,
            ))

    return final_fragments


def _extract_fragments(sql: str) -> list[dict]:
    """Extract CTE and root fragments from SQL using sqlglot AST. Returns raw dicts."""
    import sqlglot
    import sqlglot.expressions as exp

    fragments: list[dict] = []
    subq_ordinal_counter = [0]  # mutable box for monotonic counter

    try:
        tree = sqlglot.parse_one(sql, read="trino")
    except Exception:
        return [{"fragment_id": "__root_0__", "sql": sql, "role": "root",
                 "position_hint": 0, "subq_ordinal": 0,
                 "is_independently_runnable": True}]

    if tree is None:
        return [{"fragment_id": "__root_0__", "sql": sql, "role": "root",
                 "position_hint": 0, "subq_ordinal": 0,
                 "is_independently_runnable": True}]

    # Extract CTEs — key is "with_" in sqlglot Select.args
    with_clause = tree.args.get("with_")
    cte_names: set[str] = set()
    if with_clause is not None:
        ctes = with_clause.args.get("expressions", [])
        for i, cte_node in enumerate(ctes):
            try:
                alias_node = cte_node.args.get("alias")
                # TableAlias node — use .name or str() to get the alias text
                if alias_node is not None:
                    alias = getattr(alias_node, "name", None) or str(alias_node).strip()
                else:
                    alias = f"__cte_{i}__"
                cte_body = cte_node.args.get("this")
                cte_sql = cte_body.sql(dialect="trino") if cte_body else ""
                cte_names.add(alias)
                fragments.append({
                    "fragment_id": alias,
                    "sql": cte_sql,
                    "role": "cte",
                    "position_hint": i,
                    "subq_ordinal": None,  # named → no ordinal
                    "is_independently_runnable": True,
                })
            except Exception:
                continue

    # Add root fragment (the main SELECT without CTEs)
    try:
        root_sql = tree.sql(dialect="trino")
        fragments.append({
            "fragment_id": "__root__",
            "sql": root_sql,
            "role": "root",
            "position_hint": len(fragments),
            "subq_ordinal": None,
            "is_independently_runnable": True,
        })
    except Exception:
        fragments.append({
            "fragment_id": "__root__",
            "sql": sql,
            "role": "root",
            "position_hint": len(fragments),
            "subq_ordinal": None,
            "is_independently_runnable": True,
        })

    # If no CTEs found, just return the full SQL as root with a proper ordinal
    if not fragments:
        return [{"fragment_id": "__root_0__", "sql": sql, "role": "root",
                 "position_hint": 0, "subq_ordinal": 0,
                 "is_independently_runnable": True}]

    # Extract predicate subqueries (WHERE EXISTS / WHERE IN with subquery)
    # Uses ordinal-indexed monotonic counter; must be symmetric with _apply_rewrites (I3).
    # cte_names is fully populated above (after the WITH-clause extraction loop).
    _pred_ordinal = 0
    for node in tree.walk():
        if _should_skip(node):
            continue  # SKIP-FILTER: CTE-internal / nested / UNION / With
        node_class = type(node).__name__
        if node_class == "Exists":
            # inner is a bare Select (Exists.this type = Select)
            inner = node.this
            if inner is None:
                continue
        elif node_class == "In":
            q = node.args.get("query")
            # Value-list IN has q=None; subquery IN has q=Subquery; guard both
            if q is None or type(q).__name__ != "Subquery" or q.this is None:
                continue  # value-list IN → no fragment, no tick (mirrors apply TICK-CONDITION)
            inner = q.this  # the bare Select inside the Subquery wrapper
        else:
            continue  # scalar projection / derived table / other → out of scope (§1.3)
        _is_corr = _is_correlated_exists(inner, cte_names)
        _pos_kind = _subquery_ancestor_kind(node)   # "join" | "from" | "" (Change ②)
        _is_positional = _pos_kind != ""
        fragments.append({
            "fragment_id": f"__subquery_{_pred_ordinal}__",
            "sql": inner.sql(dialect="trino"),
            "role": "subquery",
            "position_hint": len(fragments),
            "subq_ordinal": _pred_ordinal,
            "is_independently_runnable": not (_is_corr or _is_positional),  # Change ②: OR-logic
            "positional_kind": _pos_kind,  # Change ②: "" | "join" | "from"
        })
        _pred_ordinal += 1  # tick: non-skipped predicate subquery

    # _assign_subq_ordinals stays a no-op (ordinals assigned inline above, spec §3.4)
    _assign_subq_ordinals(fragments, subq_ordinal_counter)

    return fragments


def _assign_subq_ordinals(fragments: list[dict], counter: list[int]) -> None:
    """Assign monotonic subq_ordinals to unnamed fragments.

    Ordinals for predicate subqueries (role=='subquery') are assigned inline
    in _extract_fragments during the walk. This post-pass is retained for
    API stability but is intentionally a no-op (spec §3.4).
    """
    for frag in fragments:
        if frag["subq_ordinal"] is None and frag["role"] != "root":
            # Named CTE — leave None
            pass
        elif frag["role"] == "root" and frag["fragment_id"] == "__root__":
            # Root fragment — leave None (named)
            pass


def _heuristic_monster_ids(fragments: list[Fragment]) -> list[str]:
    """Heuristic: fragments with block/rewrite/advise findings are monster candidates."""
    result = []
    for frag in fragments:
        for f in frag.findings:
            if f.action in {"block", "rewrite", "advise"} and f.rule_id not in {"none", ""}:
                result.append(frag.fragment_id)
                break
    return result


def _build_monster_prompt(fragments: list[Fragment], heuristic_monsters: list[str]) -> str:
    frag_summaries = []
    for frag in fragments:
        summary = {
            "id": frag.fragment_id,
            "role": frag.role,
            "findings": [{"rule_id": f.rule_id, "action": f.action} for f in frag.findings],
            "cost_available": frag.cost.available,
            "cost_scalar": frag.cost.cost_scalar,
        }
        frag_summaries.append(summary)
    return (
        f"You are a SQL optimization expert. Given these SQL fragments, "
        f"select which are 'monsters' (high-cost or problematic fragments that should be rewritten). "
        f"Heuristic suggestions: {heuristic_monsters}. "
        f"Fragments: {json.dumps(frag_summaries)}. "
        f"Return a JSON array of fragment ids to mark as monsters, ordered worst-first. "
        f"Example: [\"expensive_cte\", \"bad_subquery\"]. "
        f"If none should be rewritten, return []."
    )


def _parse_monster_response(response: str, fragments: list[Fragment]) -> list[str]:
    """Parse LLM monster selection response. Returns list of fragment_ids."""
    valid_ids = {frag.fragment_id for frag in fragments}
    try:
        # Try to find JSON array in response
        import re
        match = re.search(r'\[.*?\]', response, re.DOTALL)
        if match:
            ids = json.loads(match.group(0))
            return [str(i) for i in ids if str(i) in valid_ids]
    except Exception:
        pass
    return []


# ---------------------------------------------------------------------------
# §2.3  optimize()
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class CpGuidance:
    """Whole-query critical-path context fed into the optimize prompt (v55).

    Built by ``_produce_decompose_candidate`` from the offline cost model's
    ``cp_result`` and passed identically to every fragment's optimize call — the
    LLM gets to know which structure the cost model flagged as the bottleneck and
    which named strategy it recommends, instead of seeing only per-fragment rules.
    """
    bottleneck_label: str             # cost-model hot-node label (e.g. a CTE name / op)
    bottleneck_op: str                # the hot node's operation kind
    bottleneck_cost_pct: float        # share of the root subtree cost (0..100)
    recommended_strategy: Optional[str]  # named P-strategy the cost model maps the hot node to


def _render_cp_guidance(cp: Optional[CpGuidance]) -> str:
    """Render the cost-model guidance block, or '' when no bottleneck context exists."""
    if cp is None:
        return ""
    line = (
        f"Cost-model guidance (offline critical path): the hot node is "
        f"'{cp.bottleneck_label}' [{cp.bottleneck_op}], ~{cp.bottleneck_cost_pct}% of total "
        f"estimated cost."
    )
    if cp.recommended_strategy:
        line += (
            f" The cost model recommends prioritizing strategy {cp.recommended_strategy} "
            f"on the structure that lies on this critical path; prefer it over lower-impact "
            f"rewrites when it fits this fragment."
        )
    return line + "\n\n"


def optimize(
    fragment: Fragment,
    llm: LlmFn,
    cp_guidance: Optional[CpGuidance] = None,
) -> RewriteCandidate:
    """Apply 4-action tier to a single fragment. Never raises.

    Priority order:
    1. parse-error sentinel (OI-1 / AC-3.7)
    2. BLOCK-first (C6)
    3. Clean passthrough (B7)
    4. Non-monster passthrough
    5. Monster with rewrite/advise → LLM

    ``cp_guidance`` (v55, optional) injects whole-query critical-path context into
    the LLM prompt so the rewrite can be steered toward the cost model's bottleneck;
    None preserves the pre-v55 prompt exactly.
    """
    original = fragment.sql

    # ── Priority 1: parse-error sentinel (OI-1 / ticket §1.1 note 1)
    # Evaluated BEFORE BLOCK-first. Any fragment with rule_id=="parse-error" →
    # immediate passthrough, action="block", LLM NOT called, regardless of is_monster.
    if any(f.rule_id == "parse-error" for f in fragment.findings):
        return RewriteCandidate(
            fragment_id=fragment.fragment_id,
            original_sql=original,
            rewritten_sql=original,
            action="block",
            changed=False,
            admitted=False,
            rationale="parse-error: fragment cannot be safely rewritten",
        )

    # ── Priority 2: BLOCK-first (C6) — evaluated before any admit
    if any(f.action == "block" for f in fragment.findings):
        return RewriteCandidate(
            fragment_id=fragment.fragment_id,
            original_sql=original,
            rewritten_sql=original,
            action="block",
            changed=False,
            admitted=False,
            rationale="block: fragment contains a blocking finding",
        )

    # ── Priority 3: Clean fragment (B7) — findings beat the flag
    # Only a synthetic ("none","pass") finding → passthrough regardless of is_monster
    is_clean = (
        len(fragment.findings) == 1
        and fragment.findings[0].rule_id == "none"
        and fragment.findings[0].action == "pass"
    )
    if is_clean:
        return RewriteCandidate(
            fragment_id=fragment.fragment_id,
            original_sql=original,
            rewritten_sql=original,
            action="pass",
            changed=False,
            admitted=True,
            rationale="clean: no actionable findings",
        )

    # ── Priority 4: Non-monster passthrough
    if not fragment.is_monster:
        return RewriteCandidate(
            fragment_id=fragment.fragment_id,
            original_sql=original,
            rewritten_sql=original,
            action="pass",
            changed=False,
            admitted=True,
            rationale="non-monster: not selected for this pass",
        )

    # ── Priority 4.5: positional/correlated fragment — ADVISE ONLY, never call LLM.
    # v51a surfaces & advises; an isolated rewrite referencing outer columns is
    # semantically invalid (those columns are from the enclosing query scope and were
    # never passed to optimize()). Decorrelation under row-equivalence is v51b.
    # Branch condition: fragment carries the extraction-time positional finding (Change ③).
    _pos_finding = next(
        (f for f in fragment.findings if f.rule_id == "correlated-exists-per-row"), None
    )
    if _pos_finding is not None:
        return RewriteCandidate(
            fragment_id=fragment.fragment_id,
            original_sql=original,
            rewritten_sql=original,
            action="advise",
            changed=False,
            admitted=True,
            rationale=(
                f"correlated-exists-per-row [{_pos_finding.message}]. "
                f"{_pos_finding.suggestion}"
            ),
        )

    # ── Priority 5: Monster with rewrite/advise finding → call LLM
    # Find the highest-tier finding (rewrite > advise)
    rewrite_findings = [f for f in fragment.findings if f.action == "rewrite"]
    advise_findings = [f for f in fragment.findings if f.action == "advise"]
    tier_findings = rewrite_findings or advise_findings
    tier_action = "rewrite" if rewrite_findings else "advise"

    if not tier_findings:
        # Monster but no actionable finding → passthrough
        return RewriteCandidate(
            fragment_id=fragment.fragment_id,
            original_sql=original,
            rewritten_sql=original,
            action="pass",
            changed=False,
            admitted=True,
            rationale="monster but no rewrite/advise finding",
        )

    prompt = (
        f"Optimize this SQL fragment by applying a NAMED strategy from the menu below — "
        f"do not freestyle.\n\n"
        f"{_render_cp_guidance(cp_guidance)}"
        f"{_render_p_menu()}\n\n"
        f"Tier discipline:\n"
        f"- SAFE: apply freely (value-preserving).\n"
        f"- TRAP [MUST verify]: apply ONLY if exact row-equivalence is preserved; the "
        f"recompose gate verifies and reverts otherwise.\n"
        f"- DANGEROUS [ADVISE ONLY]: do NOT rewrite — it may change output. Leave the SQL "
        f"unchanged; the strategy is advisory only.\n\n"
        f"Detected issues for this fragment: "
        f"{[{'rule_id': f.rule_id, 'action': f.action, 'suggestion': f.suggestion} for f in tier_findings]}.\n"
        f"SQL:\n{original}\n\n"
        f"Return ONLY the rewritten SQL (no explanation, no markdown fences). "
        f"If no SAFE or TRAP strategy applies, return the original SQL unchanged."
    )
    try:
        rewritten = llm(prompt).strip()
        if not rewritten:
            rewritten = original
        changed = rewritten != original
        return RewriteCandidate(
            fragment_id=fragment.fragment_id,
            original_sql=original,
            rewritten_sql=rewritten,
            action=tier_action,
            changed=changed,
            admitted=True,
            rationale=f"{tier_action}: LLM rewrite applied",
        )
    except Exception as exc:
        return RewriteCandidate(
            fragment_id=fragment.fragment_id,
            original_sql=original,
            rewritten_sql=original,
            action=tier_action,
            changed=False,
            admitted=False,
            rationale="llm_unavailable",
        )


# ---------------------------------------------------------------------------
# §2.4  recompose()
# ---------------------------------------------------------------------------

def recompose(
    original_sql: str,
    candidates: Sequence[RewriteCandidate],
    scan_fn: Optional[Callable[[str], list[DetectionFinding]]] = None,
) -> RecomposeResult:
    """Reassemble SQL from candidates; run cross-fragment whole-query gate. Never raises."""
    try:
        # Step 1: build rewrite map from admitted+changed candidates only
        rewrite_map = {
            c.fragment_id: c.rewritten_sql
            for c in candidates
            if c.admitted and c.changed
        }

        # Step 2: apply rewrites
        if rewrite_map:
            reassembled = _apply_rewrites(original_sql, rewrite_map)
            # If rewrites were requested but reassembled == original → safe-degrade
            if reassembled == original_sql:
                return RecomposeResult(
                    sql=original_sql,
                    status=RecomposeStatus.PARSE_ERROR,
                    cross_fragment_findings=(),
                    reverted_fragments=(),
                    scan_ok_confident=False,
                )
        else:
            reassembled = original_sql

        # Step 3: cross-fragment gate on serialized reassembled string
        scan_outcome = scan_with_confidence(reassembled, scan_fn)

        # Step 4a: ERROR (the static re-scan itself crashed) → fail-closed revert.
        # A crashed scan cannot be trusted; do not ship.
        if scan_outcome.confidence == ScanConfidence.ERROR:
            return RecomposeResult(
                sql=original_sql,
                status=RecomposeStatus.SCAN_UNCERTAIN,
                cross_fragment_findings=scan_outcome.findings,
                reverted_fragments=(),
                scan_ok_confident=False,
            )

        # Step 4b: UNCERTAIN (synthetic-pass / empty findings = NO actionable finding) →
        # PROCEED, do NOT revert. The recompose cross-fragment gate exists to catch a
        # NEWLY-introduced cross-fragment monster (a block/rewrite/advise finding on the
        # reassembled query); a clean re-scan means no such monster. Reverting here would
        # undo a SUCCESSFUL optimization (one that removed the anti-pattern, leaving the
        # reassembled query legitimately clean — the common case). verify()'s LIVE
        # row-equivalence is the real P0 safety gate; this static scan is only a cheap
        # pre-check. We ship the reassembled SQL but mark scan_ok_confident=False so the
        # unconfident static pre-check stays transparent and verify makes the final call.
        if scan_outcome.confidence == ScanConfidence.UNCERTAIN:
            return RecomposeResult(
                sql=reassembled,
                status=RecomposeStatus.OK,
                cross_fragment_findings=scan_outcome.findings,
                reverted_fragments=(),
                scan_ok_confident=False,
            )

        # Step 5: CONFIRMED block → try to revert
        if scan_outcome.has_block:
            return _revert_until_clean(original_sql, candidates, scan_outcome, scan_fn)

        # Step 6: advise-only
        if scan_outcome.has_advisory:
            return RecomposeResult(
                sql=reassembled,
                status=RecomposeStatus.CROSS_FRAGMENT_ADVISE,
                cross_fragment_findings=scan_outcome.findings,
                reverted_fragments=(),
                scan_ok_confident=True,
            )

        # Step 7: clean
        return RecomposeResult(
            sql=reassembled,
            status=RecomposeStatus.OK,
            cross_fragment_findings=scan_outcome.findings,
            reverted_fragments=(),
            scan_ok_confident=True,
        )
    except Exception:
        return RecomposeResult(
            sql=original_sql,
            status=RecomposeStatus.PARSE_ERROR,
            cross_fragment_findings=(),
            reverted_fragments=(),
            scan_ok_confident=False,
        )


# ---------------------------------------------------------------------------
# §2.5  verify()
# ---------------------------------------------------------------------------

def verify(
    original_sql: str,
    recompose_result: RecomposeResult,
    query_runner: Optional[QueryRunner],
    explain_runner: Optional[ExplainRunner],
    baseline_cost: Optional[int],
    exclude_column_names: tuple[str, ...] = (),
) -> VerifyResult:
    """Live row-equivalence gate. Never raises (except via _normalize which we catch).

    Advisory findings from CROSS_FRAGMENT_ADVISE are forwarded on EVERY verdict.
    """
    advisory = tuple(
        f for f in recompose_result.cross_fragment_findings
        if f.action in {"rewrite", "advise"} and f.rule_id not in {"none", ""}
    ) if recompose_result.status == RecomposeStatus.CROSS_FRAGMENT_ADVISE else ()

    def _make_result(
        verdict: VerifyVerdict,
        rows_equiv: bool = False,
        equiv_diff: str = "",
        cand_cost: Optional[int] = None,
        cost_pct: Optional[float] = None,
        unverified: Optional[str] = None,
        scan_unc: bool = False,
    ) -> VerifyResult:
        return VerifyResult(
            verdict=verdict,
            recompose_result=recompose_result,
            rows_equivalent=rows_equiv,
            equiv_diff_reason=equiv_diff,
            baseline_cost=baseline_cost,
            candidate_cost=cand_cost,
            cost_improvement_pct=cost_pct,
            unverified_reason=unverified,
            advisory_findings=advisory,
            scan_uncertain=scan_unc,
        )

    # Priority 1: PARSE_ERROR → NO_SHIP
    if recompose_result.status == RecomposeStatus.PARSE_ERROR:
        return _make_result(VerifyVerdict.NO_SHIP, equiv_diff="recompose parse error")

    # Priority 2: CROSS_FRAGMENT_BLOCK → NO_SHIP
    if recompose_result.status == RecomposeStatus.CROSS_FRAGMENT_BLOCK:
        return _make_result(VerifyVerdict.NO_SHIP, equiv_diff="cross-fragment block finding")

    # Priority 3: SCAN_UNCERTAIN → NO_SHIP
    if recompose_result.status == RecomposeStatus.SCAN_UNCERTAIN:
        return _make_result(VerifyVerdict.NO_SHIP,
                            equiv_diff="scan uncertain: gate may be broken",
                            scan_unc=True)

    # Priority 4: no-op rewrite → NO_COST_IMPROVEMENT
    if recompose_result.sql == original_sql:
        return _make_result(VerifyVerdict.NO_COST_IMPROVEMENT,
                            equiv_diff="no-op rewrite: candidate identical to original")

    # Priority 5: no runner → UNVERIFIED
    if query_runner is None:
        return _make_result(
            VerifyVerdict.UNVERIFIED,
            unverified=(
                "no query_runner: equivalence NOT claimed; "
                "user must verify on a live cluster before applying this rewrite"
            ),
        )

    # Priority 6: run baseline then candidate
    base_rows: Optional[list] = None
    try:
        base_rows = query_runner(original_sql)
    except Exception as exc:
        return _make_result(
            VerifyVerdict.UNVERIFIED,
            unverified=f"baseline run failed: {exc}",
        )
    if base_rows is None:
        return _make_result(
            VerifyVerdict.UNVERIFIED,
            unverified="baseline run returned None (cluster unreachable)",
        )

    cand_rows: Optional[list] = None
    try:
        cand_rows = query_runner(recompose_result.sql)
    except Exception as exc:
        return _make_result(VerifyVerdict.NO_SHIP, equiv_diff=f"candidate run failed: {exc}")
    if cand_rows is None:
        return _make_result(VerifyVerdict.NO_SHIP, equiv_diff="candidate run returned None")

    # Priority 7: cross-schema guard + normalization
    try:
        base_norm, base_exc_idx = _normalize_rows_by_column_name(base_rows, exclude_column_names)
        cand_norm, cand_exc_idx = _normalize_rows_by_column_name(cand_rows, exclude_column_names)
    except ValueError as exc:
        return _make_result(
            VerifyVerdict.UNVERIFIED,
            unverified=str(exc),
        )

    # 7b: empty-set guard — only compare schemas when BOTH are non-empty and dict-typed
    if (base_norm and cand_norm
            and isinstance(base_norm[0], dict) and isinstance(cand_norm[0], dict)):
        base_keys = sorted(base_norm[0].keys())
        cand_keys = sorted(cand_norm[0].keys())
        if base_keys != cand_keys:
            return _make_result(
                VerifyVerdict.NO_SHIP,
                equiv_diff=f"column schema mismatch: {base_keys} vs {cand_keys}",
            )

    # Use base_exc_idx for consistency (schemas matched, so positions align)
    equiv, diff = _check_rows_equivalent(base_norm, cand_norm, base_exc_idx)

    if not equiv:
        return _make_result(VerifyVerdict.NO_SHIP, equiv_diff=diff.reason)

    # Rows equivalent — check cost improvement
    cand_cost_reading = read_cost(recompose_result.sql, explain_runner)
    cand_cost_scalar = cand_cost_reading.cost_scalar

    cost_pct: Optional[float] = None
    if baseline_cost is not None and cand_cost_scalar is not None and baseline_cost > 0:
        cost_pct = (baseline_cost - cand_cost_scalar) / baseline_cost * 100.0

    if baseline_cost is not None and cand_cost_scalar is not None and cand_cost_scalar < baseline_cost:
        return _make_result(
            VerifyVerdict.SHIP,
            rows_equiv=True,
            equiv_diff=diff.reason,
            cand_cost=cand_cost_scalar,
            cost_pct=cost_pct,
        )
    else:
        return _make_result(
            VerifyVerdict.NO_COST_IMPROVEMENT,
            rows_equiv=True,
            equiv_diff=diff.reason,
            cand_cost=cand_cost_scalar,
            cost_pct=cost_pct,
        )


__all__ = [
    # callable types
    "ExplainRunner", "QueryRunner", "LlmFn", "CostReaderFn",
    # enums
    "ScanConfidence", "RecomposeStatus", "VerifyVerdict",
    # dataclasses
    "ScanOutcome", "Baseline", "Fragment", "RewriteCandidate",
    "RecomposeResult", "VerifyResult",
    # public functions
    "scan_with_confidence", "baseline", "decompose", "optimize", "recompose", "verify",
]
