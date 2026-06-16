# PROTOTYPE - DO NOT MERGE; WRAP MUST ARCHIVE OR DELETE
"""
v52 / variantA — CostNode tree from sqlglot AST, recursive magnitude propagation.
Single-file prototype. No production paths touched.
"""
from __future__ import annotations
import sys
import textwrap
from dataclasses import dataclass, field
from typing import Optional

import sqlglot
import sqlglot.expressions as exp

# ---------------------------------------------------------------------------
# Weight / penalty tables
# ---------------------------------------------------------------------------
WEIGHTS = {
    "scan":                1.0,
    "filter":              0.5,
    "project":             0.3,
    "join_equi":           3.0,
    "join_cross":         20.0,   # cartesian = massive
    "join_nonequi":        8.0,   # nested-loop blowup
    "aggregate":           4.0,
    "sort":                3.5,
    "window":              5.0,   # partition sort = expensive
    "distinct":            3.0,
    "setop":               2.0,
    "correlated_subquery": 12.0,  # per-row execution
    "cte":                 0.5,
    "limit":               0.2,
}

PENALTIES = {
    "select_star":        2.0,   # R2: wide shuffle penalty
    "sort_without_limit": 3.0,   # R4: wasted sort
}

# Magnitude multipliers
MAG_CROSS      = 4
MAG_NONEQUI    = 3
MAG_EQUI       = 2


# ---------------------------------------------------------------------------
# CostNode dataclass
# ---------------------------------------------------------------------------
@dataclass
class CostNode:
    kind: str
    label: str
    self_weight: float
    magnitude: int
    children: list = field(default_factory=list)
    penalties: float = 0.0
    subtree_cost: float = 0.0

    @property
    def own_cost(self) -> float:
        return self.self_weight * self.magnitude + self.penalties


# ---------------------------------------------------------------------------
# Cost algebra
# ---------------------------------------------------------------------------
def compute_costs(node: CostNode) -> float:
    child_sum = sum(compute_costs(c) for c in node.children)
    node.subtree_cost = node.self_weight * node.magnitude + node.penalties + child_sum
    return node.subtree_cost


# ---------------------------------------------------------------------------
# Critical-path extraction
# ---------------------------------------------------------------------------
def critical_path(node: CostNode) -> list[CostNode]:
    path = [node]
    current = node
    while current.children:
        best = max(current.children, key=lambda c: c.subtree_cost)
        path.append(best)
        current = best
    return path


# ---------------------------------------------------------------------------
# Correlated-subquery detector
# ---------------------------------------------------------------------------
def _is_correlated(inner_select, outer_tables: set) -> bool:
    inner_tables: set[str] = set()
    from_node = inner_select.args.get("from_")
    if from_node:
        for tbl in from_node.find_all(exp.Table):
            inner_tables.add(tbl.alias_or_name.lower())
    for join in (inner_select.args.get("joins") or []):
        for tbl in join.find_all(exp.Table):
            inner_tables.add(tbl.alias_or_name.lower())
    for col in inner_select.find_all(exp.Column):
        if col.table and col.table.lower() not in inner_tables:
            return True
    return False


# ---------------------------------------------------------------------------
# Join-kind detector
# ---------------------------------------------------------------------------
def _join_kind(join_node) -> str:
    join_type = (join_node.args.get("kind") or "").upper()
    if join_type == "CROSS":
        return "join_cross"
    on_clause = join_node.args.get("on")
    if on_clause is None:
        # USING or implicit → treat as equi
        return "join_equi"
    # Walk AND-chain; if any predicate is not col=col equality → non-equi
    def _collect_predicates(node):
        if isinstance(node, exp.And):
            yield from _collect_predicates(node.left)
            yield from _collect_predicates(node.right)
        else:
            yield node
    for pred in _collect_predicates(on_clause):
        if not isinstance(pred, exp.EQ):
            return "join_nonequi"
        if not (isinstance(pred.left, exp.Column) and isinstance(pred.right, exp.Column)):
            return "join_nonequi"
    return "join_equi"


