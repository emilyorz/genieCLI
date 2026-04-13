#!/usr/bin/env python3
"""Interactive genie tmux session — /trino-research runner.

Uses tmux send-keys to interact with the running GenieCLI session.
Monitor via tmux capture-pane.
"""
import time, subprocess, sys, os

SESSION = "genie-pbb"
SQL_FILE = "/Users/leeabc/work/emilyorz/trino-optimize-pbb/original_query.sql"
LOG = "/tmp/genie-research-log.txt"

def pane():
    r = subprocess.run(["tmux", "capture-pane", "-t", SESSION, "-p"],
                       capture_output=True, text=True)
    return r.stdout

def send(*keys):
    for k in keys:
        subprocess.run(["tmux", "send-keys", "-t", SESSION, str(k)], check=False)
    time.sleep(0.05)

def wait_for(text, timeout=12):
    for _ in range(timeout * 10):
        if text in pane():
            return True
        time.sleep(0.1)
    return False

# Kill any existing session
subprocess.run(["tmux", "kill-session", "-t", SESSION], check=False)

# Start genie WITHOUT redirecting output (so tmux capture-pane works)
subprocess.Popen(
    ["tmux", "new-session", "-d", "-s", SESSION, "-x", "220", "-y", "50"],
    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
)
time.sleep(0.5)

# Start genie cli in the session
subprocess.Popen(
    ["tmux", "send-keys", "-t", SESSION,
     "cd /Users/leeabc/work/emilyorz/genieCLI && .venv/bin/python3 test_startup.py && cat > /dev/null"],
    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
)
time.sleep(5)

# Check startup
p = pane()
print("Pane after startup:")
print(p[-500:])
if "You >" not in p:
    print("ERROR: Genie didn't start properly")
    sys.exit(1)

# Start the log
with open(LOG, "w") as f:
    f.write("")

# Step 1: Send /trino-research
print("\n=== Sending /trino-research ===")
send("/trino-research", "Enter")
time.sleep(2)

# Step 2: Wait for paste mode
if not wait_for("Paste your SQL", timeout=10):
    print("ERROR: Paste prompt not found")
    print(pane()[-500:])
    sys.exit(1)
print("Paste mode active!")

# Step 3: Send SQL
with open(SQL_FILE) as f:
    sql = f.read()
print(f"Sending SQL ({len(sql)} chars)...")
for i, line in enumerate(sql.split("\n")):
    send(line, "Enter")
    if i % 20 == 0:
        time.sleep(0.1)
time.sleep(1)

# Send Ctrl-D to finish paste
print("Sending Ctrl-D...")
send("C-d")
time.sleep(3)

p = pane()
print(f"After Ctrl-D:\n{p[-600:]}")

# Step 4: Metric choice (1 = cpu_time_ms)
if not wait_for("Metric to minimize", timeout=10):
    print("WARNING: Metric prompt not found")
else:
    print("Metric prompt found, sending choice 1...")
    send("1", "Enter")
time.sleep(2)

# Step 5: Max iterations (default = 3 iterations)
if not wait_for("Max iterations", timeout=10):
    print("WARNING: Iterations prompt not found")
else:
    print("Sending max iterations (3)...")
    send("3", "Enter")
time.sleep(2)

# Step 6: Monitor for up to 5 minutes
print("\n=== Monitoring autoresearch (max 5 min) ===")
start = time.time()
last_new_content = start
iteration_count = 0
known_len = len(pane())

while time.time() - start < 300:
    time.sleep(3)
    current_pane = pane()
    current_len = len(current_pane)
    
    # Check for new content
    if current_len > known_len:
        last_new_content = time.time()
        known_len = current_len
        elapsed = int(time.time() - start)
        # Show last few lines
        new_bits = current_pane[-300:]
        print(f"[{elapsed}s] {new_bits.split(chr(10))[-3:]}")
    
    # Detect completion or error
    if any(k in current_pane for k in ["Workdir:", "Journal:", "Final optimized"]):
        print("DONE detected!")
        break
    if "Traceback" in current_pane or "Error:" in current_pane:
        print("ERROR detected!")
        break
    
    # Timeout if no new content for 60s
    if time.time() - last_new_content > 60:
        print(f"No new content for 60s at {int(time.time()-start)}s, stopping monitor")
        break
    
    iteration_count += 1

print("\n=== Final pane ===")
final_pane = pane()
print(final_pane)

with open(LOG, "w") as f:
    f.write(final_pane)
print(f"\nFull log saved to {LOG}")
