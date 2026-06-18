"""critical_path.py — Offline recursive structural cost model (v52).

Pure offline analyzer — zero EXPLAIN, zero query, no stats.
Builds a CostNode tree from the sqlglot AST, computes per-node cost via ordinal
magnitude propagation (variant C: derived-table depth, not raw subtree
accumulation), extracts the max-weight root→leaf critical path, and maps
the hottest node to a P-strategy.

Key sqlglot quirks (confirmed against pinned >=23.0 range):
  - FROM clause is args["from_"]  (trailing underscore — NOT "from")
  - JOINs are args["joins"]
  - WHERE is args["where"], GROUP BY is args["group"], ORDER BY is args["order"]
  - exp.Exists.this is a Select (NOT a Subquery)
  - WITH clause is args["with_"]  (trailing underscore)
  - Subqueries in FROM appear as exp.Subquery under from_.this
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import sqlglot
import sqlglot.expressions as exp

from .cost_weights import (
    AGGREGATE_REDUCER,
    CORRELATED_SUBQUERY_FACTOR,
    CROSS_JOIN_BLOWUP,
    DISTINCT_REDUCER,
    EQUI_JOIN_FACTOR,
    LIMIT_REDUCER,
    NODE_BASE_WEIGHT,
    NON_EQUI_FACTOR,
    node_penalty,
)
from genie.skills.trino_query.sql_static.rule_ids import (
    RULE_CARTESIAN_JOIN,
    RULE_JOIN_KEY_COMPUTED,
    RULE_JOIN_FIRST_FILTER_LATE,
    RULE_PREDICATE_NOT_PUSHED_TO_CTE,
    RULE_SELECT_STAR,
    RULE_UNNECESSARY_ORDER_BY_IN_SUBQUERY,
    RULE_SUBQUERY_IN_SELECT_PUSHABLE_TO_JOIN,
)


# ---------------------------------------------------------------------------
# §2 CostNode dataclass
# ---------------------------------------------------------------------------

@dataclass
class CostNode:
    """Single operation node in the cost tree.

    Immutable after construction except for row_magnitude and subtree_cost
    which are filled in place by compute_costs().
    """
    op: str               # e.g. "join_cross", "scan", "correlated_subquery", ...
    label: str            # human phrase — NEVER an AST repr or class name
    children: list["CostNode"] = field(default_factory=list)
    self_weight: float = 0.0       # from NODE_BASE_WEIGHT[op]
    rule_penalties: float = 0.0    # sum of node_penalty(...) for applicable findings
    row_magnitude: int = 1         # ordinal degree, filled by compute_costs
    subtree_cost: float = 0.0      # filled by compute_costs
    flags: set = field(default_factory=set)  # incl. "offline_truth_ceiling"
    strategy: Optional[str] = None          # P-strategy id or None
    sql_excerpt: str = ""


# ---------------------------------------------------------------------------
# §3.1 Join-classification helpers
# ---------------------------------------------------------------------------

def _collect_and_leaves(condition: exp.Expression) -> list[exp.Expression]:
    """Return the leaf predicates of an AND-chain (not recursive into sub-selects)."""
    if isinstance(condition, exp.And):
        return _collect_and_leaves(condition.left) + _collect_and_leaves(condition.right)
    return [condition]


def _is_literal_tautology(leaf: exp.Expression) -> bool:
    """True if leaf is a constant tautology — `1=1`, `'x'='x'`, or bare `TRUE`.

    §3.1 F3: in a JOIN ON clause a literal tautology is a cartesian signal — it
    contributes no row-reducing constraint. Rule-engine SQL commonly opens an ON
    with `1=1` so every real condition can be appended as `AND ...`; whether the
    join is actually cartesian depends on whether a real equi key sits among those
    ANDs (handled by the caller's has_equi_pair guard).
    """
    if isinstance(leaf, exp.Boolean):
        return bool(leaf.this) is True
    if isinstance(leaf, exp.EQ):
        left, right = leaf.left, leaf.right
        if isinstance(left, exp.Literal) and isinstance(right, exp.Literal):
            # Only an EQUAL literal pair is a tautology. `1=2` is a contradiction
            # (always-empty join) — NOT a cartesian signal; let it fall through to
            # normal classification rather than mislabel an empty join as heaviest.
            return (left.this == right.this) and (left.is_string == right.is_string)
    return False


def _classify_join(join: exp.Join) -> str:
    """Return the op kind for a JOIN node per §3.1.

    Returns one of: join_cross | join_inner | join_left | join_right |
                    join_full | join_non_equi
    """
    kind_arg = join.args.get("kind")
    kind_str = str(kind_arg).upper() if kind_arg else ""
    if kind_str == "CROSS":
        return "join_cross"

    on_clause = join.args.get("on")
    if not on_clause:
        # No ON/USING → cartesian
        return "join_cross"

    # Classify the ON clause predicates
    leaves = _collect_and_leaves(on_clause)
    non_equi_ops = (exp.LT, exp.GT, exp.LTE, exp.GTE, exp.NEQ, exp.Between)
    has_equi_pair = False
    has_non_equi = False
    has_tautology = False

    for leaf in leaves:
        if _is_literal_tautology(leaf):
            has_tautology = True
        elif isinstance(leaf, non_equi_ops):
            has_non_equi = True
        elif isinstance(leaf, exp.EQ):
            # An equality referencing a column on BOTH sides bounds the join — a
            # real join key — even when wrapped in a function (UPPER(a.k)=b.k,
            # COALESCE(a.x,a.y)=b.z). Only literal=literal (no columns, caught above
            # as a tautology) is non-bounding. This keeps a computed-key equi join
            # out of the cartesian branch (F3 no-false-positive).
            left, right = leaf.left, leaf.right
            left_has_col = isinstance(left, exp.Column) or bool(next(left.find_all(exp.Column), None))
            right_has_col = isinstance(right, exp.Column) or bool(next(right.find_all(exp.Column), None))
            if left_has_col and right_has_col:
                has_equi_pair = True

    # F3: a `1=1`/TRUE tautology with NO real cross-table equi key is a filtered
    # cross product — classify as cartesian (CROSS_JOIN_BLOWUP) regardless of any
    # fuzzy/non-equi predicates. A genuine equi key (has_equi_pair) means the join
    # is bounded → this branch is skipped and normal classification applies, so a
    # real equi join carrying an incidental `1=1`/LIKE filter is unaffected.
    if has_tautology and not has_equi_pair:
        return "join_cross"

    if has_non_equi:
        return "join_non_equi"

    # Determine side from join kind
    side_str = str(join.args.get("side", "")).upper()
    if side_str == "LEFT":
        return "join_left"
    if side_str == "RIGHT":
        return "join_right"
    if side_str == "FULL":
        return "join_full"
    return "join_inner"


def _has_computed_key(join: exp.Join) -> bool:
    """Return True if the ON clause contains a computed (function-applied) join key.

    Matches R10: UPPER(t.x) = t.y, CAST(...), date_trunc, etc.
    """
    on_clause = join.args.get("on")
    if not on_clause:
        return False
    for leaf in _collect_and_leaves(on_clause):
        if isinstance(leaf, exp.EQ):
            for side in (leaf.left, leaf.right):
                if isinstance(side, (exp.Anonymous, exp.Func)):
                    return True
    return False


# ---------------------------------------------------------------------------
# §3 Table alias collection + §3.2 correlation detection
# ---------------------------------------------------------------------------

def _collect_table_aliases(select_node: exp.Select) -> set[str]:
    """Collect all table/subquery aliases visible at this SELECT's FROM+JOINs."""
    aliases: set[str] = set()

    from_clause = select_node.args.get("from_")
    if from_clause:
        primary = from_clause.this
        if isinstance(primary, exp.Table):
            name = primary.alias if primary.alias else primary.name
            if name:
                aliases.add(name)
        elif isinstance(primary, exp.Subquery):
            if primary.alias:
                aliases.add(primary.alias)

    for join in (select_node.args.get("joins") or []):
        for tbl in join.find_all(exp.Table):
            name = tbl.alias if tbl.alias else tbl.name
            if name:
                aliases.add(name)
        for subq in join.find_all(exp.Subquery):
            if subq.alias:
                aliases.add(subq.alias)

    return aliases


def _is_correlated(inner_select: exp.Select, outer_aliases: set[str]) -> bool:
    """Return True if inner_select references any alias from outer_aliases.

    §3.2 conservative default: an unqualified `aa=bb` where we cannot prove
    the table is inner-only is treated as correlated.
    """
    inner_aliases = _collect_table_aliases(inner_select)

    for col in inner_select.find_all(exp.Column):
        tbl_ref = col.args.get("table")
        if tbl_ref:
            # Qualified column — check if qualifier is an outer alias
            tbl_name = tbl_ref.name if hasattr(tbl_ref, "name") else str(tbl_ref)
            if tbl_name and tbl_name in outer_aliases and tbl_name not in inner_aliases:
                return True
        # Unqualified column inside a WHERE EXISTS: conservative = correlated
        # (cannot prove it's not referencing the outer scope offline)

    return False


# ---------------------------------------------------------------------------
# §2.1 Label population helpers
# ---------------------------------------------------------------------------

def _table_name(tbl: exp.Table) -> str:
    """Return table alias or name as a string (not the Identifier object)."""
    return (tbl.alias or tbl.name or "?")


def _join_label(op: str, join: exp.Join, left_name: Optional[str] = None) -> str:
    """Build a human join label like 'JOIN facts⋈dims' per §2.1.

    F4: the right table is the join's OWN directly-attached source (`join.this`).
    Never `find_all(exp.Table)` — that descends into ON-clause subqueries and would
    name a validation table (e.g. the EXISTS target) instead of the joined table.
    `left_name` is the FROM-side table, threaded in by the caller (the join node
    itself does not know its left input).
    """
    kind_map = {
        "join_cross":    "CROSS JOIN",
        "join_inner":    "JOIN",
        "join_left":     "LEFT JOIN",
        "join_right":    "RIGHT JOIN",
        "join_full":     "FULL JOIN",
        "join_non_equi": "NON-EQUI JOIN",
    }
    kind_str = kind_map.get(op, "JOIN")

    right = join.this
    if isinstance(right, exp.Table):
        right_name = _table_name(right)
    elif isinstance(right, exp.Subquery):
        right_name = right.alias or "subquery"
    else:
        right_name = ""

    if left_name and right_name:
        return f"{kind_str} {left_name}⋈{right_name}"
    if right_name:
        return f"{kind_str} ⋈{right_name}"
    return kind_str


def _make_correlated_node(
    inner_sel: exp.Select,
    all_visible: set[str],
    parent_magnitude: float,
    depth: int,
    kind: str,
) -> "CostNode":
    """Build a correlated_subquery CostNode (with its recursed inner subtree).

    `kind` ∈ {EXISTS, IN, CORRELATED} controls only the human label/excerpt; the
    cost treatment (CORRELATED_SUBQUERY_FACTOR, truth-ceiling flag, P2 strategy) is
    identical — a correlated subquery runs once per parent row regardless of syntax.
    """
    sub_magnitude = parent_magnitude * CORRELATED_SUBQUERY_FACTOR
    inner_tables = list(inner_sel.find_all(exp.Table))
    inner_name = inner_tables[0].name if inner_tables else "subquery"
    label = {"IN": f"IN ({inner_name})"}.get(kind, f"EXISTS {inner_name}")
    excerpt = {
        "EXISTS": f"EXISTS ({inner_name})",
        "IN": f"IN ({inner_name})",
        "CORRELATED": f"CORRELATED ({inner_name})",
    }.get(kind, f"EXISTS ({inner_name})")
    sub_node = CostNode(
        op="correlated_subquery",
        label=label,
        self_weight=NODE_BASE_WEIGHT["correlated_subquery"],
        rule_penalties=node_penalty(RULE_SUBQUERY_IN_SELECT_PUSHABLE_TO_JOIN, "high"),
        row_magnitude=int(sub_magnitude),
        flags={"offline_truth_ceiling"},
        strategy="P2",  # exists-to-left-join (TRAP)
        sql_excerpt=excerpt,
    )
    inner_node = _analyze_select(
        inner_sel,
        parent_magnitude=sub_magnitude,
        outer_aliases=all_visible,
        depth=depth + 1,
    )
    sub_node.children.append(inner_node)
    return sub_node


def _scan_correlated_subqueries(
    scope: exp.Expression,
    all_visible: set[str],
    parent_magnitude: float,
    depth: int,
) -> list["CostNode"]:
    """Return correlated_subquery nodes for all correlated EXISTS/IN/scalar
    subqueries anywhere inside `scope` (§3.2).

    F1/F2: `scope` may be a WHERE/HAVING clause, a JOIN ON clause (subqueries
    nested in a CASE WHEN are reached via find_all), or a SELECT projection
    expression. Only correlated subqueries become blowup nodes; uncorrelated ones
    are left as ordinary (zero-extra-cost) structure.
    """
    nodes: list[CostNode] = []

    # EXISTS(...) — exp.Exists.this is a Select (not a Subquery)
    for exists_expr in scope.find_all(exp.Exists):
        inner_sel = exists_expr.this
        if isinstance(inner_sel, exp.Select) and _is_correlated(inner_sel, all_visible):
            nodes.append(_make_correlated_node(inner_sel, all_visible, parent_magnitude, depth, "EXISTS"))

    # IN (SELECT ...) — subquery lives under args["query"]
    for in_expr in scope.find_all(exp.In):
        query_arg = in_expr.args.get("query")
        if not query_arg:
            continue
        inner_sel = query_arg if isinstance(query_arg, exp.Select) else (
            query_arg.this if isinstance(query_arg, exp.Subquery) else None
        )
        if isinstance(inner_sel, exp.Select) and _is_correlated(inner_sel, all_visible):
            nodes.append(_make_correlated_node(inner_sel, all_visible, parent_magnitude, depth, "IN"))

    # Bare scalar subqueries NOT wrapped in EXISTS/IN
    for subq in scope.find_all(exp.Subquery):
        if subq.find_ancestor(exp.Exists) or subq.find_ancestor(exp.In):
            continue
        inner_sel = subq.this
        if isinstance(inner_sel, exp.Select) and _is_correlated(inner_sel, all_visible):
            nodes.append(_make_correlated_node(inner_sel, all_visible, parent_magnitude, depth, "CORRELATED"))

    return nodes


def _from_table_label(tbl: exp.Table) -> str:
    return f"SCAN {_table_name(tbl)}"


# ---------------------------------------------------------------------------
# §3 AST → CostNode tree builder
# ---------------------------------------------------------------------------

def _analyze_select(
    select_node: exp.Select,
    parent_magnitude: float,
    outer_aliases: set[str],
    depth: int,
) -> CostNode:
    """Recursively build a CostNode subtree for a SELECT node."""
    children: list[CostNode] = []

    current_aliases = _collect_table_aliases(select_node)
    all_visible = outer_aliases | current_aliases

    from_clause = select_node.args.get("from_")
    joins = select_node.args.get("joins") or []

    # ------------------------------------------------------------------
    # Compute derived-table magnitude (§4 variant C):
    # Children of this SELECT inherit a magnitude already boosted by
    # the max blowup factor among all joins at THIS level before recursing.
    # This ensures a deeper join always strictly outranks a shallower one.
    # ------------------------------------------------------------------
    derived_magnitude = parent_magnitude
    if joins:
        max_factor = 1.0
        for j in joins:
            op = _classify_join(j)
            if op == "join_cross":
                max_factor = max(max_factor, CROSS_JOIN_BLOWUP)
            elif op == "join_non_equi":
                max_factor = max(max_factor, NON_EQUI_FACTOR)
            else:
                max_factor = max(max_factor, EQUI_JOIN_FACTOR)
        derived_magnitude = parent_magnitude * max_factor

    # ------------------------------------------------------------------
    # 1. FROM source
    # ------------------------------------------------------------------
    if from_clause:
        primary = from_clause.this
        if isinstance(primary, exp.Table):
            scan_label = _from_table_label(primary)
            # R2: SELECT * + joins → penalty
            r2_penalty = 0.0
            if joins and any(isinstance(e, exp.Star) for e in select_node.expressions):
                r2_penalty = node_penalty(RULE_SELECT_STAR, "medium")
            scan = CostNode(
                op="scan",
                label=scan_label,
                self_weight=NODE_BASE_WEIGHT["scan"],
                rule_penalties=r2_penalty,
                row_magnitude=int(parent_magnitude),
                sql_excerpt=scan_label,
            )
            children.append(scan)

        elif isinstance(primary, exp.Subquery):
            # Derived table subquery — recurse with derived_magnitude
            inner_sel = primary.this
            if isinstance(inner_sel, exp.Select):
                inner_node = _analyze_select(
                    inner_sel,
                    parent_magnitude=derived_magnitude,
                    outer_aliases=all_visible,
                    depth=depth + 1,
                )
                # R2 penalty: SELECT * inside a derived table feeding joins
                if joins and any(isinstance(e, exp.Star) for e in inner_sel.expressions):
                    inner_node.rule_penalties += node_penalty(RULE_SELECT_STAR, "medium")
                children.append(inner_node)

    # ------------------------------------------------------------------
    # 2. JOINs at this SELECT level
    # ------------------------------------------------------------------
    # F4: left-side table name for join labels — the join node itself only knows
    # its right input, so thread the FROM primary in from here.
    running_left: Optional[str] = None
    if from_clause and isinstance(from_clause.this, exp.Table):
        running_left = _table_name(from_clause.this)

    for join in joins:
        op = _classify_join(join)

        if op == "join_cross":
            j_magnitude = parent_magnitude * CROSS_JOIN_BLOWUP
            j_flags = {"offline_truth_ceiling"}
            j_penalty = node_penalty(RULE_CARTESIAN_JOIN, "high")
            j_strategy: Optional[str] = None  # GAP-2: BLOCK, advisory only
        elif op == "join_non_equi":
            j_magnitude = parent_magnitude * NON_EQUI_FACTOR
            j_flags: set = set()
            j_penalty = 0.0
            j_strategy = "P5"  # predicate-partition-pushdown (TRAP)
        else:
            j_magnitude = parent_magnitude * EQUI_JOIN_FACTOR
            j_flags = set()
            j_penalty = 0.0
            j_strategy = None

        # R10: computed join key penalty
        if _has_computed_key(join):
            j_penalty += node_penalty(RULE_JOIN_KEY_COMPUTED, "medium")
            if j_strategy is None:
                j_strategy = "P1"  # function-pushup (SAFE)

        label = _join_label(op, join, left_name=running_left)
        # Advance the running left for the NEXT join's label: a chained join's left
        # input is the prior join's right table, not the original FROM primary.
        _rt = join.this
        if isinstance(_rt, exp.Table):
            running_left = _table_name(_rt)
        elif isinstance(_rt, exp.Subquery) and _rt.alias:
            running_left = _rt.alias

        join_node = CostNode(
            op=op,
            label=label,
            self_weight=NODE_BASE_WEIGHT.get(op, 5.0),
            rule_penalties=j_penalty,
            row_magnitude=int(j_magnitude),
            flags=j_flags,
            strategy=j_strategy,
            sql_excerpt=label,
        )

        # F1/F2: correlated EXISTS/IN/scalar subqueries living in the JOIN ON clause
        # (commonly nested in a CASE WHEN) run once per joined row — attach them as
        # CHILDREN of this join node so they sit on the join's subtree (magnitude
        # propagates from the join's blowup, not the pre-join parent magnitude).
        on_clause = join.args.get("on")
        if on_clause is not None:
            for sub_node in _scan_correlated_subqueries(
                on_clause, all_visible, j_magnitude, depth
            ):
                join_node.children.append(sub_node)

        children.append(join_node)

    # ------------------------------------------------------------------
    # 3. Correlated subqueries in WHERE/HAVING (§3.2)
    # A WHERE-clause correlated subquery runs once per POST-join row, so it must
    # inherit the post-join derived_magnitude (the join blowup), not the pre-join
    # parent_magnitude — otherwise a per-row EXISTS validation downstream of a big
    # join is undercounted and never reaches the critical path. HAVING runs after
    # GROUP BY (reduced cardinality) → keep the unboosted parent_magnitude.
    for clause_key, clause_magnitude in (("where", derived_magnitude), ("having", parent_magnitude)):
        clause = select_node.args.get(clause_key)
        if not clause:
            continue
        for sub_node in _scan_correlated_subqueries(
            clause, all_visible, clause_magnitude, depth
        ):
            children.append(sub_node)

    # ------------------------------------------------------------------
    # 3b. Correlated scalar subqueries in the SELECT projection list (§3.2 F1)
    # e.g. SELECT (SELECT max(x) FROM t WHERE t.id = outer.id) ...
    # Evaluated per output row of FROM+joins → post-join derived_magnitude.
    # ------------------------------------------------------------------
    for proj in select_node.expressions:
        for sub_node in _scan_correlated_subqueries(
            proj, all_visible, derived_magnitude, depth
        ):
            children.append(sub_node)

    # ------------------------------------------------------------------
    # 4. Window functions
    # ------------------------------------------------------------------
    windows = list(select_node.find_all(exp.Window))
    if windows:
        fn_name = ""
        if isinstance(windows[0].this, exp.Anonymous):
            fn_name = windows[0].this.name or ""
        elif isinstance(windows[0].this, exp.Func):
            fn_name = type(windows[0].this).__name__
        win_label = f"WINDOW {fn_name}" if fn_name else "WINDOW"
        win_node = CostNode(
            op="window",
            label=win_label,
            self_weight=NODE_BASE_WEIGHT["window"],
            rule_penalties=0.0,
            row_magnitude=int(parent_magnitude),
            sql_excerpt=win_label,
        )
        children.append(win_node)

    # ------------------------------------------------------------------
    # 5. Aggregate (GROUP BY)
    # §4 reducer: aggregate outputs fewer rows than it reads — multiply the
    # post-join derived_magnitude by AGGREGATE_REDUCER so the node's own cost
    # reflects its reduced output cardinality. min(1) prevents zero magnitude.
    # ------------------------------------------------------------------
    if select_node.args.get("group"):
        group = select_node.args["group"]
        keys = []
        for expr in (group.expressions if hasattr(group, "expressions") else []):
            if isinstance(expr, exp.Column):
                keys.append(expr.name or str(expr))
            else:
                keys.append(str(expr)[:20])
        key_str = ", ".join(keys[:3]) if keys else ""
        agg_label = f"GROUP BY {key_str}" if key_str else "GROUP BY"
        agg_magnitude = max(1, int(derived_magnitude * AGGREGATE_REDUCER))
        agg_node = CostNode(
            op="aggregate",
            label=agg_label,
            self_weight=NODE_BASE_WEIGHT["aggregate"],
            rule_penalties=0.0,
            row_magnitude=agg_magnitude,
            sql_excerpt=agg_label,
        )
        children.append(agg_node)

    # ------------------------------------------------------------------
    # 6. Sort (ORDER BY) — §6 GAP-2: advisory only, strategy=None
    # ------------------------------------------------------------------
    if select_node.args.get("order"):
        has_limit = select_node.args.get("limit") is not None
        sort_penalty = 0.0
        sort_label = "ORDER BY"
        if depth > 0 and not has_limit:
            # R4: unnecessary ORDER BY in subquery
            sort_penalty = node_penalty(RULE_UNNECESSARY_ORDER_BY_IN_SUBQUERY, "medium")
            sort_label = "ORDER BY (in subquery)"
        sort_node = CostNode(
            op="sort",
            label=sort_label,
            self_weight=NODE_BASE_WEIGHT["sort"],
            rule_penalties=sort_penalty,
            row_magnitude=int(parent_magnitude),
            strategy=None,  # §6 GAP-2: no sort-elimination strategy in v52
            sql_excerpt=sort_label,
        )
        children.append(sort_node)

    # ------------------------------------------------------------------
    # 7. DISTINCT
    # §4 reducer: DISTINCT reduces output cardinality — use DISTINCT_REDUCER
    # on derived_magnitude (post-join blowup), same rationale as aggregate.
    # ------------------------------------------------------------------
    if select_node.args.get("distinct"):
        dist_magnitude = max(1, int(derived_magnitude * DISTINCT_REDUCER))
        dist_node = CostNode(
            op="distinct",
            label="DISTINCT",
            self_weight=NODE_BASE_WEIGHT["distinct"],
            rule_penalties=0.0,
            row_magnitude=dist_magnitude,
            sql_excerpt="DISTINCT",
        )
        children.append(dist_node)

    # ------------------------------------------------------------------
    # 7b. LIMIT
    # §4 reducer: LIMIT drastically reduces output rows — use LIMIT_REDUCER
    # on derived_magnitude. Only modelled as a cost node when LIMIT is present
    # without a join (with a join the blowup dominates).
    # ------------------------------------------------------------------
    if select_node.args.get("limit") is not None:
        limit_magnitude = max(1, int(derived_magnitude * LIMIT_REDUCER))
        limit_label = "LIMIT"
        limit_node = CostNode(
            op="limit",
            label=limit_label,
            self_weight=NODE_BASE_WEIGHT.get("limit", 1.0),
            rule_penalties=0.0,
            row_magnitude=limit_magnitude,
            sql_excerpt=limit_label,
        )
        children.append(limit_node)

    # ------------------------------------------------------------------
    # 8. CTEs (WITH clause) — §3.3
    # ------------------------------------------------------------------
    cte_clause = select_node.args.get("with_")
    if cte_clause:
        for cte in (cte_clause.expressions or []):
            alias_name = cte.alias if cte.alias else "?"
            cte_node = CostNode(
                op="cte",
                label=f"CTE {alias_name}",
                self_weight=NODE_BASE_WEIGHT["cte"],
                rule_penalties=0.0,
                row_magnitude=int(parent_magnitude),
                sql_excerpt=f"CTE {alias_name}",
            )
            inner_sel = cte.this
            if isinstance(inner_sel, exp.Select):
                inner_node = _analyze_select(
                    inner_sel,
                    parent_magnitude=parent_magnitude,
                    outer_aliases=all_visible | {alias_name},
                    depth=depth + 1,
                )
                cte_node.children.append(inner_node)
            children.append(cte_node)

    # ------------------------------------------------------------------
    # Build this SELECT's root node (project / query)
    # ------------------------------------------------------------------
    if depth == 0:
        op = "root"
        label = "query"
    else:
        op = "project"
        label = "SELECT"

    root_node = CostNode(
        op=op,
        label=label,
        children=children,
        self_weight=NODE_BASE_WEIGHT.get(op, 1.0),
        rule_penalties=0.0,
        row_magnitude=int(parent_magnitude),
        sql_excerpt=label,
    )
    return root_node


def build_cost_tree(sql: str) -> CostNode:
    """Parse SQL with sqlglot (trino dialect) and build a CostNode tree.

    Returns the root CostNode (op="root"). Raises on parse failure.
    compute_costs() must be called separately to fill row_magnitude + subtree_cost.
    """
    tree = sqlglot.parse_one(sql, dialect="trino")
    if not isinstance(tree, exp.Select):
        # Handle WITH ... SELECT top-level
        if isinstance(tree, exp.Select):
            pass
        elif hasattr(tree, "this") and isinstance(tree.this, exp.Select):
            tree = tree.this
        else:
            raise ValueError(f"Expected SELECT statement, got {type(tree).__name__}")
    return _analyze_select(tree, parent_magnitude=1.0, outer_aliases=set(), depth=0)


# ---------------------------------------------------------------------------
# §4 Ordinal recursive cost algebra (variant C — derived-table magnitude)
# ---------------------------------------------------------------------------

def compute_costs(node: CostNode) -> None:
    """Fill row_magnitude and subtree_cost recursively in place.

    §4 DERIVED-TABLE magnitude propagation (variant C):
    A child inherits the parent's magnitude already multiplied by blowup
    factors at the parent level — this is done in build_cost_tree (before
    recursing into derived tables). compute_costs walks the completed tree
    and fills subtree_cost bottom-up.

    subtree_cost(node) = self_weight(node) × row_magnitude(node)
                       + Σ subtree_cost(children)
                       + rule_penalties(node)
    """
    for child in node.children:
        compute_costs(child)

    children_cost = sum(c.subtree_cost for c in node.children)
    node.subtree_cost = (
        node.self_weight * node.row_magnitude
        + children_cost
        + node.rule_penalties
    )


# ---------------------------------------------------------------------------
# §4 + §8c-3 Critical path extraction
# ---------------------------------------------------------------------------

def _preorder_index(root: CostNode) -> dict[int, int]:
    """Return a dict mapping id(node) → pre-order visit index for tie-breaking."""
    index: dict[int, int] = {}
    counter = [0]

    def _visit(n: CostNode) -> None:
        index[id(n)] = counter[0]
        counter[0] += 1
        for c in n.children:
            _visit(c)

    _visit(root)
    return index


def extract_critical_path(root: CostNode) -> list[CostNode]:
    """Return the max-weight root→leaf path (list from root to bottleneck leaf).

    §8c-3 deterministic tie-break: among equally-weighted leaves, pick the
    one with the smallest pre-order index (stable, depth-first left-to-right).

    compute_costs() must be called before this function.
    """
    preorder = _preorder_index(root)

    def _max_path(node: CostNode) -> tuple[float, list[CostNode]]:
        if not node.children:
            return node.subtree_cost, [node]
        # Find best child path (max cost, tie-break by pre-order index)
        best_cost: float = -1.0
        best_path: list[CostNode] = []
        for child in node.children:
            child_cost, child_path = _max_path(child)
            total = node.subtree_cost + child_cost
            if (total > best_cost) or (
                total == best_cost and preorder.get(id(child), 0) < preorder.get(id(best_path[0] if best_path else child), 0)
            ):
                best_cost = total
                best_path = child_path
        return best_cost, [node] + best_path

    _, path = _max_path(root)
    return path


def _count_max_weight_leaves(root: CostNode) -> int:
    """Count how many leaves share the maximum subtree_cost (for tie reporting)."""
    all_leaves: list[float] = []

    def _collect_leaves(n: CostNode) -> None:
        if not n.children:
            all_leaves.append(n.subtree_cost)
        for c in n.children:
            _collect_leaves(c)

    _collect_leaves(root)
    if not all_leaves:
        return 1
    max_cost = max(all_leaves)
    return sum(1 for c in all_leaves if c == max_cost)


# ---------------------------------------------------------------------------
# §1 CriticalPathResult + analyze_critical_path entry point
# ---------------------------------------------------------------------------

@dataclass
class CriticalPathResult:
    """Result from analyze_critical_path. Never raises to caller."""
    available: bool
    reason: str = ""                      # populated when available=False
    root: Optional[CostNode] = None
    critical_path: list[CostNode] = field(default_factory=list)
    bottleneck: Optional[CostNode] = None  # hottest leaf in the path
    has_blowup: bool = False              # True if any blowup node in tree
    tied_paths: int = 1                   # §8c-3: count of equally-hot leaves
    trivial: bool = False                 # True when no structural bottleneck
    offline_truth_ceiling: bool = False   # True when any path node has the flag


def _has_any_blowup(root: CostNode) -> bool:
    """Return True if any node in the tree is a structural blowup op."""
    blowup_ops = {
        "join_cross", "join_non_equi", "correlated_subquery",
        "join_inner", "join_left", "join_right", "join_full",
        "window",
    }
    if root.op in blowup_ops:
        return True
    return any(_has_any_blowup(c) for c in root.children)


def _strategy_for_node(node: CostNode) -> Optional[str]:
    """Return the P-strategy id for a bottleneck node per §6."""
    # If already mapped during tree build, use it
    if node.strategy is not None:
        return node.strategy

    # Map by op kind
    op_strategy: dict[str, Optional[str]] = {
        "join_cross":          None,   # BLOCK — advisory only
        "join_non_equi":       "P5",   # predicate-partition-pushdown (TRAP)
        "correlated_subquery": "P2",   # exists-to-left-join (TRAP)
        "join_inner":          None,
        "join_left":           None,
        "join_right":          None,
        "join_full":           None,
        "sort":                None,   # §6 GAP-2: no strategy
        "window":              None,
        "aggregate":           None,
        "scan":                None,
        "filter":              None,
        "project":             None,
        "distinct":            None,
        "limit":               None,
        "setop":               None,
        "cte":                 None,
        "root":                None,
    }
    return op_strategy.get(node.op)


def analyze_critical_path(sql: str) -> CriticalPathResult:
    """Orchestrate build_cost_tree + compute_costs + extract_critical_path.

    §1 contract: NEVER raises. Parse errors / unsupported shapes return
    CriticalPathResult(available=False, reason=...).
    """
    try:
        root = build_cost_tree(sql)
    except Exception as exc:
        return CriticalPathResult(
            available=False,
            reason=f"parse error: {exc}",
        )

    try:
        compute_costs(root)
        path = extract_critical_path(root)

        # §8c-2 trivial check: no structural blowup node in tree
        has_blowup = _has_any_blowup(root)
        if not has_blowup:
            return CriticalPathResult(
                available=True,
                root=root,
                critical_path=path,
                bottleneck=None,
                has_blowup=False,
                tied_paths=1,
                trivial=True,
                offline_truth_ceiling=False,
            )

        # Bottleneck = node in the path with the highest self contribution
        # (self_weight × row_magnitude + rule_penalties). This picks the actual
        # blowup node (e.g. correlated_subquery) rather than the deepest scan leaf.
        def _self_contribution(n: CostNode) -> float:
            return n.self_weight * n.row_magnitude + n.rule_penalties

        blowup_in_path = [n for n in path if n.op in {
            "join_cross", "join_non_equi", "correlated_subquery",
            "join_inner", "join_left", "join_right", "join_full",
            "window", "aggregate", "sort",
        }]
        if blowup_in_path:
            bottleneck = max(blowup_in_path, key=_self_contribution)
        else:
            bottleneck = max(path, key=_self_contribution) if path else None

        # §8c-3 tied paths count
        tied_count = _count_max_weight_leaves(root)

        # §8c-4 truth-ceiling: any node in the path has the flag
        has_ceiling = any("offline_truth_ceiling" in n.flags for n in path)

        # Map strategy for bottleneck
        if bottleneck is not None and bottleneck.strategy is None:
            bottleneck.strategy = _strategy_for_node(bottleneck)

        return CriticalPathResult(
            available=True,
            root=root,
            critical_path=path,
            bottleneck=bottleneck,
            has_blowup=has_blowup,
            tied_paths=tied_count,
            trivial=False,
            offline_truth_ceiling=has_ceiling,
        )

    except Exception as exc:
        return CriticalPathResult(
            available=False,
            reason=f"cost model error: {exc}",
        )


# ---------------------------------------------------------------------------
# Utility: all nodes flat-sorted by subtree_cost (for tests / reporting)
# ---------------------------------------------------------------------------

def flatten_nodes(root: CostNode, exclude_wrappers: bool = True) -> list[CostNode]:
    """Flatten to list sorted by subtree_cost descending.

    When exclude_wrappers=True, nodes with op in {root, project, cte} are
    excluded so rankings focus on meaningful operation nodes.
    """
    wrapper_ops = {"root", "project", "cte"}
    nodes: list[CostNode] = []

    def _collect(n: CostNode) -> None:
        if not (exclude_wrappers and n.op in wrapper_ops):
            nodes.append(n)
        for c in n.children:
            _collect(c)

    _collect(root)
    return sorted(nodes, key=lambda n: n.subtree_cost, reverse=True)


__all__ = [
    "CostNode",
    "CriticalPathResult",
    "build_cost_tree",
    "compute_costs",
    "extract_critical_path",
    "analyze_critical_path",
    "flatten_nodes",
]
