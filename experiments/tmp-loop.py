#!/usr/bin/env python3
"""Iterative SQL optimization with cold-cache measurement each round."""
import sys, time, subprocess, re, json, urllib.request
sys.path.insert(0, "/Users/leeabc/work/emilyorz/genieCLI")
import trino.dbapi
from genie.skills.trino_query import _extract_metrics

def restart_trino():
    """Clear query cache by restarting Trino container."""
    subprocess.run(["docker", "restart", "trino"], capture_output=True)
    time.sleep(10)  # wait for Trino to become healthy

def ai(prompt, max_tokens=10000):
    body = json.dumps({
        "model": "qwen3.5:4b",
        "messages": [{"role": "user", "content": prompt}],
        "think": False, "max_tokens": max_tokens, "stream": False
    }).encode()
    req = urllib.request.Request("http://localhost:11434/api/chat", data=body,
                                headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=600) as r:
        resp = json.loads(r.read())
    return resp["message"].get("content") or ""

def extract_sql(raw):
    raw = re.sub(r"```sql\s*", "", raw, flags=re.IGNORECASE)
    raw = re.sub(r"```\s*", "", raw)
    lines = []
    started = False
    for line in raw.split("\n"):
        stripped = line.strip()
        if not stripped: continue
        if not started and re.match(r"SELECT", stripped, re.I):
            started = True
        if started:
            lines.append(stripped)
            if re.search(r"(FETCH\s+(?:FIRST\s+)?LIMIT|LIMIT\s+\d+)", stripped, re.I):
                break
    if not lines: return ""
    sql = " ".join(lines)
    m = re.search(r"(SELECT\s+[\s\S]+?(?:FETCH\s+(?:FIRST\s+)?LIMIT|LIMIT\s+\d+))", sql, re.I)
    return m.group(1).rstrip(";").strip() if m else sql.rstrip(";").strip()

def wait_trino():
    """Poll until Trino is healthy."""
    for _ in range(20):
        try:
            conn = trino.dbapi.connect(host="localhost", port=8085, http_scheme="http",
                                      user="emily", catalog="iceberg", schema="warehouse")
            cur = conn.cursor()
            cur.execute("SELECT 1")
            conn.close()
            return True
        except:
            time.sleep(2)
    return False

def measure(sql):
    restart_trino()
    if not wait_trino():
        return None, "Trino not ready"
    results = []
    for _ in range(3):
        try:
            conn = trino.dbapi.connect(host="localhost", port=8085, http_scheme="http",
                                      user="emily", catalog="iceberg", schema="warehouse")
            cur = conn.cursor()
            cur.execute(sql.strip())
            try: rows = cur.fetchall()
            except: rows = []
            s = cur.stats or {}
            m = _extract_metrics(s)
            results.append({"cpu": m.cpu_time_ms or 0, "wall": m.wall_time_ms or 0,
                          "splits": m.total_splits or 0, "rows": len(rows)})
            conn.close()
        except Exception as e:
            return None, str(e)[:60]
    if not results: return None, "no results"
    cpus = sorted([r["cpu"] for r in results])
    walls = sorted([r["wall"] for r in results])
    return {"cpu": cpus[1], "wall": walls[1], "splits": results[1]["splits"], "rows": results[1]["rows"]}, None

QUERY = """SELECT e.employee_id, e.first_name || ' ' || e.last_name AS full_name,
COALESCE(e.commission_pct, 0) AS commission,
CASE WHEN e.department_id = 10 THEN 'Admin' WHEN e.department_id = 20 THEN 'Marketing'
WHEN e.department_id = 30 THEN 'IT' ELSE 'Other' END AS dept_name,
date_diff('day', e.hire_date, CURRENT_DATE) AS days_employed, d.department_name,
(SELECT COUNT(*) FROM employees_full e2 WHERE e2.manager_id = e.employee_id) AS direct_reports
FROM employees_full e LEFT JOIN departments d ON e.department_id = d.department_id
ORDER BY e.salary DESC FETCH FIRST 100 ROWS ONLY"""

print("=" * 65)
print("  ITERATIVE AUTORESEARCH — cold cache each round")
print("=" * 65)

baseline, err = measure(QUERY)
if err: print("Baseline ERROR:", err); sys.exit(1)
print(f"Baseline (cold): CPU={baseline['cpu']}ms  Wall={baseline['wall']}ms")

current_sql = QUERY
current_cpu = baseline["cpu"]
history = []

for i in range(1, 6):
    print(f"\n  Iteration {i}/5  best={current_cpu}ms")
    hist = ""
    for j, (desc, b, a) in enumerate(history, 1):
        hist += f"  {j}. {desc} ({b}ms -> {a}ms)\n"
    if hist:
        hist = "\nPrevious:\n" + hist
    prompt = (f"[Iter {i}/5] Trino SQL Optimization.\n"
               f"Baseline={baseline['cpu']}ms | Current best={current_cpu}ms{hist}\n"
               f"Current SQL:\n{current_sql}\n\n"
               "Apply ONE more optimization. Reply with ONLY the complete new SQL query.")
    raw = ai(prompt)
    opt = extract_sql(raw)
    if not opt or len(opt) < 30:
        print(f"  SKIP: extract failed, raw_len={len(raw)}")
        continue
    print(f"  SQL: {opt[:60]}...")
    new_m, err = measure(opt)
    if err:
        print(f"  FAIL:", err); continue
    kept = new_m["cpu"] < current_cpu
    if kept:
        old_w = set(re.sub(r'\W+', ' ', current_sql).split())
        new_w = set(re.sub(r'\W+', ' ', opt).split())
        diff = list(new_w - old_w)[:3]
        change = " ".join(diff) if diff else "optimized"
        print(f"  KEPT: {current_cpu}ms -> {new_m['cpu']}ms  change={change[:50]}")
        history.append((change, current_cpu, new_m["cpu"]))
        current_sql = opt
        current_cpu = new_m["cpu"]
    else:
        print(f"  revert: {new_m['cpu']}ms (not better)")

saved = baseline["cpu"] - current_cpu
print(f"\n  {'='*55}")
print(f"  RESULT: {baseline['cpu']}ms -> {current_cpu}ms  (-{saved}ms, {len(history)} changes)")
print(f"  {'='*55}")
for line in current_sql.split("\n"):
    print(f"  {line}")
