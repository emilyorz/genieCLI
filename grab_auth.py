#!/usr/bin/env python3
import json
import sys
import time
import websocket
import requests
from pathlib import Path

CDP_URL            = "http://localhost:9222"
CONFIG_PATH        = Path.home() / "ai-agent-config.json"
TARGET_URL_KEYWORD = "tgenie"
API_ENDPOINT       = "https://tgenie3-main.mlp.tw.ent.tsmc.com"
FRONTEND_URL       = "https://tgenie3.tgenie.mlp.tw.ent.tsmc.com"
COOKIE_DOMAIN      = ".tsmc.com"
DEFAULT_MODEL      = "gemini-2.5-flash"
SYSTEM_PROMPT      = "You are a helpful AI assistant."

# JS snippets stored as constants to avoid inline escaping issues
JS_CHECK_TEXTAREA = "document.querySelector('[name=\"chat-input-textarea\"]') !== null"

JS_TYPE_HI = "\n".join([
    "(function() {",
    "  var el = document.querySelector('[name=\"chat-input-textarea\"]');",
    "  if (!el) return 'not found';",
    "  el.focus();",
    "  var setter = Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype, 'value').set;",
    "  setter.call(el, 'hi');",
    "  el.dispatchEvent(new Event('input', { bubbles: true }));",
    "  return 'ok';",
    "})()",
])

JS_CLICK_SEND = "\n".join([
    "(function() {",
    "  var btns = document.querySelectorAll('button');",
    "  for (var i = 0; i < btns.length; i++) {",
    "    var cls = btns[i].className || '';",
    "    if (cls.indexOf('rounded-sm') >= 0 && cls.indexOf('text-blue-600') >= 0) {",
    "      btns[i].click();",
    "      return 'clicked: ' + cls.substring(0, 60);",
    "    }",
    "  }",
    "  var s = document.querySelector('button[type=\"submit\"]');",
    "  if (s) { s.click(); return 'clicked submit'; }",
    "  return 'button not found';",
    "})()",
])


def get_tabs():
    try:
        return requests.get(f"{CDP_URL}/json", timeout=5).json()
    except Exception as e:
        print(f"  [ERROR] Cannot connect to Chrome CDP: {e}")
        print()
        print("  Start Chrome with:")
        print("  chrome.exe --remote-debugging-port=9222 "
              "--user-data-dir=C:\\ChromeDebug "
              "--remote-allow-origins=http://localhost:9222")
        sys.exit(1)


def find_tab(tabs, keyword):
    for tab in tabs:
        if keyword in tab.get("url", "").lower():
            return tab
    return None


class CDP:
    def __init__(self, ws_url):
        self.ws = websocket.create_connection(ws_url, timeout=15)
        self._id = 0

    def send(self, method, params=None):
        if params is None:
            params = {}
        self._id += 1
        self.ws.send(json.dumps({"id": self._id, "method": method, "params": params}))
        cur_id = self._id
        while True:
            msg = json.loads(self.ws.recv())
            if msg.get("id") == cur_id:
                if "error" in msg:
                    raise RuntimeError(f"CDP error: {msg['error']}")
                return msg.get("result", {})

    def recv_event(self, timeout=10):
        self.ws.settimeout(timeout)
        try:
            return json.loads(self.ws.recv())
        except Exception:
            return None

    def js(self, expression):
        return self.send("Runtime.evaluate", {
            "expression": expression,
            "returnByValue": True,
        })

    def close(self):
        self.ws.close()


def main():
    print()
    print("  +======================================+")
    print("  |    TGenie Auth Grabber  v2           |")
    print("  |    CDP Network Intercept Mode        |")
    print("  +======================================+")
    print()

    # 1. Find TGenie tab
    print("  Connecting to Chrome CDP...")
    tabs = get_tabs()
    tab  = find_tab(tabs, TARGET_URL_KEYWORD)
    if not tab:
        print(f"  [ERROR] No tab with '{TARGET_URL_KEYWORD}' found. Open tabs:")
        for t in tabs:
            print(f"    - {t.get('url', '')}")
        sys.exit(1)

    print(f"  [OK] Found tab: {tab['url']}")
    cdp = CDP(tab["webSocketDebuggerUrl"])

    # 2. Enable interception
    print("  Setting up network interception...")
    cdp.send("Network.enable")
    cdp.send("Fetch.enable", {
        "patterns": [{"urlPattern": "*oaicompatible*", "requestStage": "Request"}]
    })

    # 3. Check textarea exists
    print("  Finding textarea...")
    result = cdp.js(JS_CHECK_TEXTAREA)
    if not result.get("result", {}).get("value"):
        print("  [ERROR] Textarea not found. Are you on the TGenie chat page?")
        cdp.close()
        sys.exit(1)
    print("  [OK] Found textarea")

    # 4. Type "hi"
    result = cdp.js(JS_TYPE_HI)
    val = result.get("result", {}).get("value", "")
    if val != "ok":
        print(f"  [WARN] Type result: {val}")
    else:
        print("  [OK] Typed 'hi'")
    time.sleep(0.5)

    # 5. Click send button
    result = cdp.js(JS_CLICK_SEND)
    val = result.get("result", {}).get("value", "")
    print(f"  [OK] {val}")

    # 6. Wait for intercepted request
    print("  Waiting for oaicompatible request (max 15s)...")
    token    = None
    deadline = time.time() + 15

    while time.time() < deadline:
        event = cdp.recv_event(timeout=2)
        if not event:
            continue
        if event.get("method") == "Fetch.requestPaused":
            params  = event["params"]
            req_id  = params["requestId"]
            headers = params["request"].get("headers", {})
            auth    = headers.get("authorization") or headers.get("Authorization", "")
            if auth.startswith("Bearer "):
                token = auth[len("Bearer "):]
                print(f"  [OK] Got token: {token[:40]}...")
            cdp.send("Fetch.continueRequest", {"requestId": req_id})
            if token:
                break

    if not token:
        print("  [ERROR] Timeout - could not get token")
        cdp.close()
        sys.exit(1)

    # 7. Grab cookies
    print("  Grabbing cookies...")
    result      = cdp.send("Storage.getCookies")
    all_cookies = result.get("cookies", [])
    if not all_cookies:
        all_cookies = cdp.send("Network.getAllCookies").get("cookies", [])

    keep = [c for c in all_cookies if COOKIE_DOMAIN.lstrip(".") in c.get("domain", "")]
    print(f"  [OK] {len(keep)} cookie(s)")
    for c in keep:
        print(f"       {c['name']} @ {c['domain']}")

    cdp.send("Fetch.disable")
    cdp.close()

    # 8. Save config
    existing = {}
    if CONFIG_PATH.exists():
        try:
            existing = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass

    cfg = {
        "endpoint":     existing.get("endpoint",     API_ENDPOINT),
        "frontendUrl":  existing.get("frontendUrl",  FRONTEND_URL),
        "authToken":    token,
        "customHeader": existing.get("customHeader", ""),
        "defaultModel": existing.get("defaultModel", DEFAULT_MODEL),
        "systemPrompt": existing.get("systemPrompt", SYSTEM_PROMPT),
        "cookies":      [
            {"name": c["name"], "value": c["value"], "domain": c["domain"]}
            for c in keep
        ],
    }
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"  [OK] Config saved: {CONFIG_PATH}")
    print()
    print("  Done! Run: python ai_agent.py")
    print()


if __name__ == "__main__":
    main()
