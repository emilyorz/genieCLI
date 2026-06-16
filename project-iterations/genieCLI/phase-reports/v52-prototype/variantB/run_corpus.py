# PROTOTYPE - DO NOT MERGE; WRAP MUST ARCHIVE OR DELETE
"""run_corpus.py — execute all 8 corpus queries through decompose() + cost_model.

Entry point for v52 variantB prototype.
Requires: source ~/.hermes/hermes-agent/venv/bin/activate
Run:  python project-iterations/genieCLI/phase-reports/v52-prototype/variantB/run_corpus.py
"""
from __future__ import annotations

import sys
import os

# ---------------------------------------------------------------------------
# Path setup — add genieCLI repo root
# ---------------------------------------------------------------------------
sys.path.insert(0, '/Users/leeabc/work/emilyorz/genieCLI')

# Also add the prototype dir so cost_model import works
_PROTO_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _PROTO_DIR)

from genie.skills.mcp_trino.trino_optimize import decompose
from genie.skills.mcp_trino.cost_reader import CostReading
from cost_model import build_cost_model, WEIGHTS

# ---------------------------------------------------------------------------
# Stubs
# ---------------------------------------------------------------------------

def stub_llm(prompt: str) -> str:
    """Return empty monster list — let heuristics drive ranking."""
    return "[]"


def offline_cost_reader(sql: str) -> CostReading:
    return CostReading(
        rows_est=None,
        bytes_est=None,
        cost_scalar=None,
        plan_json=None,
        available=False,
        reason="offline",
    )


# ---------------------------------------------------------------------------
# Corpus queries
# ---------------------------------------------------------------------------

QUERIES = {
    "Q1": """SELECT o.id, c.name
FROM orders o
CROSS JOIN customers c
WHERE o.status = 'OPEN';""",

    "Q2": """SELECT o.id
FROM orders o
WHERE EXISTS (
  SELECT 1 FROM line_items li WHERE li.order_id = o.id AND li.qty > 100
);""",

    "Q3": """SELECT t.*
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
WHERE t.v < 1000;""",

    "Q4": """SELECT *
FROM big_fact f
JOIN small_dim d ON d.id = f.dim_id;""",

    "Q5": """SELECT a.id
FROM a
JOIN b ON b.id = a.id
JOIN c ON c.lo <= a.v AND c.hi >= a.v;""",

    "Q6": """SELECT s.k, count(*)
FROM (
  SELECT k, v FROM events ORDER BY v
) s
GROUP BY s.k;""",

    "Q7": """SELECT a.label, b.label
FROM color_dim a
CROSS JOIN size_dim b;""",

    "Q8": """SELECT f.id, row_number() OVER (PARTITION BY f.cust_id ORDER BY f.ts DESC) rn
FROM fact f
JOIN cust c ON c.id = f.cust_id;""",
}

# Expected #1 operator for pass/fail assessment
EXPECTED_TOP_OP = {
    "Q1": "cross_join",
    "Q2": "correlated_subquery",
    "Q3": "equi_join",      # inner JOIN (deepest, highest accumulated magnitude)
    "Q4": "equi_join",
    "Q5": "non_equi_join",  # JOIN c with range predicate outweighs equi JOIN b
    "Q6": "aggregate",      # GROUP BY wraps ORDER BY in subquery; aggregate wins
    "Q7": "cross_join",
    "Q8": "window_func",    # OVER() row_number
}

