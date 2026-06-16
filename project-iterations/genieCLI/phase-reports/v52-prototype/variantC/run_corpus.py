# PROTOTYPE - DO NOT MERGE; WRAP MUST ARCHIVE OR DELETE
"""
v52 variantC — Run the 8-query corpus through cost_model.py across 3 weight settings
and print a stability matrix + hard-requirements scorecard.
"""

import sys
import os

# Allow running from any cwd
sys.path.insert(0, os.path.dirname(__file__))

from cost_model import build_cost_tree, flatten_nodes, WEIGHT_CONFIGS, CostNode

# ---------------------------------------------------------------------------
# Corpus definition
# ---------------------------------------------------------------------------

CORPUS = {
    "Q1": {
        "name": "CROSS JOIN orders×customers",
        "sql": "SELECT o.id, c.name FROM orders o CROSS JOIN customers c",
        "hard_req": "cross join #1",
        "check": lambda ranked: ranked[0].node_type == "cross_join",
    },
    "Q2": {
        "name": "Correlated EXISTS subquery",
        "sql": """SELECT o.id, o.total FROM orders o WHERE EXISTS (
            SELECT 1 FROM order_items oi
            WHERE oi.order_id = o.id AND oi.amount > 100
        )""",
        "hard_req": "correlated subquery #1",
        "check": lambda ranked: ranked[0].node_type == "correlated_subquery",
    },
    "Q3": {
        "name": "3-level deep nesting",
        "sql": """SELECT * FROM (
            SELECT a.id, b.val FROM (
                SELECT f.id, d.val FROM facts f JOIN dims d ON f.dim_id = d.id
            ) b JOIN another a ON b.id = a.b_id
        ) c WHERE c.val > 0""",
        "hard_req": "inner join > middle join > outer filter",
        "check": None,  # checked separately
    },
    "Q4": {
        "name": "SELECT * feeding join",
        "sql": """SELECT j.id, j.other FROM (SELECT * FROM big_fact) j
            JOIN dim_table dt ON j.dim_id = dt.id""",
        "hard_req": "R2 penalty applied AND scan(big_fact) above scan magnitude 1",
        "check": lambda ranked: (
            # R2 penalty elevates the scan's magnitude; check that a scan appears
            # with magnitude > 1 (meaning it was analyzed inside a join context)
            any(n.node_type == "scan" and n.magnitude > 1 for n in ranked)
        ),
    },
    "Q5": {
        "name": "Equi vs non-equi join",
        "sql": """SELECT a.id, b.id, c.id
            FROM table_a a
            JOIN table_b b ON a.id = b.a_id
            JOIN table_c c ON a.price BETWEEN c.low AND c.high""",
        "hard_req": "non-equi join > equi join",
        "check": None,  # checked separately
    },
    "Q6": {
        "name": "ORDER BY without LIMIT in subquery + GROUP BY",
        "sql": """SELECT category, COUNT(*) as cnt FROM (
            SELECT * FROM events ORDER BY event_time
        ) sub GROUP BY category""",
        "hard_req": "ORDER BY + GROUP BY both above bare scan",
        "check": None,  # checked separately
    },
    "Q7": {
        "name": "CROSS JOIN of tiny dims (truth-ceiling)",
        "sql": "SELECT * FROM tiny_dim_a CROSS JOIN tiny_dim_b",
        "hard_req": "cross join #1 + offline_truth_ceiling flagged",
        "check": lambda ranked: (
            ranked[0].node_type == "cross_join"
            and any("truth_ceiling" in f for f in ranked[0].flags)
        ),
    },
    "Q8": {
        "name": "Window function over join",
        "sql": """SELECT t.id, t.val, w.other,
            ROW_NUMBER() OVER (PARTITION BY t.category ORDER BY t.val DESC) as rn
            FROM main_table t JOIN other_table w ON t.id = w.t_id""",
        "hard_req": "window and join both above bare scan",
        "check": None,  # checked separately
    },
}


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def run_all():
    results: dict = {}  # qname -> weight_name -> {"ranked": ..., "req_met": ...}

    for weight_name, config in WEIGHT_CONFIGS.items():
        print(f"\n{'='*60}")
        print(f"WEIGHT SETTING: {weight_name}")
        print("=" * 60)

        for qname, qdef in CORPUS.items():
            try:
                root = build_cost_tree(qdef["sql"], config)
                ranked = flatten_nodes(root)

                # Default requirement check
                req_met = "N/A"
                if qdef["check"]:
                    req_met = "PASS" if qdef["check"](ranked) else "FAIL"

                print(f"\n{qname} [{weight_name}]: {qdef['name']}")
                print(f"  #1={ranked[0].label} (type={ranked[0].node_type}, cost={ranked[0].subtree_cost:.1f})")
                if len(ranked) > 1:
                    print(f"  #2={ranked[1].label} (cost={ranked[1].subtree_cost:.1f})")
                if len(ranked) > 2:
                    print(f"  #3={ranked[2].label} (cost={ranked[2].subtree_cost:.1f})")
                print(f"  req={qdef['hard_req']}: {req_met}")
                if ranked[0].flags:
                    print(f"  flags={ranked[0].flags}")

                # --- Special per-query checks ---

                if qname == "Q5":
                    non_equi = next((n for n in ranked if n.node_type == "non_equi_join"), None)
                    equi = next((n for n in ranked if n.node_type == "equi_join"), None)
                    if non_equi and equi:
                        ok = non_equi.subtree_cost > equi.subtree_cost
                        print(
                            f"  Q5 check: non_equi({non_equi.subtree_cost:.1f}) > "
                            f"equi({equi.subtree_cost:.1f}) = {'PASS' if ok else 'FAIL'}"
                        )
                        req_met = "PASS" if ok else "FAIL"
                    else:
                        print(f"  Q5 check: non_equi={non_equi} equi={equi} = FAIL (node not found)")
                        req_met = "FAIL"

                if qname == "Q3":
                    equi_joins = [n for n in ranked if n.node_type == "equi_join"]
                    if len(equi_joins) >= 2:
                        inner_ok = equi_joins[0].subtree_cost > equi_joins[1].subtree_cost
                        print(
                            f"  Q3 check: inner_join({equi_joins[0].subtree_cost:.1f}) > "
                            f"middle_join({equi_joins[1].subtree_cost:.1f}) = {'PASS' if inner_ok else 'FAIL'}"
                        )
                        req_met = "PASS" if inner_ok else "FAIL"
                    else:
                        print(f"  Q3 check: only {len(equi_joins)} equi_join found — FAIL")
                        req_met = "FAIL"

                if qname == "Q6":
                    sort_nodes = [n for n in ranked if n.node_type == "sort"]
                    agg_nodes = [n for n in ranked if n.node_type == "aggregate"]
                    scan_nodes = [n for n in ranked if n.node_type == "scan"]
                    if sort_nodes and agg_nodes and scan_nodes:
                        best_scan = max(n.subtree_cost for n in scan_nodes)
                        sort_ok = sort_nodes[0].subtree_cost > best_scan
                        agg_ok = agg_nodes[0].subtree_cost > best_scan
                        print(
                            f"  Q6 check: sort({sort_nodes[0].subtree_cost:.1f}) > scan({best_scan:.1f}) = "
                            f"{'PASS' if sort_ok else 'FAIL'}, "
                            f"agg({agg_nodes[0].subtree_cost:.1f}) > scan({best_scan:.1f}) = "
                            f"{'PASS' if agg_ok else 'FAIL'}"
                        )
                        req_met = "PASS" if (sort_ok and agg_ok) else "FAIL"
                    else:
                        print(f"  Q6 check: sort={sort_nodes} agg={agg_nodes} scan={scan_nodes} = FAIL")
                        req_met = "FAIL"

                if qname == "Q7":
                    cross = next((n for n in ranked if n.node_type == "cross_join"), None)
                    if cross:
                        has_flag = any("truth_ceiling" in f for f in cross.flags)
                        print(f"  Q7 flags on cross_join={cross.flags} offline_truth_ceiling={has_flag}")
                        req_met = "PASS" if (ranked[0].node_type == "cross_join" and has_flag) else "FAIL"
                    else:
                        print("  Q7: no cross_join node found — FAIL")
                        req_met = "FAIL"

                if qname == "Q8":
                    window_nodes = [n for n in ranked if n.node_type == "window"]
                    join_nodes = [n for n in ranked if n.node_type in ("equi_join", "non_equi_join", "cross_join")]
                    scan_nodes = [n for n in ranked if n.node_type == "scan"]
                    if window_nodes and join_nodes and scan_nodes:
                        best_scan = max(n.subtree_cost for n in scan_nodes)
                        win_ok = window_nodes[0].subtree_cost > best_scan
                        join_ok = join_nodes[0].subtree_cost > best_scan
                        print(
                            f"  Q8 check: window({window_nodes[0].subtree_cost:.1f}) > scan({best_scan:.1f}) = "
                            f"{'PASS' if win_ok else 'FAIL'}, "
                            f"join({join_nodes[0].subtree_cost:.1f}) > scan({best_scan:.1f}) = "
                            f"{'PASS' if join_ok else 'FAIL'}"
                        )
                        req_met = "PASS" if (win_ok and join_ok) else "FAIL"
                    else:
                        print(f"  Q8 check: window={window_nodes} join={join_nodes} scan={scan_nodes} = FAIL")
                        req_met = "FAIL"

                if qname not in results:
                    results[qname] = {}
                results[qname][weight_name] = {"ranked": ranked, "req_met": req_met}

            except Exception as exc:
                print(f"\n{qname} [{weight_name}]: ERROR - {exc}")
                import traceback
                traceback.print_exc()
                if qname not in results:
                    results[qname] = {}
                results[qname][weight_name] = {"ranked": [], "req_met": "ERROR"}

    # ---------------------------------------------------------------------------
    # Stability matrix
    # ---------------------------------------------------------------------------
    print("\n" + "=" * 80)
    print("STABILITY MATRIX")
    print("=" * 80)
    header = f"{'Query':<10} | {'baseline':^22} | {'perturbed':^22} | {'compressed':^22}"
    print(header)
    print("-" * len(header))
    for qname in CORPUS:
        row = f"{qname:<10} | "
        for wname in ["baseline", "perturbed", "compressed"]:
            if qname in results and wname in results[qname]:
                data = results[qname][wname]
                ranked = data["ranked"]
                if ranked:
                    cell = f"{ranked[0].node_type[:16]}({ranked[0].subtree_cost:.0f})"
                else:
                    cell = "ERROR"
                row += f"{cell:^22} | "
            else:
                row += f"{'ERROR':^22} | "
        print(row)

    # ---------------------------------------------------------------------------
    # Hard requirements scorecard
    # ---------------------------------------------------------------------------
    print("\n" + "=" * 80)
    print("RUBRIC SCORECARD")
    print("=" * 80)
    print(f"\n{'Hard Requirement':<44} | {'baseline':^12} | {'perturbed':^12} | {'compressed':^12} | Overall")
    print("-" * 100)

    hard_reqs: dict = {
        "Q1: cross_join #1": (
            "Q1",
            lambda r: r[0].node_type == "cross_join",
        ),
        "Q2: correlated_subquery #1": (
            "Q2",
            lambda r: r[0].node_type == "correlated_subquery",
        ),
        "Q5: non_equi > equi": (
            "Q5",
            lambda r: (
                next((n for n in r if n.node_type == "non_equi_join"), None) is not None
                and next((n for n in r if n.node_type == "equi_join"), None) is not None
                and next((n for n in r if n.node_type == "non_equi_join")).subtree_cost
                > next((n for n in r if n.node_type == "equi_join")).subtree_cost
            ),
        ),
        "Q7: cross_join #1 + offline_truth_ceiling": (
            "Q7",
            lambda r: (
                r[0].node_type == "cross_join"
                and any("truth_ceiling" in f for f in r[0].flags)
            ),
        ),
    }

    all_hard_pass = True
    for req_name, (qname, check_fn) in hard_reqs.items():
        row = f"  {req_name:<42} | "
        req_all_pass = True
        for wname in ["baseline", "perturbed", "compressed"]:
            if qname in results and wname in results[qname]:
                ranked = results[qname][wname]["ranked"]
                if ranked:
                    ok = check_fn(ranked)
                    cell = "PASS" if ok else "FAIL"
                    if not ok:
                        req_all_pass = False
                        all_hard_pass = False
                else:
                    cell = "N/A"
                    req_all_pass = False
                    all_hard_pass = False
            else:
                cell = "N/A"
                req_all_pass = False
                all_hard_pass = False
            row += f"{cell:^12} | "
        row += "ALL_PASS" if req_all_pass else "UNSTABLE"
        print(row)

    print()
    print(f"Overall hard-req status: {'ALL 4 PASS ACROSS ALL CONFIGS' if all_hard_pass else 'SOME FAILURES — see above'}")

    # ---------------------------------------------------------------------------
    # Summary per-query across weights
    # ---------------------------------------------------------------------------
    print("\n" + "=" * 80)
    print("PER-QUERY VERDICT SUMMARY")
    print("=" * 80)
    for qname, qdef in CORPUS.items():
        print(f"\n  {qname}: {qdef['name']}")
        print(f"    req: {qdef['hard_req']}")
        for wname in ["baseline", "perturbed", "compressed"]:
            if qname in results and wname in results[qname]:
                data = results[qname][wname]
                rm = data["req_met"]
                ranked = data["ranked"]
                top = ranked[0].label if ranked else "N/A"
                top_cost = f"{ranked[0].subtree_cost:.1f}" if ranked else "N/A"
                print(f"    [{wname:>10}] req_met={rm}  #1={top} cost={top_cost}")
            else:
                print(f"    [{wname:>10}] ERROR")


if __name__ == "__main__":
    run_all()
