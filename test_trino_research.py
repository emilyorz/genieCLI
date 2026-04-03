#!/usr/bin/env python3
"""
Send /trino-research interaction to the running genie tmux session.
Flow:
  1. Send /trino-research\n
  2. Read current pane (wait for "Paste your SQL" prompt)
  3. Send SQL lines + Ctrl-D
  4. Send metric choice (1\n)
  5. Send max iterations (\n)
  6. Let autoresearch run, capture progress
"""
import time, subprocess, sys

SESSION = "genie-pbb"
SQL_FILE = "/Users/leeabc/work/emilyorz/trino-optimize-pbb/original_query.sql"
LOG = "/tmp/genie-research-log.txt"

def capture():
    r = subprocess.run(["tmux", "capture-pane", "-t", SESSION, "-p"],
                       capture_output=True, text=True)
    return r.stdout

def send_text(text):
    """Send text as keyboard input"""
    subprocess.run(["tmux", "send-keys", "-t", SESSION] + list(text), check=False)
    time.sleep(0.1)

def send_ctrl_d():
    subprocess.run(["tmux", "send-keys", "-t", SESSION, "C-d"], check=False)
    time.sleep(0.1)

def wait_for(prompt_fragment, timeout=15):
    """Wait until pane contains prompt_fragment"""
    for i in range(timeout * 10):
        pane = capture()
        if prompt_fragment in pane:
            return True
        time.sleep(0.1)
    return False

# Read SQL
with open(SQL_FILE) as f:
    sql = f.read()

# Step 1: Send /trino-research
print("Step 1: Sending /trino-research...")
send_text("/trino-research")
send_text("\n")
time.sleep(1)

# Step 2: Wait for paste prompt
print("Step 2: Waiting for paste prompt...")
if not wait_for("Paste your SQL", timeout=10):
    print("ERROR: Paste prompt not found")
    print(capture()[-500:])
    sys.exit(1)
print("Found paste prompt!")

# Step 3: Send SQL line by line, then Ctrl-D
print(f"Step 3: Sending SQL ({len(sql)} chars)...")
for line in sql.split("\n"):
    send_text(line)
    send_text("\n")
    time.sleep(0.02)
send_ctrl_d()
print("SQL sent, waiting for metric prompt...")
time.sleep(2)

# Step 4: Send metric choice (1 = cpu_time_ms)
print("Step 4: Sending metric choice (1)...")
if not wait_for("Metric to minimize", timeout=10):
    print("WARNING: Metric prompt not found")
    print(capture()[-500:])
send_text("1")
send_text("\n")
time.sleep(1)

# Step 5: Send max iterations (default = 5)
print("Step 5: Sending max iterations (3)...")
if not wait_for("Max iterations", timeout=10):
    print("WARNING: Iterations prompt not found")
send_text("3")
send_text("\n")
time.sleep(2)

# Step 6: Wait for autoresearch to run
print("Step 6: Monitoring autoresearch (60s)...")
for i in range(60):
    pane = capture()
    # Check for key progress indicators
    lines = pane.strip().split("\n")
    last_lines = "\n".join(lines[-5:])
    with open(LOG, "a") as f:
        f.write(f"\n--- tick {i} ---\n")
        f.write(pane[-800:])
    print(f"  [{i:02d}s] {last_lines[:100]}")
    
    # Exit if "Final" or "Workdir" or "improvement" appears (done signal)
    if any(k in pane for k in ["Workdir:", "Journal:", "Final optimized"]):
        print("Detected completion!")
        break
    if "Traceback" in pane:
        print("ERROR detected!")
        break
    time.sleep(1)

print("\n=== Final pane content ===")
with open(LOG) as f:
    content = f.read()
print(content[-3000:])