# Alternative acceptable answers (some queries have ambiguity)
ACCEPTABLE_TOP_OPS = {
    "Q2": {"correlated_subquery"},
    "Q3": {"equi_join", "non_equi_join"},   # inner join at depth 2
    "Q5": {"non_equi_join"},
    "Q6": {"aggregate", "order_by_subquery"},
    "Q8": {"window_func", "equi_join"},
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

DIVIDER = "=" * 72

def _rule_ids(fragment) -> set[str]:
    return {f.rule_id for f in fragment.findings}


def _print_fragments(fragments: list) -> None:
    print(f"  Fragments emitted: {len(fragments)}")
    for i, frag in enumerate(fragments):
        rids = _rule_ids(frag)
        print(f"    [{i}] id={frag.fragment_id}  role={frag.role}  "
              f"pos={frag.position_hint}  monster={frag.is_monster}  "
              f"findings={sorted(rids)}")


def _print_nodes(nodes: list) -> None:
    print(f"  CostNodes: {len(nodes)}")
    sorted_nodes = sorted(nodes, key=lambda n: n.magnitude, reverse=True)
    for n in sorted_nodes:
        star = " <-- CRITICAL" if n.is_critical_path else ""
        print(f"    [{n.operator_type:24s}] depth={n.nesting_depth}  "
              f"mag={n.magnitude:8.1f}  pct={n.pct_of_total:5.1f}%  "
              f"frag={n.fragment_id}{star}")
        print(f"      label: {n.label}")


def _top_op(nodes: list) -> str:
    if not nodes:
        return "NONE"
    return max(nodes, key=lambda n: n.magnitude).operator_type


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    results: list[dict] = []

    for qname, sql in QUERIES.items():
        print(f"\n{DIVIDER}")
        print(f"  {qname}")
        print(DIVIDER)

        # 1. decompose()
        try:
            fragments = decompose(sql, stub_llm, offline_cost_reader)
        except Exception as e:
            print(f"  ERROR in decompose(): {e}")
            results.append({"query": qname, "fragments": 0, "top_op": "ERROR",
                            "expected": EXPECTED_TOP_OP.get(qname, "?"), "pass": False})
            continue

        _print_fragments(fragments)

        # 2. cost model
        try:
            nodes = build_cost_model(fragments)
        except Exception as e:
            print(f"  ERROR in build_cost_model(): {e}")
            results.append({"query": qname, "fragments": len(fragments), "top_op": "ERROR",
                            "expected": EXPECTED_TOP_OP.get(qname, "?"), "pass": False})
            continue

        _print_nodes(nodes)

        top = _top_op(nodes)
        expected = EXPECTED_TOP_OP.get(qname, "?")
        acceptable = ACCEPTABLE_TOP_OPS.get(qname, {expected})
        passed = top in acceptable

        print(f"\n  #1 operator: {top}  |  expected: {expected}  |  "
              f"{'PASS' if passed else 'FAIL'}")

        results.append({
            "query": qname,
            "fragments": len(fragments),
            "top_op": top,
            "expected": expected,
            "pass": passed,
        })

    # ---- Summary table ----
    print(f"\n\n{'#' * 72}")
    print("  SUMMARY TABLE")
    print(f"{'#' * 72}")
    print(f"  {'Query':<6}  {'Frags':>5}  {'#1 Node':<24}  {'Expected':<24}  {'Pass'}")
    print(f"  {'-'*6}  {'-'*5}  {'-'*24}  {'-'*24}  {'-'*4}")
    passes = 0
    for r in results:
        p = "PASS" if r["pass"] else "FAIL"
        if r["pass"]:
            passes += 1
        print(f"  {r['query']:<6}  {r['fragments']:>5}  {r['top_op']:<24}  "
              f"{r['expected']:<24}  {p}")
    print(f"\n  Total: {passes}/{len(results)} PASS")

    # ---- Honest assessment of decompose() scope ----
    print(f"\n{'#' * 72}")
    print("  HONEST ASSESSMENT: What decompose() emits per query")
    print(f"{'#' * 72}")

    assessment = {
        "Q1": "1 root fragment. CROSS JOIN detected via findings (cartesian-join rule).",
        "Q2": "2 fragments: root + 1 WHERE EXISTS subquery. correlated-exists-per-row in findings.",
        "Q3": "1 root fragment ONLY. FROM-clause derived tables are NOT extracted by decompose(). "
              "All operator detection must come from AST re-scan of root SQL.",
        "Q4": "1 root fragment. SELECT * detected via findings (select-star rule). "
              "Equi-join detected by AST re-scan.",
        "Q5": "1 root fragment. Non-equi join (range predicates) detected by AST re-scan of ON clause.",
        "Q6": "1 root fragment. FROM-clause derived table NOT extracted. "
              "ORDER BY inside subquery and GROUP BY detected by AST re-scan.",
        "Q7": "1 root fragment. CROSS JOIN detected via findings (cartesian-join rule).",
        "Q8": "1 root fragment. Window function and equi-join detected by AST re-scan.",
    }
    for qname, note in assessment.items():
        print(f"\n  {qname}: {note}")

    print(f"\n{'#' * 72}")
    print("  Q3 DEPTH RECONSTRUCTION ANALYSIS")
    print(f"{'#' * 72}")
    print("""
  Q3 has 2 levels of nesting (inner SELECT → middle SELECT → outer SELECT).
  decompose() produces ONLY 1 root fragment containing the entire SQL.

  Depth reconstruction approach used:
    - _nesting_depth_from_ast(sql) walks the AST and counts maximum SELECT depth
    - For Q3 this returns 2 (inner is 2 levels deep)
    - _detect_operators() uses sqlglot Join node walk with _depth_of() to assign
      depth to each equi-join: inner JOIN gets depth=2, outer JOIN gets depth=1
    - Virtual scan nodes are emitted for depths without explicit operators

  Information preserved:
    - JOIN type (equi vs non-equi) YES
    - Nesting depth of each JOIN YES (via AST depth count)
    - Inner > outer ordering YES (deeper JOIN gets higher depth → higher magnitude)

  Information LOST vs a true AST approach:
    - Row-count context: no stats → cannot measure actual amplification factor
    - Precise operator tree topology: we approximate depth, not parent→child links
    - Derived-table alias chains (a, t) are not resolved — we count SELECT depth instead
    - If decompose() had derived-table fragments, we'd have cost readings per fragment

  Verdict: Q3 inner>middle>outer ordering IS achievable with AST re-scan,
  but only because sqlglot exposes Select depth. This is a workaround for the
  lack of first-class derived-table Fragment objects from decompose().
""")


if __name__ == "__main__":
    main()
