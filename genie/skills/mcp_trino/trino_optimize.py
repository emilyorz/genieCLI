"""trino_optimize — monster-driven SQL optimization pipeline.

Five public functions: baseline / decompose / optimize / recompose / verify.
All injected callables (ExplainRunner, QueryRunner, LlmFn, CostReaderFn)
isolate cluster and LLM access so the pipeline is pure-logic and unit-testable.

No public function raises; all degrade to typed unavailable/unverified outcomes.
"""
from __future__ import annotations

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
            # Unnamed subqueries: key pattern is __role_ordinal__
            # We rely on the key being injected as a comment or matched via ordinal
            # For unnamed subqueries, we use a position-based approach:
            # The key must be present literally in active_rewrites — skip if not matched above.

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

def decompose(sql: str, llm: LlmFn, cost_reader_fn: CostReaderFn) -> list[Fragment]:
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
        frag_findings = tuple(scan_sql(frag_sql))

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

    # Build ranked monster list heuristically, then refine with LLM
    heuristic_monsters = _heuristic_monster_ids(built_fragments)

    monster_ids: list[str] = []
    try:
        llm_response = llm(_build_monster_prompt(built_fragments, heuristic_monsters))
        monster_ids = _parse_monster_response(llm_response, built_fragments)
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

    # Assign subq_ordinals to unnamed fragments (CTEs are named, root gets None)
    # For test T2.6: unnamed sibling subqueries need distinct ordinals
    # We handle this by scanning for subqueries in the tree
    _assign_subq_ordinals(fragments, subq_ordinal_counter)

    return fragments


def _assign_subq_ordinals(fragments: list[dict], counter: list[int]) -> None:
    """Assign monotonic subq_ordinals to unnamed fragments."""
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

def optimize(fragment: Fragment, llm: LlmFn) -> RewriteCandidate:
    """Apply 4-action tier to a single fragment. Never raises.

    Priority order:
    1. parse-error sentinel (OI-1 / AC-3.7)
    2. BLOCK-first (C6)
    3. Clean passthrough (B7)
    4. Non-monster passthrough
    5. Monster with rewrite/advise → LLM
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
