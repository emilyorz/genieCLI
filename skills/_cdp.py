"""
skills/_cdp.py - Shared CDP singleton for the session.

All skills use get_shared_cdp() instead of creating/closing connections
per tool call. Connection is lazily created and reused across the session.
Call close_shared_cdp() once at program exit.
"""

import json

CDP_URL = "http://localhost:9222"


def _requests():
    import requests
    return requests


def _websocket():
    import websocket
    return websocket

_shared_ws = None  # module-level singleton


def get_shared_cdp() -> "SharedCDP":
    global _shared_ws
    if _shared_ws is None or not _shared_ws.is_alive():
        _shared_ws = SharedCDP()
        _shared_ws.connect()
    return _shared_ws


def close_shared_cdp():
    global _shared_ws
    if _shared_ws:
        _shared_ws.close()
        _shared_ws = None


class SharedCDP:
    def __init__(self):
        self.ws = None
        self._id = 0

    def connect(self):
        tabs = _requests().get(f"{CDP_URL}/json", timeout=3).json()
        pages = [t for t in tabs if t.get("type") == "page"]
        if not pages:
            raise RuntimeError("No Chrome tab found. Is Chrome running with --remote-debugging-port=9222?")
        self.ws = _websocket().create_connection(pages[0]["webSocketDebuggerUrl"], timeout=15)

    def is_alive(self) -> bool:
        try:
            return self.ws is not None and self.ws.connected
        except Exception:
            return False

    def send(self, method, params=None):
        self._id += 1
        self.ws.send(json.dumps({"id": self._id, "method": method, "params": params or {}}))
        cur = self._id
        while True:
            msg = json.loads(self.ws.recv())
            if msg.get("id") == cur:
                if "error" in msg:
                    raise RuntimeError(f"CDP error: {msg['error']}")
                return msg.get("result", {})

    def js(self, expr, await_promise=False):
        return self.send("Runtime.evaluate", {
            "expression":    expr,
            "returnByValue": True,
            "awaitPromise":  await_promise,
        })

    def js_val(self, expr):
        r = self.send("Runtime.evaluate", {"expression": expr, "returnByValue": True})
        return r.get("result", {}).get("value")

    def get_box(self, selector):
        """Return bounding box {x, y, w, h} of element or None."""
        sel_json = json.dumps(selector)
        raw = self.js_val(
            "(function(){"
            "  var el=document.querySelector(" + sel_json + ");"
            "  if(!el)return null;"
            "  el.scrollIntoView({block:'center',inline:'center'});"
            "  var r=el.getBoundingClientRect();"
            "  return JSON.stringify({x:r.left,y:r.top,w:r.width,h:r.height});"
            "})()"
        )
        return json.loads(raw) if raw else None

    def center_of(self, selector):
        """Return (cx, cy) center of element or None."""
        box = self.get_box(selector)
        if not box:
            return None
        return box["x"] + box["w"] / 2, box["y"] + box["h"] / 2

    def close(self):
        if self.ws:
            try:
                self.ws.close()
            except Exception:
                pass
            self.ws = None