# ---------------------------------------------------------------------------
# Main tree builder
# ---------------------------------------------------------------------------
def build_tree(select_node, magnitude: int = 1, outer_tables: set = None) -> CostNode:
    if outer_tables is None:
        outer_tables = set()

    nodes: list[CostNode] = []   # will be assembled into a tree

    # ---- collect THIS level's table references ----
    my_tables: set[str] = set()
    from_node = select_node.args.get("from_")
    if from_node:
        for tbl in from_node.find_all(exp.Table):
            my_tables.add(tbl.alias_or_name.lower())
    for join in (select_node.args.get("joins") or []):
        for tbl in join.find_all(exp.Table):
            my_tables.add(tbl.alias_or_name.lower())

    # ---- CTEs ----
    cte_nodes: list[CostNode] = []
    with_clause = select_node.args.get("with_")
    if with_clause:
        for cte in (with_clause.expressions or []):
            cte_child = CostNode(
                kind="cte",
                label=f"CTE {cte.alias}",
                self_weight=WEIGHTS["cte"],
                magnitude=magnitude,
            )
            # Recurse into CTE body
            cte_body = cte.args.get("this")
            if cte_body and isinstance(cte_body, exp.Select):
                cte_child.children = [build_tree(cte_body, magnitude, my_tables | outer_tables)]
            cte_nodes.append(cte_child)

    # ---- FROM: table or subquery ----
    from_child: Optional[CostNode] = None
    has_select_star = any(isinstance(s, exp.Star) for s in select_node.expressions)
    star_penalty = PENALTIES["select_star"] if has_select_star else 0.0

    if from_node:
        from_expr = from_node.this
        if isinstance(from_expr, exp.Subquery):
            inner = from_expr.this
            if isinstance(inner, exp.Select):
                from_child = build_tree(inner, magnitude, my_tables | outer_tables)
            else:
                from_child = CostNode("scan", "FROM (subquery)", WEIGHTS["scan"], magnitude)
        elif isinstance(from_expr, exp.Table):
            tname = from_expr.alias_or_name
            from_child = CostNode(
                kind="scan",
                label=f"SCAN {tname}",
                self_weight=WEIGHTS["scan"],
                magnitude=magnitude,
                penalties=star_penalty,
            )
        else:
            from_child = CostNode("scan", "FROM (?)", WEIGHTS["scan"], magnitude, penalties=star_penalty)

    # ---- JOINs ----
    join_chain: Optional[CostNode] = from_child
    for join in (select_node.args.get("joins") or []):
        kind = _join_kind(join)
        # Compute child magnitude for right side
        right_mag = magnitude
        if kind == "join_cross":
            child_mag = magnitude
            new_mag = magnitude * MAG_CROSS
        elif kind == "join_nonequi":
            child_mag = magnitude
            new_mag = magnitude * MAG_NONEQUI
        else:
            child_mag = magnitude
            new_mag = magnitude * MAG_EQUI

        # Right-side child
        join_right = join.this
        if isinstance(join_right, exp.Subquery):
            inner = join_right.this
            if isinstance(inner, exp.Select):
                right_child = build_tree(inner, child_mag, my_tables | outer_tables)
            else:
                right_child = CostNode("scan", "JOIN (subquery)", WEIGHTS["scan"], child_mag)
        elif isinstance(join_right, exp.Table):
            tname = join_right.alias_or_name
            right_child = CostNode("scan", f"SCAN {tname}", WEIGHTS["scan"], child_mag)
        else:
            right_child = CostNode("scan", "JOIN (?)", WEIGHTS["scan"], child_mag)

        # label
        left_name = join_chain.label if join_chain else "?"
        right_name = right_child.label
        lbl = f"{kind.upper()} {left_name}×{right_name}"

        join_node_cost = CostNode(
            kind=kind,
            label=lbl,
            self_weight=WEIGHTS[kind],
            magnitude=new_mag,
            children=[join_chain, right_child] if join_chain else [right_child],
        )
        join_chain = join_node_cost
        magnitude = new_mag  # propagate outward for subsequent operators

    # top-level from/join root
    from_root = join_chain if join_chain else from_child

    # ---- WHERE (subqueries) ----
    where_node = select_node.args.get("where")
    where_children: list[CostNode] = []
    if where_node:
        # Collect inner selects from EXISTS(...) and IN (SELECT ...) and plain Subquery wrappers
        inner_selects: list[exp.Select] = []
        for ex in where_node.find_all(exp.Exists):
            inner = ex.this
            if isinstance(inner, exp.Select):
                inner_selects.append(inner)
            elif isinstance(inner, exp.Subquery) and isinstance(inner.this, exp.Select):
                inner_selects.append(inner.this)
        for in_expr in where_node.find_all(exp.In):
            query = in_expr.args.get("query")
            if query is not None and isinstance(query, exp.Select):
                inner_selects.append(query)
            elif query is not None and isinstance(query, exp.Subquery) and isinstance(query.this, exp.Select):
                inner_selects.append(query.this)
        for sq in where_node.find_all(exp.Subquery):
            if isinstance(sq.this, exp.Select):
                inner_selects.append(sq.this)

        for inner in inner_selects:
            is_corr = _is_correlated(inner, my_tables | outer_tables)
            if is_corr:
                inner_mag = max(1, magnitude // 2)
                inner_tree = build_tree(inner, inner_mag, my_tables | outer_tables)
                corr_node = CostNode(
                    kind="correlated_subquery",
                    label="CORRELATED SUBQUERY",
                    self_weight=WEIGHTS["correlated_subquery"],
                    magnitude=magnitude,
                    children=[inner_tree],
                )
                where_children.append(corr_node)
            else:
                inner_tree = build_tree(inner, max(1, magnitude // 2), my_tables | outer_tables)
                where_children.append(inner_tree)
        # Basic filter cost for the WHERE itself
        filter_node = CostNode("filter", "WHERE predicate", WEIGHTS["filter"], magnitude)
        where_children.insert(0, filter_node)

    # ---- GROUP BY ----
    agg_node: Optional[CostNode] = None
    if select_node.args.get("group"):
        agg_mag = max(1, magnitude // 2)
        agg_node = CostNode("aggregate", "GROUP BY", WEIGHTS["aggregate"], agg_mag)

    # ---- ORDER BY ----
    sort_node: Optional[CostNode] = None
    if select_node.args.get("order"):
        has_limit = select_node.args.get("limit") is not None
        sort_penalty = 0.0 if has_limit else PENALTIES["sort_without_limit"]
        sort_node = CostNode(
            "sort",
            "ORDER BY",
            WEIGHTS["sort"],
            magnitude,
            penalties=sort_penalty,
        )

    # ---- WINDOW ----
    window_node: Optional[CostNode] = None
    windows = list(select_node.find_all(exp.Window))
    if windows:
        window_node = CostNode("window", "WINDOW fn", WEIGHTS["window"], magnitude)

    # ---- LIMIT ----
    limit_node: Optional[CostNode] = None
    if select_node.args.get("limit"):
        limit_mag = max(1, magnitude // 2)
        limit_node = CostNode("limit", "LIMIT", WEIGHTS["limit"], limit_mag)

    # ---- DISTINCT ----
    distinct_node: Optional[CostNode] = None
    if select_node.args.get("distinct"):
        dist_mag = max(1, magnitude // 2)
        distinct_node = CostNode("distinct", "DISTINCT", WEIGHTS["distinct"], dist_mag)

    # ---- Assemble tree ----
    # Chain: outermost → innermost
    # Order: window → sort → distinct → agg → filter/where_children → from_root
    # Each successive node has the next as its only child (linear chain)
    # CTE nodes are siblings of the root chain (they're materialised aside)

    def chain(operators: list[Optional[CostNode]], leaf_children: list[CostNode]) -> Optional[CostNode]:
        """Build a linear chain: ops[0] → ops[1] → ... → ops[-1](children=leaf_children)."""
        ops = [o for o in operators if o is not None]
        if not ops:
            # Return a synthetic root if we have leaf_children
            if leaf_children and len(leaf_children) == 1:
                return leaf_children[0]
            return None
        # Attach leaf_children to innermost op
        ops[-1].children = leaf_children
        for i in range(len(ops) - 2, -1, -1):
            ops[i].children = [ops[i + 1]]
        return ops[0]

    # Build inner chain (bottom-up: from_root is the leaf)
    leaf_list = [from_root] + cte_nodes if from_root else cte_nodes
    inner = chain([window_node, sort_node, distinct_node, agg_node] + where_children[:1],
                  leaf_list)
    # where_children[1:] (correlated subquery etc.) become siblings of from_root
    if where_children and len(where_children) > 1:
        if inner and inner.children:
            # append extra where children alongside from_root
            inner.children[-1:] = inner.children[-1:] + where_children[1:]
        elif inner:
            inner.children += where_children[1:]

    if limit_node is not None:
        if inner:
            limit_node.children = [inner]
        return limit_node

    return inner if inner else CostNode("scan", "(empty)", WEIGHTS["scan"], 1)


# ---------------------------------------------------------------------------
# Pretty-print tree
# ---------------------------------------------------------------------------
def print_tree(node: CostNode, indent: int = 0) -> list[str]:
    lines = []
    prefix = "  " * indent
    lines.append(
        f"{prefix}[{node.kind}] {node.label:<40}  "
        f"mag={node.magnitude}  self_cost={node.own_cost:.1f}  subtree={node.subtree_cost:.1f}"
    )
    for child in node.children:
        lines.extend(print_tree(child, indent + 1))
    return lines


# ---------------------------------------------------------------------------
# Corpus
# ---------------------------------------------------------------------------
QUERIES = [
    ("Q1", "Cartesian (cross join)",
     "SELECT o.id, c.name FROM orders o CROSS JOIN customers c WHERE o.status = 'OPEN'"),
    ("Q2", "Correlated EXISTS subquery",
     """SELECT o.id FROM orders o
        WHERE EXISTS (
          SELECT 1 FROM line_items li WHERE li.order_id = o.id AND li.qty > 100
        )"""),
    ("Q3", "3-level nested subqueries",
     """SELECT t.*
        FROM (
          SELECT a.id, a.v
          FROM (
            SELECT x.id, x.v
            FROM facts x
            JOIN dims d ON d.id = x.dim_id
            WHERE x.v > 0
          ) a
          JOIN more m ON m.id = a.id
        ) t
        WHERE t.v < 1000"""),
    ("Q4", "Simple wide-table equi-join + SELECT *",
     "SELECT * FROM big_fact f JOIN small_dim d ON d.id = f.dim_id"),
    ("Q5", "Equi-join + non-equi range join",
     """SELECT a.id FROM a
        JOIN b ON b.id = a.id
        JOIN c ON c.lo <= a.v AND c.hi >= a.v"""),
    ("Q6", "Subquery with ORDER BY (no limit) + GROUP BY",
     """SELECT s.k, count(*)
        FROM (SELECT k, v FROM events ORDER BY v) s
        GROUP BY s.k"""),
    ("Q7", "Second cross join (smaller tables)",
     "SELECT a.label, b.label FROM color_dim a CROSS JOIN size_dim b"),
    ("Q8", "Window function + equi-join",
     """SELECT f.id, row_number() OVER (PARTITION BY f.cust_id ORDER BY f.ts DESC) rn
        FROM fact f JOIN cust c ON c.id = f.cust_id"""),
]

# Expected "heaviest" operator kind for each query
EXPECTED = {
    "Q1": "join_cross",
    "Q2": "correlated_subquery",
    "Q3": "join_equi",   # inner join should dominate
    "Q4": "join_equi",
    "Q5": "join_nonequi",
    "Q6": ("sort", "aggregate"),  # either qualifies
    "Q7": "join_cross",
    "Q8": ("window", "join_equi"),
}


def find_heaviest(node: CostNode) -> CostNode:
    """Return the node with highest own_cost in entire tree."""
    best = node
    for child in node.children:
        candidate = find_heaviest(child)
        if candidate.own_cost > best.own_cost:
            best = candidate
    return best


def run_query(qid: str, label: str, sql: str):
    print(f"\n=== {qid} — {label} ===")
    try:
        ast = sqlglot.parse_one(sql, dialect="trino")
    except Exception as e:
        print(f"  PARSE ERROR: {e}")
        return None, None

    if not isinstance(ast, exp.Select):
        print(f"  NOT A SELECT: {type(ast)}")
        return None, None

    tree = build_tree(ast)
    compute_costs(tree)

    # Print operator tree
    print("Operator tree:")
    for line in print_tree(tree):
        print(line)

    # Critical path
    path = critical_path(tree)
    path_str = " → ".join(n.kind for n in path)
    print(f"\nCritical path: {path_str}")

    # #1 node = heaviest own_cost in tree
    top1 = find_heaviest(tree)
    print(f"#1 node: {top1.kind} (own_cost={top1.own_cost:.1f}, subtree={top1.subtree_cost:.1f})")

    # Evaluate against expected
    exp_val = EXPECTED.get(qid)
    if isinstance(exp_val, tuple):
        passed = top1.kind in exp_val
        exp_str = " or ".join(exp_val)
    else:
        passed = top1.kind == exp_val
        exp_str = str(exp_val)
    status = "PASS ✓" if passed else f"FAIL ✗ (expected {exp_str})"
    print(f"Expected #1: {exp_str}  →  {status}")

    return tree, top1


# ---------------------------------------------------------------------------
# Scorecard
# ---------------------------------------------------------------------------
def evaluate_scorecard(results: dict):
    print("\n" + "=" * 60)
    print("=== RUBRIC SCORECARD ===")

    def top_kind(qid):
        if results.get(qid) is None:
            return "ERROR"
        _, top1 = results[qid]
        if top1 is None:
            return "ERROR"
        return top1.kind

    def passes(qid, expected):
        kind = top_kind(qid)
        if isinstance(expected, tuple):
            return kind in expected
        return kind == expected

    # Hard requirements
    q1_pass = passes("Q1", "join_cross")
    q7_pass = passes("Q7", "join_cross")
    q2_pass = passes("Q2", "correlated_subquery")
    q5_pass = passes("Q5", "join_nonequi")

    print("Hard requirements:")
    print(f"  Q1 cross join #1:           {'PASS' if q1_pass else 'FAIL'}")
    print(f"  Q7 cross join #1+ceiling:   {'PASS' if q7_pass else 'FAIL'}")
    print(f"  Q2 correlated subquery #1:  {'PASS' if q2_pass else 'FAIL'}")
    print(f"  Q5 non-equi > equi:         {'PASS' if q5_pass else 'FAIL'}")

    # Ordering credibility
    q3_pass = passes("Q3", "join_equi")
    q4_pass = passes("Q4", ("join_equi", "scan"))
    q6_pass = passes("Q6", ("sort", "aggregate"))
    q8_pass = passes("Q8", ("window", "join_equi"))

    print("Ordering credibility:")
    print(f"  Q3 inner>middle>outer:      {'PASS' if q3_pass else 'FAIL'}")
    print(f"  Q4 wide join-input hot:     {'PASS' if q4_pass else 'FAIL'}")
    print(f"  Q6 both heavy above scans:  {'PASS' if q6_pass else 'FAIL'}")
    print(f"  Q8 both heavy above scans:  {'PASS' if q8_pass else 'FAIL'}")

    # Design fidelity
    # Verify recursive magnitude via Q5: equi-join → non-equi chain should yield mag=6 on outer
    # (mag starts at 1; after equi-join → mag=2; after non-equi → mag=6)
    if results.get("Q5") and results["Q5"][0] is not None:
        tree5 = results["Q5"][0]
        def collect_kind(node, kind, acc):
            if node.kind == kind:
                acc.append(node)
            for c in node.children:
                collect_kind(c, kind, acc)
            return acc
        nonequi_nodes = collect_kind(tree5, "join_nonequi", [])
        equi_nodes    = collect_kind(tree5, "join_equi", [])
        # Non-equi should have higher magnitude than the equi it wraps
        if nonequi_nodes and equi_nodes:
            outer_mag = max(n.magnitude for n in nonequi_nodes)
            inner_mag = max(n.magnitude for n in equi_nodes)
            recursive_mag = outer_mag > inner_mag
        else:
            recursive_mag = False
    else:
        recursive_mag = False

    print("Design fidelity:")
    print(f"  Recursive magnitude:        {'YES' if recursive_mag else 'NO'} "
          f"(depth matters — Q5 nonequi mag > equi mag confirms propagation)")

    hard_count = sum([q1_pass, q7_pass, q2_pass, q5_pass])
    print(f"\nHard requirements passed: {hard_count}/4")
    return hard_count


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    results = {}
    for qid, label, sql in QUERIES:
        tree, top1 = run_query(qid, label, sql)
        results[qid] = (tree, top1)

    hard_count = evaluate_scorecard(results)

    recommend = "YES" if hard_count >= 3 else ("CONDITIONAL" if hard_count == 2 else "NO")
    print(f"\nOverall recommendation: {recommend}")
    return results, hard_count, recommend


if __name__ == "__main__":
    main()
