# PROTOTYPE - DO NOT MERGE; WRAP MUST ARCHIVE OR DELETE
"""cost_model.py — flat-Fragment structural offline cost model for v52 variantB.

Accepts a list[Fragment] from decompose(), re-scans each fragment's SQL via sqlglot
to detect operator types embedded in the fragment body, then builds a pseudo-tree of
CostNode objects with magnitude algebra.

Key limitation (documented honestly):
  decompose() only emits Fragment objects for CTEs, WHERE EXISTS/IN subqueries, and
  the root query.  JOIN nodes, ORDER BY, GROUP BY, window functions, and FROM-clause
  derived tables are all EMBEDDED in one of those fragments.  We re-scan the fragment
  SQL via sqlglot AST to detect them and create virtual CostNodes.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

# ---------------------------------------------------------------------------
# Weight table — ordinal magnitude constants
# ---------------------------------------------------------------------------
WEIGHTS: dict[str, int] = {
    "cross_join":           100,   # N×M blowup; no join key
    "non_equi_join":         50,   # nested-loop required
    "correlated_subquery":   40,   # per-row re-execution
    "window_func":           20,   # partition + sort
    "equi_join":             15,   # hash join
    "order_by_subquery":     12,   # wasted sort inside subquery / derived table
    "aggregate":             10,   # GROUP BY
    "select_star":            8,   # wide shuffle penalty
    "scan":                   1,   # base table scan
}

# Structural blowup multipliers (applied on top of weight)
BLOWUP_MULTIPLIER: dict[str, float] = {
    "cross_join":          3.0,
    "correlated_subquery": 3.0,
    "non_equi_join":       1.5,
}

# Depth scaling: each level of nesting multiplies magnitude
DEPTH_FACTOR_BASE: float = 2.0  # magnitude × DEPTH_FACTOR_BASE ^ nesting_depth


@dataclass
class CostNode:
    node_id: str
    label: str           # human-readable description
    operator_type: str   # key into WEIGHTS
    fragment_id: str     # source Fragment
    nesting_depth: int   # 0 = outermost; higher = deeper inside query
    magnitude: float
    pct_of_total: float  # filled in by build_cost_model()
    is_critical_path: bool


# ---------------------------------------------------------------------------
# Operator detection helpers (sqlglot re-scan of a single fragment's SQL)
# ---------------------------------------------------------------------------

def _detect_operators(sql: str, finding_rule_ids: set[str]) -> list[dict]:
    """Return a list of operator dicts found in *sql*, combining:
      - Finding rule_ids already attached to the Fragment
      - sqlglot AST walk of the fragment SQL

    Each dict: {"op": str, "depth": int, "label": str}
    depth is the estimated nesting depth within this fragment (for nested derived tables).
    """
    try:
        import sqlglot
        import sqlglot.expressions as exp
        tree = sqlglot.parse_one(sql, read="trino")
    except Exception:
        tree = None

    ops: list[dict] = []

    # ---- 1. Findings-based detections (already classified by rule_gate) ----
    if "cartesian-join" in finding_rule_ids:
        ops.append({"op": "cross_join", "depth": 0, "label": "CROSS JOIN (from findings)"})
    if "correlated-exists-per-row" in finding_rule_ids:
        ops.append({"op": "correlated_subquery", "depth": 0, "label": "correlated EXISTS/IN (from findings)"})
    if "select-star" in finding_rule_ids:
        ops.append({"op": "select_star", "depth": 0, "label": "SELECT * (from findings)"})
    if "order-by-in-subquery" in finding_rule_ids:
        ops.append({"op": "order_by_subquery", "depth": 0, "label": "ORDER BY in subquery (from findings)"})

    if tree is None:
        # Fallback: scan SQL text for window functions
        if re.search(r'\bOVER\s*\(', sql, re.IGNORECASE):
            ops.append({"op": "window_func", "depth": 0, "label": "Window function OVER() (text scan)"})
        return ops

    # ---- 2. AST walk: find operator types embedded in this fragment's SQL ----
    # Track cross joins via AST as cross-check / for queries findings may miss
    cross_join_found_ast = False
    non_equi_join_found = False
    correlated_subquery_found_ast = False
    window_found = False
    aggregate_found = False
    order_in_subquery_found = False
    equi_joins: list[int] = []   # nesting depths

    # Walk AST and tag nesting depth (count enclosing Select nodes)
    def _depth_of(node) -> int:
        """Count how many Select ancestors this node has (= nesting level)."""
        depth = 0
        p = getattr(node, "parent", None)
        while p is not None:
            if type(p).__name__ == "Select":
                depth += 1
            p = getattr(p, "parent", None)
        return depth

    for node in tree.walk():
        node_type = type(node).__name__

        # Cross join: Join with no ON/USING and kind contains "cross", OR
        # products of two tables with no explicit join key
        if node_type == "Join":
            kind = str(getattr(node, "kind", "") or "").lower()
            on_clause = node.args.get("on")
            using_clause = node.args.get("using")
            if "cross" in kind:
                cross_join_found_ast = True
            elif on_clause is None and using_clause is None and "cross" not in kind:
                # Implicit cross join (FROM a, b style)
                cross_join_found_ast = True
            elif on_clause is not None and not cross_join_found_ast:
                # Check for non-equi: range predicates in ON clause.
                # Range = <=, >=, <>, !=, or standalone < / > (not part of <= or >=)
                on_sql = on_clause.sql() if on_clause else ""
                has_range = bool(re.search(
                    r'(?:<=|>=|<>|!=|(?<![<>!])>(?!=)|(?<![<>!])<(?!=))',
                    on_sql
                ))
                if has_range:
                    non_equi_join_found = True
                else:
                    equi_joins.append(_depth_of(node))

        # Correlated EXISTS/IN subquery via AST: look for Exists/In nodes that
        # reference tables NOT defined in their inner FROM/JOIN.
        # This catches cases where the findings-based detection misses due to
        # alias resolution (e.g. li alias for line_items not in inner_tables set).
        if node_type == "Exists":
            inner_sel = node.this
            if inner_sel is not None:
                try:
                    # Collect inner FROM/JOIN table names AND aliases
                    inner_scope: set[str] = set()
                    from_node_inner = inner_sel.args.get("from_")
                    if from_node_inner:
                        for tbl in from_node_inner.find_all(exp.Table):
                            inner_scope.add(str(tbl.name).lower())
                            alias = tbl.args.get("alias")
                            if alias:
                                inner_scope.add(str(alias).lower())
                    for jn in (inner_sel.args.get("joins") or []):
                        for tbl in jn.find_all(exp.Table):
                            inner_scope.add(str(tbl.name).lower())
                            alias = tbl.args.get("alias")
                            if alias:
                                inner_scope.add(str(alias).lower())
                    # Check for outer-scope references: column qualifier not in inner scope
                    for col in inner_sel.find_all(exp.Column):
                        tbl_name = (col.table or "").lower()
                        if tbl_name and tbl_name not in inner_scope:
                            correlated_subquery_found_ast = True
                            break
                except Exception:
                    pass

        # Window function
        if node_type in ("Window", "WindowSpec", "Over"):
            window_found = True

        # Aggregate: Group node
        if node_type == "Group":
            aggregate_found = True

        # ORDER BY inside a subquery / derived table (not outermost ORDER BY)
        if node_type == "Order":
            d = _depth_of(node)
            if d > 0:  # d==0 means it's the top-level ORDER BY
                order_in_subquery_found = True

    # Merge AST findings with findings-based (avoid duplicates)
    rule_ids_seen = {o["op"] for o in ops}

    if cross_join_found_ast and "cross_join" not in rule_ids_seen:
        ops.append({"op": "cross_join", "depth": 0, "label": "CROSS JOIN (AST)"})
    if non_equi_join_found:
        ops.append({"op": "non_equi_join", "depth": 0, "label": "Non-equi JOIN (range predicates in ON)"})
    if correlated_subquery_found_ast and "correlated_subquery" not in rule_ids_seen:
        ops.append({"op": "correlated_subquery", "depth": 0, "label": "Correlated EXISTS/IN (AST outer-ref scan)"})
    if window_found:
        ops.append({"op": "window_func", "depth": 0, "label": "Window function OVER()"})
    if aggregate_found:
        ops.append({"op": "aggregate", "depth": 0, "label": "GROUP BY aggregate"})
    if order_in_subquery_found and "order_by_subquery" not in rule_ids_seen:
        ops.append({"op": "order_by_subquery", "depth": 0, "label": "ORDER BY in subquery/derived table"})
    for d in equi_joins:
        ops.append({"op": "equi_join", "depth": d, "label": f"Equi-JOIN (depth={d})"})

    # If no ops detected at all, add a base scan
    if not ops:
        ops.append({"op": "scan", "depth": 0, "label": "base table scan"})

    return ops


def _nesting_depth_from_ast(sql: str) -> int:
    """Return the maximum nesting depth of SELECT statements in *sql*.
    Used for Q3-style deeply nested derived tables that decompose() collapses
    into a single root fragment.
    """
    try:
        import sqlglot
        import sqlglot.expressions as exp
        tree = sqlglot.parse_one(sql, read="trino")
        if tree is None:
            return 0
        max_depth = 0
        for node in tree.walk():
            if type(node).__name__ == "Select":
                depth = 0
                p = getattr(node, "parent", None)
                while p is not None:
                    if type(p).__name__ == "Select":
                        depth += 1
                    p = getattr(p, "parent", None)
                max_depth = max(max_depth, depth)
        return max_depth
    except Exception:
        return 0


# ---------------------------------------------------------------------------
# Fragment → CostNode builder
# ---------------------------------------------------------------------------

def _fragment_to_nodes(fragment, base_depth: int = 0) -> list[dict]:
    """Convert one Fragment into a list of operator dicts, taking into account
    the fragment's role and existing findings.
    """
    finding_rule_ids = {f.rule_id for f in fragment.findings}
    ops = _detect_operators(fragment.sql, finding_rule_ids)
    # Adjust depth relative to fragment's own nesting in the full query
    for op in ops:
        op["depth"] = op["depth"] + base_depth
        op["fragment_id"] = fragment.fragment_id
    return ops


def _compute_magnitude(op_type: str, depth: int, parent_magnitude: Optional[float] = None) -> float:
    """Compute ordinal magnitude for one operator node.

    magnitude = weight × depth_factor × blowup
    For correlated_subquery: magnitude = parent_magnitude × inner_weight × blowup
    """
    weight = WEIGHTS.get(op_type, 1)
    depth_factor = DEPTH_FACTOR_BASE ** depth
    blowup = BLOWUP_MULTIPLIER.get(op_type, 1.0)

    if op_type == "correlated_subquery" and parent_magnitude is not None:
        # Correlated: evaluated once per row of parent → multiply parent cost
        return parent_magnitude * weight * blowup
    return weight * depth_factor * blowup


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def build_cost_model(fragments: list) -> list[CostNode]:
    """Build a list of CostNode objects from a flat Fragment list.

    Strategy:
    1. For WHERE EXISTS/IN subquery fragments (role="subquery"): treat the
       fragment as nested inside the root; detect correlated_subquery op.
    2. For CTE fragments (role="cte"): treat as depth=0 nodes (materialized
       once; not per-row).
    3. For root fragments: walk the AST to detect JOIN types, aggregates,
       window functions, and ORDER BY at each nesting level.
    4. Q3/Q6 style (single root with nested derived tables): use
       _nesting_depth_from_ast() to estimate inner nesting levels and assign
       virtual nodes per detected nesting level.
    """
    all_ops: list[dict] = []

    # Map fragment_id → fragment for parent-magnitude lookups
    frag_map = {f.fragment_id: f for f in fragments}

    # ---- Pass 1: collect raw operator dicts per fragment ----
    for frag in fragments:
        if frag.role == "subquery":
            # Subquery is nested inside root — base_depth=1
            ops = _fragment_to_nodes(frag, base_depth=1)
            all_ops.extend(ops)
        elif frag.role == "cte":
            # CTE: materialised independently, depth=0
            ops = _fragment_to_nodes(frag, base_depth=0)
            all_ops.extend(ops)
        elif frag.role == "root":
            # For the root, check max nesting to handle Q3-style collapsed fragments
            max_nest = _nesting_depth_from_ast(frag.sql)
            if max_nest > 0:
                # Q3/Q6 style: multiple nesting levels inside one root fragment
                # We emit one virtual-node batch per nesting level
                # First detect ops at each depth via AST, using depth context
                ops = _fragment_to_nodes(frag, base_depth=0)
                # For each level from inner (max_nest) to outer (0), emit ops
                # Re-classify: ops with equi_join get depth from AST, others get depth=0
                # We also add virtual "inner query" scan nodes at deeper levels
                all_ops.extend(ops)
                # Add virtual scan nodes for each inner level that doesn't already have ops
                depths_with_ops = {o["depth"] for o in ops}
                for level in range(1, max_nest + 1):
                    if level not in depths_with_ops:
                        all_ops.append({
                            "op": "scan",
                            "depth": level,
                            "fragment_id": frag.fragment_id,
                            "label": f"inner query level {level} (virtual scan node)",
                        })
            else:
                ops = _fragment_to_nodes(frag, base_depth=0)
                all_ops.extend(ops)
        else:
            ops = _fragment_to_nodes(frag, base_depth=0)
            all_ops.extend(ops)

    # ---- Pass 2: compute magnitudes ----
    # Find root fragment magnitude estimate for correlated subquery calculation
    root_fragments = [f for f in fragments if f.role == "root"]
    root_weight_estimate = WEIGHTS["scan"]  # fallback
    if root_fragments:
        root_frag = root_fragments[0]
        root_rule_ids = {f.rule_id for f in root_frag.findings}
        root_ops = _detect_operators(root_frag.sql, root_rule_ids)
        # Estimate root magnitude as the highest-weight non-correlated op at depth=0
        root_top = max(
            (WEIGHTS.get(o["op"], 1) * BLOWUP_MULTIPLIER.get(o["op"], 1.0)
             for o in root_ops if o.get("depth", 0) == 0 and o["op"] != "correlated_subquery"),
            default=float(WEIGHTS["scan"])
        )
        root_weight_estimate = root_top

    nodes: list[CostNode] = []
    for idx, op in enumerate(all_ops):
        op_type = op["op"]
        depth = op.get("depth", 0)
        frag_id = op.get("fragment_id", "__root__")
        label = op.get("label", op_type)

        parent_mag = root_weight_estimate if op_type == "correlated_subquery" else None
        magnitude = _compute_magnitude(op_type, depth, parent_mag)

        node_id = f"{frag_id}:{op_type}:{idx}"
        nodes.append(CostNode(
            node_id=node_id,
            label=label,
            operator_type=op_type,
            fragment_id=frag_id,
            nesting_depth=depth,
            magnitude=magnitude,
            pct_of_total=0.0,   # filled below
            is_critical_path=False,
        ))

    if not nodes:
        return []

    # ---- Pass 3: percentages ----
    total_mag = sum(n.magnitude for n in nodes)
    if total_mag > 0:
        for n in nodes:
            n.pct_of_total = (n.magnitude / total_mag) * 100.0

    # ---- Pass 4: mark critical path (top node by magnitude) ----
    # Sort by magnitude descending
    nodes_sorted = sorted(nodes, key=lambda n: n.magnitude, reverse=True)
    if nodes_sorted:
        top_node = nodes_sorted[0]
        # Mark top node and any nodes sharing same operator_type+depth (chain)
        for n in nodes:
            if n.node_id == top_node.node_id:
                n.is_critical_path = True
                break

    return nodes
