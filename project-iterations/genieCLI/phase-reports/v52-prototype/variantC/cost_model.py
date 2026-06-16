# PROTOTYPE - DO NOT MERGE; WRAP MUST ARCHIVE OR DELETE
"""
v52 variantC — Fresh AST CostNode tree with recursive magnitude propagation.
Builds a hierarchical cost tree from sqlglot AST for Trino SQL queries.

Key sqlglot v30.4.2 notes (discovered during prototype):
- FROM clause is args["from_"] (underscore), not args["from"]
- JOINs are args["joins"] (list)
- WHERE is args["where"], GROUP BY is args["group"], ORDER BY is args["order"]
- exp.Exists.this is a Select (not a Subquery)
- Subqueries in FROM appear under args["from_"].find_all(exp.Subquery)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import sqlglot
import sqlglot.expressions as exp


# ---------------------------------------------------------------------------
# Weight configurations
# ---------------------------------------------------------------------------

WEIGHT_CONFIGS = {
    "baseline": {
        "CROSS_JOIN_BLOWUP": 100,
        "EQUI_JOIN_FACTOR": 5,
        "NON_EQUI_FACTOR": 30,
        "SUBQUERY_FACTOR": 3,
        "AGGREGATE_FACTOR": 2,
        "WINDOW_FACTOR": 10,
        "LIMIT_FACTOR": 0.1,
        "self_weights": {
            "cross_join": 100,
            "non_equi_join": 30,
            "equi_join": 5,
            "correlated_subquery": 50,
            "window": 10,
            "aggregate": 2,
            "sort": 5,
            "scan": 1,
            "filter": 1,
            "project": 1,
            "subquery": 3,
        },
        "rule_penalties": {
            "R1_correlated": 50,
            "R2_select_star_under_join": 15,
            "R3_non_equi": 20,
            "R4_order_by_no_limit_in_subquery": 10,
        },
    },
    "perturbed": {  # all weights ×1.3
        "CROSS_JOIN_BLOWUP": 130,
        "EQUI_JOIN_FACTOR": 6.5,
        "NON_EQUI_FACTOR": 39,
        "SUBQUERY_FACTOR": 3.9,
        "AGGREGATE_FACTOR": 2.6,
        "WINDOW_FACTOR": 13,
        "LIMIT_FACTOR": 0.13,
        "self_weights": {
            "cross_join": 130,
            "non_equi_join": 39,
            "equi_join": 6.5,
            "correlated_subquery": 65,
            "window": 13,
            "aggregate": 2.6,
            "sort": 6.5,
            "scan": 1.3,
            "filter": 1.3,
            "project": 1.3,
            "subquery": 3.9,
        },
        "rule_penalties": {
            "R1_correlated": 65,
            "R2_select_star_under_join": 19.5,
            "R3_non_equi": 26,
            "R4_order_by_no_limit_in_subquery": 13,
        },
    },
    "compressed": {  # smaller spread
        "CROSS_JOIN_BLOWUP": 20,
        "EQUI_JOIN_FACTOR": 3,
        "NON_EQUI_FACTOR": 10,
        "SUBQUERY_FACTOR": 2,
        "AGGREGATE_FACTOR": 1.5,
        "WINDOW_FACTOR": 5,
        "LIMIT_FACTOR": 0.2,
        "self_weights": {
            "cross_join": 20,
            "non_equi_join": 10,
            "equi_join": 3,
            "correlated_subquery": 15,
            "window": 5,
            "aggregate": 1.5,
            "sort": 3,
            "scan": 1,
            "filter": 1,
            "project": 1,
            "subquery": 2,
        },
        "rule_penalties": {
            "R1_correlated": 15,
            "R2_select_star_under_join": 5,
            "R3_non_equi": 8,
            "R4_order_by_no_limit_in_subquery": 4,
        },
    },
}


# ---------------------------------------------------------------------------
# CostNode dataclass
# ---------------------------------------------------------------------------

@dataclass
class CostNode:
    node_type: str          # "cross_join", "equi_join", "non_equi_join", "correlated_subquery", etc.
    label: str              # human readable
    magnitude: float        # accumulated multiplicative magnitude at this node
    self_weight: float      # base weight from node_type
    rule_penalties: float   # additive rule penalties
    subtree_cost: float     # self_weight * magnitude + sum(children.subtree_cost) + rule_penalties
    critical_path_pct: float  # node.subtree_cost / root.subtree_cost * 100
    children: list = field(default_factory=list)
    flags: list = field(default_factory=list)


# ---------------------------------------------------------------------------
# Helpers: AST interrogation
# ---------------------------------------------------------------------------

def _collect_table_aliases(select_node: exp.Select) -> set[str]:
    """Collect table/subquery aliases visible at this SELECT's FROM+JOINs."""
    aliases: set[str] = set()
    from_clause = select_node.args.get("from_")
    if from_clause:
        for tbl in from_clause.find_all(exp.Table):
            name = tbl.alias if tbl.alias else tbl.name
            if name:
                aliases.add(name)
        for subq in from_clause.find_all(exp.Subquery):
            if subq.alias:
                aliases.add(subq.alias)

    for join in (select_node.args.get("joins") or []):
        for tbl in join.find_all(exp.Table):
            name = tbl.alias if tbl.alias else tbl.name
            if name:
                aliases.add(name)
        for subq in join.find_all(exp.Subquery):
            if subq.alias:
                aliases.add(subq.alias)

    return aliases


