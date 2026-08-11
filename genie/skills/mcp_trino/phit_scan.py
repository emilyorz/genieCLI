"""Engine-side structured P-hit SCAN (Stage-2).

Pure AST scan → list[PHit]. No network, no LLM line guessing.
Public IDs are catalog P-ids only (P1–P10).

Maps internal transformation ideas (T*) to P-ids in comments only.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional


@dataclass(frozen=True)
class Span:
    line: Optional[int] = None
    col: Optional[int] = None


@dataclass(frozen=True)
class PHit:
    pid: str
    node_ref: str
    tier: str  # safe | trap | dangerous (lowercase)
    why: str
    span: Optional[Span] = None


def _tier_for(pid: str) -> str:
    try:
        from genie.skills.mcp_trino.p_strategies import P_STRATEGY_BY_ID
        s = P_STRATEGY_BY_ID.get(pid)
        if s is not None:
            return str(s.tier).lower()
    except Exception:
        pass
    defaults = {
        "P1": "safe",
        "P2": "trap",
        "P3": "dangerous",
        "P4": "dangerous",
        "P5": "trap",
        "P6": "dangerous",
        "P7": "safe",
        "P8": "safe",
        "P9": "trap",
        "P10": "trap",
    }
    return defaults.get(pid, "dangerous")


def _span_of(node: Any) -> Optional[Span]:
    try:
        meta = getattr(node, "meta", None) or {}
        line = meta.get("line") or meta.get("start_line")
        col = meta.get("col") or meta.get("start_col")
        if line is None and col is None:
            return None
        return Span(
            line=int(line) if line is not None else None,
            col=int(col) if col is not None else None,
        )
    except Exception:
        return None


def _is_computed_side(node: Any, exp: Any) -> bool:
    """True if join key side is wrapped in function/cast/concat/coalesce/arithmetic."""
    if node is None:
        return False
    if isinstance(node, exp.Column):
        return False
    # Common wrappers that kill sargability / hash-join keys.
    bad = (
        exp.Anonymous,
        exp.Func,
        exp.Cast,
        exp.TryCast,
        exp.Coalesce,
        exp.Concat,
        exp.DPipe,  # ||
        exp.Upper,
        exp.Lower,
        exp.Trim,
        exp.Substring,
        exp.Add,
        exp.Sub,
        exp.Mul,
        exp.Div,
    )
    if isinstance(node, bad):
        return True
    # Nested: function over column still counts.
    try:
        if any(isinstance(n, bad) for n in node.find_all(bad) if n is not node):
            # only if a column is buried inside
            if any(isinstance(c, exp.Column) for c in node.find_all(exp.Column)):
                return True
    except Exception:
        pass
    return False


def _join_on_predicates(join: Any, exp: Any) -> list[Any]:
    on = join.args.get("on")
    if on is None:
        return []
    eqs = list(on.find_all(exp.EQ))
    return eqs if eqs else [on]


def _scan_p1(tree: Any, exp: Any) -> list[PHit]:
    hits: list[PHit] = []
    for i, join in enumerate(tree.find_all(exp.Join)):
        for pred in _join_on_predicates(join, exp):
            sides = []
            if isinstance(pred, exp.EQ):
                sides = [pred.left, pred.right]
            else:
                # non-eq join predicate: still flag computed columns inside
                sides = [pred]
            if any(_is_computed_side(s, exp) for s in sides):
                hits.append(
                    PHit(
                        pid="P1",
                        node_ref=f"ast:join_on_computed[{i}]",
                        tier=_tier_for("P1"),
                        why="JOIN ON uses function/cast/concat/coalesce/arithmetic on a key side; push compute upstream (T2→P1).",
                        span=_span_of(join),
                    )
                )
                break
    return hits


def _inner_scope_names(inner_select: Any, exp: Any) -> set[str]:
    scope: set[str] = set()
    try:
        nodes = []
        from_node = inner_select.args.get("from_")
        if from_node is not None:
            nodes.append(from_node)
        nodes.extend(inner_select.args.get("joins") or [])
        for node in nodes:
            for tbl in node.find_all(exp.Table):
                if tbl.name:
                    scope.add(str(tbl.name).lower())
                if tbl.alias:
                    scope.add(str(tbl.alias).lower())
    except Exception:
        pass
    return scope


def _is_correlated_exists(exists_node: Any, exp: Any) -> bool:
    inner = exists_node.this
    if inner is None:
        return False
    scope = _inner_scope_names(inner, exp)
    where = inner.args.get("where") if hasattr(inner, "args") else None
    if where is None:
        # still treat bare EXISTS as structural candidate for P9 playbook
        return True
    try:
        for col in where.find_all(exp.Column):
            tbl = (col.table or "").lower()
            if tbl and tbl not in scope:
                return True
        for eq in where.find_all(exp.EQ):
            lhs, rhs = eq.left, eq.right
            if isinstance(lhs, exp.Column) and isinstance(rhs, exp.Column):
                lt = (lhs.table or "").lower()
                rt = (rhs.table or "").lower()
                lhs_in = bool(lt) and lt in scope
                rhs_in = bool(rt) and rt in scope
                if lhs_in ^ rhs_in:
                    return True
    except Exception:
        return True
    return True


def _scan_p9(tree: Any, exp: Any) -> list[PHit]:
    hits: list[PHit] = []
    for i, exists_node in enumerate(tree.find_all(exp.Exists)):
        if not _is_correlated_exists(exists_node, exp):
            continue
        hits.append(
            PHit(
                pid="P9",
                node_ref=f"ast:exists_correlated[{i}]",
                tier=_tier_for("P9"),
                why="Correlated EXISTS/IN-style existence check; prefer pre-aggregate + join (T1→P9), not bare P2 on 1-to-many.",
                span=_span_of(exists_node),
            )
        )
    # IN (SELECT ...) correlated-ish: treat subquery IN as P9 candidate when subquery present
    for i, inn in enumerate(tree.find_all(exp.In)):
        query = inn.args.get("query") or getattr(inn, "query", None)
        if query is None and hasattr(inn, "this"):
            # sqlglot sometimes stores subquery differently
            pass
        sub = None
        try:
            for sel in inn.find_all(exp.Select):
                if sel is not tree and sel.find_ancestor(exp.In) is inn:
                    sub = sel
                    break
        except Exception:
            sub = None
        if sub is None:
            continue
        hits.append(
            PHit(
                pid="P9",
                node_ref=f"ast:in_subquery[{i}]",
                tier=_tier_for("P9"),
                why="IN (subquery) pattern; consider preagg/semi-join rewrite (T1 family→P9).",
                span=_span_of(inn),
            )
        )
    return hits


def _scan_p3(tree: Any, exp: Any) -> list[PHit]:
    hits: list[PHit] = []
    # LIKE '%x%'
    for i, like in enumerate(tree.find_all(exp.Like)):
        try:
            pattern = like.expression.sql() if hasattr(like, "expression") else ""
        except Exception:
            pattern = str(like)
        pat = pattern.replace("'", "")
        if "%" in pat:
            hits.append(
                PHit(
                    pid="P3",
                    node_ref=f"ast:like[{i}]",
                    tier=_tier_for("P3"),
                    why="LIKE/pattern match; token contains rewrite is DANGEROUS (T3→P3 advise-only).",
                    span=_span_of(like),
                )
            )
    # strpos(…, …) often used for CSV membership
    for i, call in enumerate(tree.find_all(exp.Anonymous)):
        name = (getattr(call, "this", None) or getattr(call, "name", None) or "")
        name_s = str(name).lower()
        if name_s in {"strpos", "str_pos", "position"}:
            hits.append(
                PHit(
                    pid="P3",
                    node_ref=f"ast:strpos[{i}]",
                    tier=_tier_for("P3"),
                    why="strpos/position string membership; consider array contains (T3→P3 advise-only).",
                    span=_span_of(call),
                )
            )
    return hits


def _scan_p4(tree: Any, exp: Any) -> list[PHit]:
    hits: list[PHit] = []
    for i, call in enumerate(tree.find_all(exp.Anonymous)):
        name = str(getattr(call, "this", None) or getattr(call, "name", None) or "").lower()
        if name == "listagg":
            hits.append(
                PHit(
                    pid="P4",
                    node_ref=f"ast:listagg[{i}]",
                    tier=_tier_for("P4"),
                    why="LISTAGG present; bounded array_agg/slice is lossy DANGEROUS (T4→P4 advise-only).",
                    span=_span_of(call),
                )
            )
    # some dialects parse listagg as dedicated expr
    ListAgg = getattr(exp, "GroupConcat", None) or getattr(exp, "ListAgg", None)
    if ListAgg is not None:
        for i, node in enumerate(tree.find_all(ListAgg)):
            hits.append(
                PHit(
                    pid="P4",
                    node_ref=f"ast:group_concat[{i}]",
                    tier=_tier_for("P4"),
                    why="List/group concat aggregation; bounded slice is DANGEROUS advise-only (T4→P4).",
                    span=_span_of(node),
                )
            )
    return hits


def _cte_join_signature(cte_select: Any, exp: Any) -> frozenset[str]:
    """Normalized set of joined table names inside a CTE body (LEFT/INNER)."""
    names: set[str] = set()
    try:
        for join in cte_select.find_all(exp.Join):
            # table being joined
            tbl = join.this
            if isinstance(tbl, exp.Table) and tbl.name:
                names.add(str(tbl.name).lower())
            elif tbl is not None:
                for t in tbl.find_all(exp.Table):
                    if t.name:
                        names.add(str(t.name).lower())
                        break
    except Exception:
        return frozenset()
    return frozenset(names)


def _cte_is_simple_enrichment(cte_select: Any, exp: Any) -> bool:
    """True if CTE looks like project/CASE enrich without aggregation grain change."""
    try:
        if cte_select.args.get("group") is not None:
            return False
        if cte_select.args.get("distinct") is not None:
            return False
        # HAVING / QUALIFY-like
        if cte_select.args.get("having") is not None:
            return False
        # window is ok-ish but treat as non-simple for v1 safety
        if list(cte_select.find_all(exp.Window)):
            return False
    except Exception:
        return False
    return True


def _scan_p10(tree: Any, exp: Any) -> list[PHit]:
    """Detect consecutive CTEs that LEFT/JOIN the same dim set (T5→P10)."""
    hits: list[PHit] = []
    try:
        with_ = tree.args.get("with")
        if with_ is None:
            # root may be Select with with_
            if hasattr(tree, "find"):
                # collect CTE nodes in source order
                ctes = list(tree.find_all(exp.CTE))
            else:
                ctes = []
        else:
            ctes = list(with_.find_all(exp.CTE)) if hasattr(with_, "find_all") else list(with_.expressions or [])
        if len(ctes) < 2:
            return []
        # walk consecutive pairs/triples with same join signature
        sigs: list[tuple[str, frozenset[str], bool]] = []
        for cte in ctes:
            name = str(getattr(cte, "alias_or_name", None) or getattr(cte, "alias", None) or cte.alias or "").lower()
            if not name:
                try:
                    name = str(cte.alias_or_name).lower()
                except Exception:
                    name = f"cte{len(sigs)}"
            body = cte.this
            if body is None:
                continue
            sig = _cte_join_signature(body, exp)
            simple = _cte_is_simple_enrichment(body, exp)
            sigs.append((name, sig, simple))
        # find runs of ≥2 consecutive CTEs sharing non-empty identical join set + simple
        i = 0
        run_id = 0
        while i < len(sigs) - 1:
            name_a, sig_a, simple_a = sigs[i]
            if not sig_a or not simple_a:
                i += 1
                continue
            j = i + 1
            names = [name_a]
            while j < len(sigs):
                name_b, sig_b, simple_b = sigs[j]
                if sig_b == sig_a and simple_b and sig_b:
                    names.append(name_b)
                    j += 1
                else:
                    break
            if len(names) >= 2:
                hits.append(
                    PHit(
                        pid="P10",
                        node_ref=f"ast:cte_chain_same_dims[{run_id}]:{'>'.join(names)}",
                        tier=_tier_for("P10"),
                        why=(
                            f"Consecutive CTEs {', '.join(names)} repeatedly join the same "
                            f"dimension set {sorted(sig_a)}; merge into one enrichment CTE (T5→P10)."
                        ),
                        span=None,
                    )
                )
                run_id += 1
                i = j
            else:
                i += 1
    except Exception:
        return hits
    return hits


def scan_phits(sql: str) -> list[PHit]:
    """Scan SQL for catalog P-hits. Never raises; returns [] on parse failure."""
    if not sql or not str(sql).strip():
        return []
    try:
        import sqlglot
        import sqlglot.expressions as exp

        tree = sqlglot.parse_one(str(sql), read="trino")
        if tree is None:
            return []
    except Exception:
        return []

    hits: list[PHit] = []
    try:
        hits.extend(_scan_p1(tree, exp))
        hits.extend(_scan_p9(tree, exp))
        hits.extend(_scan_p3(tree, exp))
        hits.extend(_scan_p4(tree, exp))
        hits.extend(_scan_p10(tree, exp))
    except Exception:
        return hits

    # Dedupe stable by pid+node_ref
    seen: set[tuple[str, str]] = set()
    out: list[PHit] = []
    for h in hits:
        key = (h.pid, h.node_ref)
        if key in seen:
            continue
        seen.add(key)
        out.append(h)
    return out


def format_phits_markdown(hits: list[PHit]) -> str:
    if not hits:
        return "_No P-hits detected by engine SCAN._\n"
    lines = [
        "| pid | tier | node_ref | why |",
        "|-----|------|----------|-----|",
    ]
    for h in hits:
        why = h.why.replace("|", "\\|")
        lines.append(f"| {h.pid} | {h.tier} | `{h.node_ref}` | {why} |")
    return "\n".join(lines) + "\n"


def format_phits_direction_bullets(hits: list[PHit], *, limit: int = 5) -> str:
    """Compact bullets for optional directions appendix (not a CP essay)."""
    executeish = [h for h in hits if h.tier in {"safe", "trap"}][:limit]
    if not executeish:
        return ""
    lines = ["Engine P-hit shortlist (one change per iteration; catalog P-ids only):"]
    for h in executeish:
        lines.append(f"- {h.pid} ({h.tier}): {h.why}")
    return "\n".join(lines) + "\n"


__all__ = [
    "Span",
    "PHit",
    "scan_phits",
    "format_phits_markdown",
    "format_phits_direction_bullets",
]
