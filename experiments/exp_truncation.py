"""
Experiments to diagnose Ollama output truncation.
Run: python3 exp_truncation.py
"""
import json
import time
import urllib.request
import urllib.error
import socket

OLLAMA_HOST = "http://localhost:11434"

with open("e2e_query.sql") as f:
    SQL = f.read()

SHORT_PROMPT = f"Optimize this SQL query for performance. Return only the optimized SQL, no explanation:\n\n{SQL}"

# Longer system prompt mirrors what genieCLI actually sends
SYSTEM_PROMPT = (
    "You are a SQL optimization expert. "
    "When given a SQL query, return ONLY the optimized SQL query with no explanation, "
    "no markdown, no code blocks. Just raw SQL."
)


def chat(model, messages, stream=False, options=None, timeout=300):
    payload = {
        "model": model,
        "messages": messages,
        "stream": stream,
        "think": False,
        "options": options or {"num_predict": -1},
    }
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        f"{OLLAMA_HOST}/api/chat",
        data=data,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read()
    elapsed = time.time() - t0

    if stream:
        # Stream returns newline-delimited JSON
        chunks = []
        for line in raw.decode().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
                chunks.append(obj)
            except Exception:
                pass
        content = "".join(c.get("message", {}).get("content", "") for c in chunks)
        last = chunks[-1] if chunks else {}
        return {
            "content": content,
            "done_reason": last.get("done_reason", "?"),
            "eval_count": last.get("eval_count", "?"),
            "prompt_eval_count": last.get("prompt_eval_count", "?"),
            "elapsed": elapsed,
        }
    else:
        d = json.loads(raw)
        msg = d.get("message", {})
        content = msg.get("content", "")
        return {
            "content": content,
            "done_reason": d.get("done_reason", "?"),
            "eval_count": d.get("eval_count", "?"),
            "prompt_eval_count": d.get("prompt_eval_count", "?"),
            "elapsed": elapsed,
        }


def generate(model, prompt, stream=False, options=None, timeout=300):
    payload = {
        "model": model,
        "prompt": prompt,
        "stream": stream,
        "options": options or {"num_predict": -1},
    }
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        f"{OLLAMA_HOST}/api/generate",
        data=data,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read()
    elapsed = time.time() - t0

    if stream:
        chunks = []
        for line in raw.decode().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
                chunks.append(obj)
            except Exception:
                pass
        content = "".join(c.get("response", "") for c in chunks)
        last = chunks[-1] if chunks else {}
        return {
            "content": content,
            "done_reason": last.get("done_reason", "?"),
            "eval_count": last.get("eval_count", "?"),
            "prompt_eval_count": last.get("prompt_eval_count", "?"),
            "elapsed": elapsed,
        }
    else:
        d = json.loads(raw)
        content = d.get("response", "")
        return {
            "content": content,
            "done_reason": d.get("done_reason", "?"),
            "eval_count": d.get("eval_count", "?"),
            "prompt_eval_count": d.get("prompt_eval_count", "?"),
            "elapsed": elapsed,
        }


def report(label, r):
    content = r["content"]
    print(f"\n{'='*60}")
    print(f"TEST: {label}")
    print(f"  chars={len(content)}  tokens_out={r['eval_count']}  tokens_in={r['prompt_eval_count']}")
    print(f"  done_reason={r['done_reason']}  elapsed={r['elapsed']:.1f}s")
    print(f"  last_200: {repr(content[-200:])}")
    print(f"  first_100: {repr(content[:100])}")


results = {}

# -------------------------------------------------------
# EXP A: qwen3.5:9b /api/chat stream=False num_predict=-1
# -------------------------------------------------------
print("\nRunning EXP A: qwen3.5:9b chat stream=False num_predict=-1")
msgs_simple = [{"role": "user", "content": SHORT_PROMPT}]
try:
    r = chat("qwen3.5:9b", msgs_simple, stream=False, options={"num_predict": -1})
    report("qwen3.5:9b /chat stream=False num_predict=-1", r)
    results["A"] = {"label": "qwen3.5:9b chat stream=False", "chars": len(r["content"]),
                    "done_reason": r["done_reason"], "eval_count": r["eval_count"],
                    "prompt_eval_count": r["prompt_eval_count"], "elapsed": r["elapsed"]}
except Exception as e:
    print(f"  FAILED: {e}")
    results["A"] = {"error": str(e)}

# -------------------------------------------------------
# EXP B: qwen3.5:9b /api/chat stream=True (accumulate)
# -------------------------------------------------------
print("\nRunning EXP B: qwen3.5:9b chat stream=True")
try:
    r = chat("qwen3.5:9b", msgs_simple, stream=True, options={"num_predict": -1})
    report("qwen3.5:9b /chat stream=True", r)
    results["B"] = {"label": "qwen3.5:9b chat stream=True", "chars": len(r["content"]),
                    "done_reason": r["done_reason"], "eval_count": r["eval_count"],
                    "prompt_eval_count": r["prompt_eval_count"], "elapsed": r["elapsed"]}
except Exception as e:
    print(f"  FAILED: {e}")
    results["B"] = {"error": str(e)}

# -------------------------------------------------------
# EXP C: qwen3.5:9b /api/generate stream=True
# -------------------------------------------------------
print("\nRunning EXP C: qwen3.5:9b generate stream=True")
try:
    r = generate("qwen3.5:9b", SHORT_PROMPT, stream=True, options={"num_predict": -1})
    report("qwen3.5:9b /generate stream=True", r)
    results["C"] = {"label": "qwen3.5:9b generate stream=True", "chars": len(r["content"]),
                    "done_reason": r["done_reason"], "eval_count": r["eval_count"],
                    "prompt_eval_count": r["prompt_eval_count"], "elapsed": r["elapsed"]}