def _is_select_correlated(inner_select: exp.Select, outer_aliases: set[str]) -> bool:
    """Return True if any Column in inner_select references an outer alias."""
    # Aliases defined INSIDE this select — not correlation
    inner_aliases = _collect_table_aliases(inner_select)

    for col in inner_select.find_all(exp.Column):
        tbl_ref = col.args.get("table")
        if tbl_ref:
            tbl_name = tbl_ref.name if hasattr(tbl_ref, "name") else str(tbl_ref)
            if tbl_name in outer_aliases and tbl_name not in inner_aliases:
                return True
    return False


def _join_condition_type(join_node: exp.Join) -> str:
    """Classify a JOIN's ON condition: 'cross_join', 'non_equi_join', 'equi_join'."""
    kind = join_node.args.get("kind")
    if kind and str(kind).upper() == "CROSS":
        return "cross_join"

    on_clause = join_node.args.get("on")
    if not on_clause:
        return "cross_join"  # no ON = cartesian

    non_equi_types = (exp.LT, exp.GT, exp.LTE, exp.GTE, exp.Between, exp.NEQ)
    for cls in non_equi_types:
        if on_clause.find(cls):
            return "non_equi_join"

    return "equi_join"


def _has_select_star(select_node: exp.Select) -> bool:
    for expr in select_node.expressions:
        if isinstance(expr, exp.Star):
            return True
    return False


# ---------------------------------------------------------------------------
# Core recursive builder
# ---------------------------------------------------------------------------

def build_cost_tree(sql: str, config: dict, dialect: str = "trino") -> CostNode:
    """Parse SQL and build a CostNode tree with recursive magnitude propagation."""
    tree = sqlglot.parse_one(sql, dialect=dialect)
    if not isinstance(tree, exp.Select):
        raise ValueError(f"Expected SELECT, got {type(tree).__name__}")

    root = _analyze_select(tree, config, parent_magnitude=1.0, outer_aliases=set(), depth=0)
    _set_critical_path_pct(root, root.subtree_cost)
    return root


def _get_direct_from_source(from_clause: exp.From):
    """
    Return (tables, subqueries) that are DIRECT children of this FROM node.
    Uses .this to get the primary source rather than find_all which recurses deeply.
    """
    if not from_clause:
        return [], []
    primary = from_clause.this
    tables = []
    subqs = []
    if isinstance(primary, exp.Table):
        tables.append(primary)
    elif isinstance(primary, exp.Subquery):
        subqs.append(primary)
    return tables, subqs


