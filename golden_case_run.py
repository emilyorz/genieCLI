"""v37 golden-case: run the trino_optimize pipeline END-TO-END on LIVE Trino.

Proves the deterministic pipeline (cost via real EXPLAIN, equivalence via real
query results) works against the local Trino (iceberg.warehouse). The LLM
judgment (monster pick + rewrite) is the injected part — scripted here for a
known anti-pattern (redundant-distinct-after-group-by). NOT a unit test; a live
demonstration.
"""
import os, re, json
os.environ['GENIE_MCP_TRINO_URL'] = 'http://localhost:8811/mcp'
os.environ['GENIE_MCP_TRINO_ENABLED'] = 'true'

from genie.skills.mcp_trino.client import McpClient, load_mcp_config
from genie.skills.mcp_trino import research as R
from genie.skills.mcp_trino import cost_reader as CR
from genie.skills.mcp_trino import trino_optimize as T
from genie.skills.trino_query.detection_scan import scan_sql

SQL = "SELECT DISTINCT dept, count(*) AS cnt FROM iceberg.warehouse.employees GROUP BY dept"

c = McpClient(load_mcp_config())
explain_runner = R._build_mcp_explain_runner(c)

def query_runner(sql):
    res = R._execute_via_mcp(c, sql, 30000, "golden")
    return res.get('rows', []) if isinstance(res, dict) else []

def cost_reader_fn(sql):
    return CR.read_cost(sql, explain_runner)

def llm(prompt):  # scripted LLM judgment (the injected part)
    if "select which are 'monsters'" in prompt:
        # a real LLM returns valid double-quoted JSON; mark fragments that carry a
        # rewrite/block finding as monsters (worst-first).
        m = re.search(r'Fragments: (\[.*\])\. Return', prompt, re.DOTALL)
        monsters = []
        if m:
            for fr in json.loads(m.group(1)):
                if any(f.get('action') in ('rewrite', 'block') for f in fr.get('findings', [])):
                    monsters.append(fr['id'])
        return json.dumps(monsters)
    if "Return ONLY the rewritten SQL" in prompt:
        m = re.search(r'SQL:\n(.*?)\n\nReturn ONLY', prompt, re.DOTALL)
        orig = (m.group(1).strip() if m else "")
        # redundant-distinct-after-group-by -> drop the leading DISTINCT
        return re.sub(r'(?i)\bSELECT\s+DISTINCT\b', 'SELECT', orig, count=1)
    return ""

print("=" * 70)
print("v37 GOLDEN CASE — live Trino (localhost:8811 -> 8085, iceberg.warehouse)")
print("=" * 70)
print("ORIGINAL SQL:\n  " + SQL + "\n")

print("--- detection_scan (deterministic, Run A) ---")
for f in scan_sql(SQL):
    print(f"  rule_id={f.rule_id}  action={f.action}  sev={f.severity}")

print("\n--- STAGE 1 baseline (real EXPLAIN cost + row anchor) ---")
b = T.baseline(SQL, explain_runner, query_runner)
print(f"  cost_scalar={b.cost.cost_scalar}  plan_signature={str(b.plan_signature)[:16]}...  row_anchor={b.row_anchor}  available={b.available}")

print("\n--- STAGE 2 decompose (LLM monster hunt, guided by detection_scan + cost) ---")
frags = T.decompose(SQL, llm, cost_reader_fn)
for fr in frags:
    print(f"  frag={fr.fragment_id} role={fr.role} is_monster={fr.is_monster} rank={fr.monster_rank} findings={[(x.rule_id, x.action) for x in fr.findings]}")

print("\n--- STAGE 3 optimize (per fragment, 4-action tier gate) ---")
candidates = [T.optimize(fr, llm) for fr in frags]
for cd in candidates:
    print(f"  frag={cd.fragment_id} action={cd.action} admitted={cd.admitted} changed={cd.changed}")
    if cd.changed:
        print(f"      rewritten: {cd.rewritten_sql}")

print("\n--- STAGE 4 recompose (stitch + cross-fragment re-scan) ---")
rr = T.recompose(SQL, candidates, scan_fn=scan_sql)
print(f"  status={rr.status}  recomposed_sql:\n      {rr.sql}")

print("\n--- STAGE 5 verify (LIVE row-equivalence P0 + cost compare) ---")
vr = T.verify(SQL, rr, query_runner, explain_runner, b.cost.cost_scalar, exclude_column_names=())
print(f"  VERDICT = {vr.verdict}")
print(f"  rows_equivalent={vr.rows_equivalent}  baseline_cost={vr.baseline_cost}  candidate_cost={vr.candidate_cost}")
print(f"  reason: {getattr(vr, 'equiv_diff_reason', None) or getattr(vr, 'unverified_reason', None) or 'n/a'}")

print("\n" + "=" * 70)
print(f"RESULT: original cost={b.cost.cost_scalar} -> candidate cost={vr.candidate_cost} | "
      f"row-equivalent={vr.rows_equivalent} | verdict={vr.verdict}")
print("=" * 70)