except Exception as e:
    print(f"  FAILED: {e}")
    results["C"] = {"error": str(e)}

# -------------------------------------------------------
# EXP D: qwen3.5:9b /api/chat num_ctx=8192 explicit
# -------------------------------------------------------
print("\nRunning EXP D: qwen3.5:9b chat num_ctx=8192 num_predict=-1")
try:
    r = chat("qwen3.5:9b", msgs_simple, stream=False, options={"num_predict": -1, "num_ctx": 8192})
    report("qwen3.5:9b /chat num_ctx=8192", r)
    results["D"] = {"label": "qwen3.5:9b chat num_ctx=8192", "chars": len(r["content"]),
                    "done_reason": r["done_reason"], "eval_count": r["eval_count"],
                    "prompt_eval_count": r["prompt_eval_count"], "elapsed": r["elapsed"]}
except Exception as e:
    print(f"  FAILED: {e}")
    results["D"] = {"error": str(e)}

# -------------------------------------------------------
# EXP E: gemma4:e4b /api/chat stream=True
# -------------------------------------------------------
print("\nRunning EXP E: gemma4:e4b chat stream=True")
try:
    r = chat("gemma4:e4b", msgs_simple, stream=True, options={"num_predict": -1})
    report("gemma4:e4b /chat stream=True", r)
    results["E"] = {"label": "gemma4:e4b chat stream=True", "chars": len(r["content"]),
                    "done_reason": r["done_reason"], "eval_count": r["eval_count"],
                    "prompt_eval_count": r["prompt_eval_count"], "elapsed": r["elapsed"]}
except Exception as e:
    print(f"  FAILED: {e}")
    results["E"] = {"error": str(e)}

# -------------------------------------------------------
# EXP F: gemma4:e4b with num_ctx=8192
# -------------------------------------------------------
print("\nRunning EXP F: gemma4:e4b chat num_ctx=8192")
try:
    r = chat("gemma4:e4b", msgs_simple, stream=False, options={"num_predict": -1, "num_ctx": 8192})
    report("gemma4:e4b /chat num_ctx=8192", r)
    results["F"] = {"label": "gemma4:e4b chat num_ctx=8192", "chars": len(r["content"]),
                    "done_reason": r["done_reason"], "eval_count": r["eval_count"],
                    "prompt_eval_count": r["prompt_eval_count"], "elapsed": r["elapsed"]}
except Exception as e:
    print(f"  FAILED: {e}")
    results["F"] = {"error": str(e)}

# -------------------------------------------------------
# EXP G: qwen3.5:4b baseline (known working)
# -------------------------------------------------------
print("\nRunning EXP G: qwen3.5:4b chat stream=False (baseline)")
try:
    r = chat("qwen3.5:4b", msgs_simple, stream=False, options={"num_predict": -1})
    report("qwen3.5:4b /chat stream=False (baseline)", r)
    results["G"] = {"label": "qwen3.5:4b chat stream=False", "chars": len(r["content"]),
                    "done_reason": r["done_reason"], "eval_count": r["eval_count"],
                    "prompt_eval_count": r["prompt_eval_count"], "elapsed": r["elapsed"]}
except Exception as e:
    print(f"  FAILED: {e}")
    results["G"] = {"error": str(e)}

# -------------------------------------------------------
# EXP H: Two-step approach with qwen3.5:9b
# Step 1: identify optimizations; Step 2: generate SQL
# -------------------------------------------------------
print("\nRunning EXP H: Two-step with qwen3.5:9b")
try:
    step1_msgs = [
        {"role": "user", "content": (
            f"List the specific SQL optimizations needed for this query (max 5 bullet points, be very brief):\n\n{SQL}"
        )}
    ]
    r1 = chat("qwen3.5:9b", step1_msgs, stream=False, options={"num_predict": 512})
    optimizations = r1["content"]
    print(f"  Step1: {len(optimizations)} chars, done_reason={r1['done_reason']}")

    step2_msgs = [
        {"role": "user", "content": (
            f"Apply these optimizations to the SQL query. Return ONLY the optimized SQL, no explanation:\n\n"
            f"Optimizations:\n{optimizations}\n\nOriginal SQL:\n{SQL}"
        )}
    ]
    r2 = chat("qwen3.5:9b", step2_msgs, stream=False, options={"num_predict": -1})
    report("qwen3.5:9b two-step (step2 SQL output)", r2)
    results["H"] = {
        "label": "qwen3.5:9b two-step", "chars": len(r2["content"]),
        "done_reason": r2["done_reason"], "eval_count": r2["eval_count"],
        "prompt_eval_count": r2["prompt_eval_count"], "elapsed": r1["elapsed"] + r2["elapsed"],
        "step1_chars": len(optimizations)
    }
except Exception as e:
    print(f"  FAILED: {e}")
    results["H"] = {"error": str(e)}

# -------------------------------------------------------
# Summary
# -------------------------------------------------------
print("\n" + "="*60)
print("SUMMARY")
print("="*60)
for k, v in sorted(results.items()):
    if "error" in v:
        print(f"  {k}: ERROR - {v['error']}")
    else:
        print(f"  {k}: {v['label']}")
        print(f"     chars={v['chars']}  done_reason={v['done_reason']}  tokens_out={v.get('eval_count','?')}  tokens_in={v.get('prompt_eval_count','?')}  elapsed={v.get('elapsed',0):.1f}s")

print("\n\nFULL RESULTS JSON:")
print(json.dumps(results, indent=2))