def _analyze_select(
    select_node: exp.Select,
    config: dict,
    parent_magnitude: float,
    outer_aliases: set[str],
    depth: int,
) -> CostNode:
    """
    Build a CostNode for this SELECT.
    Children list contains one node per 'interesting' operation:
    joins, derived-table subqueries, correlated/scalar subqueries,
    window functions, aggregate, sort, scans.
    The SELECT node's own subtree_cost = its self cost + sum of children.
    """
    sw = config["self_weights"]
    rp = config["rule_penalties"]
    children: list[CostNode] = []

    current_aliases = _collect_table_aliases(select_node)
    all_visible = outer_aliases | current_aliases

    from_clause = select_node.args.get("from_")
    joins = select_node.args.get("joins") or []

    # ------------------------------------------------------------------
    # 1. Base table scans in FROM — use direct child only, not find_all
    # ------------------------------------------------------------------
    direct_tables, direct_subqs = _get_direct_from_source(from_clause)

    for tbl in direct_tables:
        scan = CostNode(
            node_type="scan",
            label=f"SCAN({tbl.name})",
            magnitude=parent_magnitude,
            self_weight=sw["scan"],
            rule_penalties=0.0,
            subtree_cost=sw["scan"] * parent_magnitude,
            critical_path_pct=0.0,
        )
        children.append(scan)

    # Derived table subqueries in FROM — recurse.
    # If this SELECT has JOINs, the derived table is the "build side"; its
    # internal operations should be costed at the join's output magnitude so
    # that deeply nested joins accumulate higher costs than shallower ones.
    # We compute the effective magnitude as parent_magnitude × product of all
    # join factors at this level (conservative: use max join factor).
    derived_table_magnitude = parent_magnitude
    if joins:
        # Multiply by the highest join factor among all joins at this level
        max_factor = 1.0
        for j in joins:
            jt = _join_condition_type(j)
            if jt == "cross_join":
                max_factor = max(max_factor, config["CROSS_JOIN_BLOWUP"])
            elif jt == "non_equi_join":
                max_factor = max(max_factor, config["NON_EQUI_FACTOR"])
            else:
                max_factor = max(max_factor, config["EQUI_JOIN_FACTOR"])
        derived_table_magnitude = parent_magnitude * max_factor

    for subq in direct_subqs:
        inner_sel = subq.this
        if isinstance(inner_sel, exp.Select):
            inner_node = _analyze_select(
                inner_sel,
                config,
                parent_magnitude=derived_table_magnitude,
                outer_aliases=all_visible,
                depth=depth + 1,
            )
            # R2: SELECT * inside a derived table that feeds a JOIN
            has_star = _has_select_star(inner_sel)
            r2_penalty = rp["R2_select_star_under_join"] if (has_star and joins) else 0.0
            if r2_penalty:
                inner_node.rule_penalties += r2_penalty
                inner_node.subtree_cost += r2_penalty
                inner_node.label += " [R2:SELECT*]"
            children.append(inner_node)

    # ------------------------------------------------------------------
    # 2. JOINs (at this SELECT level)
    # ------------------------------------------------------------------
    for join in joins:
        join_type = _join_condition_type(join)

        if join_type == "cross_join":
            j_magnitude = parent_magnitude * config["CROSS_JOIN_BLOWUP"]
            j_self_wt = sw["cross_join"]
            j_penalty = 0.0
        elif join_type == "non_equi_join":
            j_magnitude = parent_magnitude * config["NON_EQUI_FACTOR"]
            j_self_wt = sw["non_equi_join"]
            j_penalty = rp["R3_non_equi"]
        else:  # equi_join
            j_magnitude = parent_magnitude * config["EQUI_JOIN_FACTOR"]
            j_self_wt = sw["equi_join"]
            j_penalty = 0.0

        # Scan node for the joined table
        joined_table_names = [t.name for t in join.find_all(exp.Table)]
        label = f"{join_type.upper()}({','.join(joined_table_names)})"

        # offline_truth_ceiling: we can't know table cardinality without stats
        flags: list[str] = []
        if join_type == "cross_join":
            flags.append("offline_truth_ceiling")

        join_node = CostNode(
            node_type=join_type,
            label=label,
            magnitude=j_magnitude,
            self_weight=j_self_wt,
            rule_penalties=j_penalty,
            subtree_cost=j_self_wt * j_magnitude + j_penalty,
            critical_path_pct=0.0,
            flags=flags,
        )
        children.append(join_node)

    # ------------------------------------------------------------------
    # 3. Correlated/scalar subqueries in WHERE, HAVING, SELECT list
    # ------------------------------------------------------------------
    for clause_key in ("where", "having"):
        clause = select_node.args.get(clause_key)
        if not clause:
            continue

        # EXISTS(SELECT ...) — Exists.this is a Select directly
        for exists_node in clause.find_all(exp.Exists):
            inner_sel = exists_node.this
            if not isinstance(inner_sel, exp.Select):
                continue
            correlated = _is_select_correlated(inner_sel, all_visible)
            node_type = "correlated_subquery" if correlated else "subquery"
            self_wt = sw["correlated_subquery"] if correlated else sw["subquery"]
            penalty = rp["R1_correlated"] if correlated else 0.0
            label = "CORRELATED_EXISTS" if correlated else "EXISTS_SUBQ"
            sub_magnitude = parent_magnitude * config["SUBQUERY_FACTOR"]

            inner_node = _analyze_select(
                inner_sel,
                config,
                parent_magnitude=sub_magnitude,
                outer_aliases=all_visible,
                depth=depth + 1,
            )
            sub_node = CostNode(
                node_type=node_type,
                label=label,
                magnitude=sub_magnitude,
                self_weight=self_wt,
                rule_penalties=penalty,
                subtree_cost=self_wt * sub_magnitude + penalty + inner_node.subtree_cost,
                critical_path_pct=0.0,
                children=[inner_node],
            )
            children.append(sub_node)

        # Regular scalar subqueries (NOT wrapped in EXISTS)
        for subq in clause.find_all(exp.Subquery):
            # Skip if this subquery is inside an Exists (already handled above)
            if subq.find_ancestor(exp.Exists):
                continue
            inner_sel = subq.this
            if not isinstance(inner_sel, exp.Select):
                continue
            correlated = _is_select_correlated(inner_sel, all_visible)
            node_type = "correlated_subquery" if correlated else "subquery"
            self_wt = sw["correlated_subquery"] if correlated else sw["subquery"]
            penalty = rp["R1_correlated"] if correlated else 0.0
            label = "CORRELATED_SUBQ" if correlated else "SCALAR_SUBQ"
            sub_magnitude = parent_magnitude * config["SUBQUERY_FACTOR"]

            inner_node = _analyze_select(
                inner_sel,
                config,
                parent_magnitude=sub_magnitude,
                outer_aliases=all_visible,
                depth=depth + 1,
            )
            sub_node = CostNode(
                node_type=node_type,
                label=label,
                magnitude=sub_magnitude,
                self_weight=self_wt,
                rule_penalties=penalty,
                subtree_cost=self_wt * sub_magnitude + penalty + inner_node.subtree_cost,
                critical_path_pct=0.0,
                children=[inner_node],
            )
            children.append(sub_node)

    # ------------------------------------------------------------------
    # 4. Window functions (at this SELECT level)
    # ------------------------------------------------------------------
    if select_node.find(exp.Window):
        window_node = CostNode(
            node_type="window",
            label="WINDOW_FUNC",
            magnitude=parent_magnitude,
            self_weight=sw["window"],
            rule_penalties=0.0,
            subtree_cost=sw["window"] * parent_magnitude,
            critical_path_pct=0.0,
        )
        children.append(window_node)

    # ------------------------------------------------------------------
    # 5. Aggregate (GROUP BY at this SELECT level)
    # ------------------------------------------------------------------
    if select_node.args.get("group"):
        agg_node = CostNode(
            node_type="aggregate",
            label="AGGREGATE(GROUP_BY)",
            magnitude=parent_magnitude,
            self_weight=sw["aggregate"],
            rule_penalties=0.0,
            subtree_cost=sw["aggregate"] * parent_magnitude,
            critical_path_pct=0.0,
        )
        children.append(agg_node)

    # ------------------------------------------------------------------
    # 6. Sort (ORDER BY at this SELECT level)
    # ------------------------------------------------------------------
    if select_node.args.get("order"):
        has_limit = select_node.args.get("limit") is not None
        # R4: ORDER BY without LIMIT inside a derived table / subquery
        sort_penalty = 0.0
        sort_label = "SORT(ORDER_BY)"
        if depth > 0 and not has_limit:
            sort_penalty = rp["R4_order_by_no_limit_in_subquery"]
            sort_label = "SORT(ORDER_BY_NO_LIMIT) [R4]"
        sort_node = CostNode(
            node_type="sort",
            label=sort_label,
            magnitude=parent_magnitude,
            self_weight=sw["sort"],
            rule_penalties=sort_penalty,
            subtree_cost=sw["sort"] * parent_magnitude + sort_penalty,
            critical_path_pct=0.0,
        )
        children.append(sort_node)

    # ------------------------------------------------------------------
    # 7. Filter (WHERE at this SELECT level) — lightweight
    # ------------------------------------------------------------------
    if select_node.args.get("where"):
        filter_node = CostNode(
            node_type="filter",
            label="FILTER(WHERE)",
            magnitude=parent_magnitude,
            self_weight=sw["filter"],
            rule_penalties=0.0,
            subtree_cost=sw["filter"] * parent_magnitude,
            critical_path_pct=0.0,
        )
        children.append(filter_node)

    # ------------------------------------------------------------------
    # Build this SELECT's CostNode — its cost = self_weight + sum(children)
    # The SELECT node itself represents the "project" step
    # ------------------------------------------------------------------
    total_children_cost = sum(c.subtree_cost for c in children)
    select_self_cost = sw["project"] * parent_magnitude
    root_node = CostNode(
        node_type="select",
        label=f"SELECT(depth={depth})",
        magnitude=parent_magnitude,
        self_weight=sw["project"],
        rule_penalties=0.0,
        subtree_cost=select_self_cost + total_children_cost,
        critical_path_pct=0.0,
        children=children,
    )
    return root_node


def _set_critical_path_pct(node: CostNode, root_cost: float) -> None:
    if root_cost > 0:
        node.critical_path_pct = node.subtree_cost / root_cost * 100
    for child in node.children:
        _set_critical_path_pct(child, root_cost)


# ---------------------------------------------------------------------------
# Flatten tree — excludes "select" wrapper nodes so the ranking focuses
# on meaningful operation nodes (joins, subqueries, scans, etc.)
# ---------------------------------------------------------------------------

def flatten_nodes(root: CostNode, exclude_select_wrappers: bool = True) -> list[CostNode]:
    """
    Flatten tree to list sorted by subtree_cost descending.
    By default, excludes node_type="select" wrapper nodes so rankings
    reflect actual operation costs, not accumulated wrapper totals.
    """
    nodes: list[CostNode] = []

    def _collect(n: CostNode) -> None:
        if not (exclude_select_wrappers and n.node_type == "select"):
            nodes.append(n)
        for c in n.children:
            _collect(c)

    _collect(root)
    return sorted(nodes, key=lambda n: n.subtree_cost, reverse=True)


def flatten_all_nodes(root: CostNode) -> list[CostNode]:
    """Flatten all nodes including select wrappers."""
    return flatten_nodes(root, exclude_select_wrappers=False)
