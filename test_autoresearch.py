#!/usr/bin/env python3
"""Direct trino-research test — bypass interactive REPL."""
import sys, os, json, tempfile, subprocess, time, base64, requests
from pathlib import Path

GENIE_DIR = Path("/Users/leeabc/work/emilyorz/genieCLI")
WORKDIR = Path(tempfile.mkdtemp(prefix="trino-test-"))
SQL_FILE = Path("/Users/leeabc/work/emilyorz/trino-optimize-pbb/original_query.sql")
GENIE_PY = str(GENIE_DIR / ".venv/bin/python3")
os.chdir(WORKDIR)

# Git init
subprocess.run(["git", "init", "-q"], check=True)
subprocess.run(["git", "config", "user.email", "test@trino"], check=True)
subprocess.run(["git", "config", "user.name", "trino-test"], check=True)

# Write SQL
query_sql = SQL_FILE.read_text().strip()
(WORKDIR / "query.sql").write_text(query_sql + "\n")
subprocess.run(["git", "add", "-A"], cwd=WORKDIR, check=True)
subprocess.run(["git", "commit", "-q", "-m", "initial"], cwd=WORKDIR, check=True)

# Verify script
verify_py = f"""#!/usr/bin/env python3
import sys, time
sys.path.insert(0, '{GENIE_DIR}')
from genie.skills.trino_query.connection import get_active_profile
from genie.skills.trino_query import _extract_metrics

with open('query.sql') as f:
    sql = f.read().strip()

try:
    import trino.dbapi
    cfg = get_active_profile()
    conn = cfg.connect()
    cur = conn.cursor()
    t0 = time.monotonic()
    cur.execute(sql)
    rows = cur.fetchall()
    wall_ms = int((time.monotonic() - t0) * 1000)
    stats = getattr(cur, 'stats', {{}}) or {{}}
    metrics = _extract_metrics(stats)
    conn.close()
    print(f'rows={{len(rows)}}')
    print(f'wall_ms={{wall_ms}}')
    print(f'cpu_time_ms={{metrics.cpu_time_ms}}')
    print(f'splits={{metrics.total_splits}}')
    print(f'processed_rows={{metrics.processed_rows}}')
    print(f'physical_input={{metrics.physical_input_bytes}}')
    print(f'peak_memory={{metrics.peak_memory_bytes}}')
    print(wall_ms)
except Exception as e:
    print(f'ERROR: {{e}}')
    print(999999)
    sys.exit(1)
"""
(WORKDIR / "verify.py").write_text(verify_py)
os.chmod(WORKDIR / "verify.py", 0o755)

# Baseline measurement
print("── Measuring baseline ──")
r = subprocess.run([GENIE_PY, "verify.py"], cwd=WORKDIR,
                   capture_output=True, text=True, timeout=30)
print(r.stdout)
if r.returncode != 0:
    print(f"Baseline FAILED: {r.stderr[-200:]}")
    sys.exit(1)

baseline_wall = None
for line in r.stdout.strip().split("\n"):
    if line.startswith("wall_ms="):
        baseline_wall = float(line.split("=")[1])

print(f"\nBaseline wall time: {baseline_wall}ms")

# MiniMax API
with open(os.path.expanduser("~/.openclaw/openclaw.json")) as f:
    d = json.load(f)
api_key = d["models"]["providers"]["minimax-portal"]["apiKey"]
base_url = d["models"]["providers"]["minimax-portal"]["baseUrl"]

SYSTEM = """You are optimizing a Trino SQL query in query.sql.
Goal: minimize wall_time_ms (lower is better).
Keep the same result set. Use Trino best practices: CTEs over subqueries, COALESCE not NVL, explicit JOINs.
Reply with your analysis, then apply a patch using:
TOOL_CALL: {"tool": "file_patch", "args": {"path": "query.sql", "patch": "<complete new SQL>"}}"""

def ai_complete(history):
    payload = {
        "model": "MiniMax-M2.7",
        "messages": [{"role": "system", "content": SYSTEM}] + history,
        "stream": False,
    }
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"}
    resp = requests.post(f"{base_url}/chat/completions",
                         headers=headers, json=payload, timeout=90)
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"]

# Optimization loop
history = []
for iteration in range(3):
    print(f"\n=== Iteration {iteration+1} ===")
    current = (WORKDIR / "query.sql").read_text().strip()

    prompt = f"""Current query.sql (first 50 lines):
```sql
{chr(10).join(current.split(chr(10))[:50])}
```

Propose ONE focused optimization. Reply with analysis then TOOL_CALL JSON."""

    history.append({"role": "user", "content": prompt})
    reply = ai_complete(history)
    print(f"AI reply ({len(reply)} chars): {reply[:300]}...")
    history.append({"role": "assistant", "content": reply})

    import re
    m = re.search(r'TOOL_CALL:\s*(\{{.*?}})\s*```?', reply, re.DOTALL)
    if not m:
        m = re.search(r'\(\{\"tool\":\s*"file_patch"', reply)
    if not m:
        print("No tool call found, stopping.")
        break

    try:
        # Extract JSON from reply
        json_str = m.group(1) if m.group(1) else m.group(0).split("TOOL_CALL")[1]
        for start in ['{', '```json', '```']:
            idx = json_str.find(start)
            if idx >= 0:
                json_str = json_str[idx:]
                break
        tc = json.loads(json_str)
        args = tc.get("args", {})
        new_sql = args.get("patch", "")
        if new_sql:
            (WORKDIR / "query.sql").write_text(new_sql + "\n")
            print(f"Patch applied ({len(new_sql)} chars)")
    except Exception as e:
        print(f"Patch failed: {e}")
        continue

    # Verify
    r = subprocess.run([GENIE_PY, "verify.py"], cwd=WORKDIR,
                       capture_output=True, text=True, timeout=30)
    print(f"Verify: {r.stdout[:200]}")
    new_wall = None
    for line in r.stdout.strip().split("\n"):
        if line.startswith("wall_ms="):
            new_wall = float(line.split("=")[1])
    if new_wall:
        delta = (new_wall - baseline_wall) / baseline_wall * 100 if baseline_wall else 0
        print(f"Wall: {baseline_wall}ms → {new_wall}ms ({delta:+.0f}%)")
        if new_wall < baseline_wall:
            subprocess.run(["git", "add", "-A"], cwd=WORKDIR, check=True)
            subprocess.run(["git", "commit", "-q", "-m", f"iter {iteration+1}: {new_wall}ms"], cwd=WORKDIR, check=True)

print(f"\nWorkdir: {WORKDIR}")
