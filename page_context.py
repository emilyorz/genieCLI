"""
page_context.py - Compress raw CDP data into minimal structured context for AI.

Instead of dumping raw DOM/HTML/JSON, this module builds a clean
"page snapshot" that gives AI exactly what it needs:
  - Numbered interactive elements with positions
  - Page title + URL
  - Compressed visible text
  - Key data values (numbers, labels)

AI then operates on element IDs, not selectors.
"""

import json
import re

CDP_URL = "http://localhost:9222"


# ── CDP helper ────────────────────────────────────────────────────────────────

def _get_tab():
    import requests
    tabs  = requests.get(f"{CDP_URL}/json", timeout=3).json()
    pages = [t for t in tabs if t.get("type") == "page"]
    if not pages:
        raise RuntimeError("No Chrome tab found")
    return pages[0]


class _CDP:
    def __init__(self):
        import requests as _req
        import websocket as _ws
        tab      = _get_tab()
        self.ws  = _ws.create_connection(tab["webSocketDebuggerUrl"], timeout=15)
        self._id = 0

    def send(self, method, params=None):
        self._id += 1
        self.ws.send(json.dumps({"id": self._id, "method": method, "params": params or {}}))
        cur = self._id
        while True:
            msg = json.loads(self.ws.recv())
            if msg.get("id") == cur:
                return msg.get("result", {})

    def js_val(self, expr):
        r = self.send("Runtime.evaluate", {
            "expression": expr, "returnByValue": True
        })
        return r.get("result", {}).get("value")

    def close(self):
        self.ws.close()


# ── Element Registry ──────────────────────────────────────────────────────────

# Global registry: element_id -> {selector, x, y, tag, text}
_ELEMENT_REGISTRY = {}
_NEXT_ID = 1


def clear_registry():
    global _ELEMENT_REGISTRY, _NEXT_ID
    _ELEMENT_REGISTRY = {}
    _NEXT_ID = 1


def get_element_by_id(element_id: int):
    return _ELEMENT_REGISTRY.get(element_id)


def _register(tag, text, x, y, selector, role=""):
    global _NEXT_ID
    eid = _NEXT_ID
    _NEXT_ID += 1
    _ELEMENT_REGISTRY[eid] = {
        "id":       eid,
        "tag":      tag,
        "text":     text,
        "x":        x,
        "y":        y,
        "selector": selector,
        "role":     role,
    }
    return eid


# ── Page Snapshot ─────────────────────────────────────────────────────────────

def get_page_snapshot(include_text=True, max_text=600) -> str:
    """
    Build a compressed page snapshot:
    - URL + title
    - Numbered interactive elements (buttons, inputs, links, selects)
    - Visible text summary
    Returns a compact string for AI context.
    """
    cdp = _CDP()
    try:
        url   = cdp.js_val("window.location.href") or ""
        title = cdp.js_val("document.title") or ""

        # Collect interactive elements via JS
        raw = cdp.js_val("""
(function() {
    var results = [];
    var seen    = new Set();

    var selectors = [
        { sel: "button, [role='button']",         type: "BTN"    },
        { sel: "input:not([type=hidden])",         type: "INPUT"  },
        { sel: "textarea",                         type: "TEXTAREA"},
        { sel: "select",                           type: "SELECT" },
        { sel: "a[href]",                          type: "LINK"   },
        { sel: "[role='tab']",                     type: "TAB"    },
        { sel: "[role='menuitem']",                type: "MENU"   },
        { sel: "[role='checkbox'], input[type=checkbox]", type: "CHECKBOX"},
        { sel: "[role='link']",                    type: "LINK"   },
        { sel: "[onclick], [data-click]",          type: "CLICK"  },
    ];

    selectors.forEach(function(item) {
        var els = document.querySelectorAll(item.sel);
        els.forEach(function(el) {
            if (seen.has(el)) return;
            seen.add(el);

            var r = el.getBoundingClientRect();
            // skip invisible elements
            if (r.width === 0 || r.height === 0) return;
            if (r.top < -100 || r.top > window.innerHeight + 100) return;

            var text = (
                el.innerText ||
                el.value ||
                el.placeholder ||
                el.getAttribute("aria-label") ||
                el.getAttribute("title") ||
                el.getAttribute("name") ||
                el.getAttribute("alt") ||
                ""
            ).trim().substring(0, 50);

            var cls = (el.className || "").toString().substring(0, 40);

            results.push({
                type:     item.type,
                text:     text,
                x:        Math.round(r.left + r.width / 2),
                y:        Math.round(r.top  + r.height / 2),
                tag:      el.tagName,
                cls:      cls,
                disabled: el.disabled || false,
            });
        });
    });

    return JSON.stringify(results);
})()
""")

        elements = json.loads(raw) if raw else []

        # Register elements and build display lines
        clear_registry()
        lines = [
            f"[Page] {title}",
            f"[URL]  {url}",
            "",
            "=== Interactive Elements ===",
        ]

        for el in elements:
            if el.get("disabled"):
                continue
            eid = _register(
                tag=el["tag"],
                text=el["text"],
                x=el["x"],
                y=el["y"],
                selector="",
                role=el["type"],
            )
            label = el["text"] or el["cls"] or el["tag"]
            lines.append(
                f"[{eid:3}] {el['type']:<9} {label:<35} at ({el['x']:4},{el['y']:4})"
            )

        # Visible text summary
        if include_text:
            text = cdp.js_val("document.body.innerText") or ""
            text = re.sub(r'\n{3,}', '\n\n', text).strip()
            if text:
                lines += [
                    "",
                    "=== Page Text (summary) ===",
                    text[:max_text] + ("..." if len(text) > max_text else ""),
                ]

        return "\n".join(lines)

    finally:
        cdp.close()


def get_numbers_on_page() -> str:
    """
    Extract all numbers/values visible on page — useful for charts,
    dashboards, metrics where AI needs actual data values.
    """
    cdp = _CDP()
    try:
        raw = cdp.js_val("""
(function() {
    var results = [];
    var seen    = new Set();
    // Target elements likely to contain data values
    var sels = [
        '[class*="value"]', '[class*="Value"]',
        '[class*="metric"]', '[class*="stat"]',
        '[class*="count"]', '[class*="number"]',
        '[class*="label"]', '[class*="legend"]',
        'td', 'th', 'li', 'span', 'p',
    ];
    sels.forEach(function(sel) {
        document.querySelectorAll(sel).forEach(function(el) {
            if (seen.has(el)) return;
            seen.add(el);
            var r = el.getBoundingClientRect();
            if (r.width === 0 || r.height === 0) return;
            var t = (el.innerText || "").trim();
            // Only keep elements with numeric content
            if (/[0-9]/.test(t) && t.length < 100) {
                results.push(t);
            }
        });
    });
    // Deduplicate
    return JSON.stringify([...new Set(results)].slice(0, 80));
})()
""")
        values = json.loads(raw) if raw else []
        if not values:
            return "No numeric values found on page"
        return "Numeric values on page:\n" + "\n".join(values)
    finally:
        cdp.close()


def click_element(element_id: int) -> str:
    """Click a registered element by its ID, then verify something changed."""
    import time
    el = get_element_by_id(element_id)
    if not el:
        return f"Element #{element_id} not found. Run browser_snapshot first."
    cdp = _CDP()
    try:
        # Capture state before click
        url_before   = cdp.js_val("window.location.href") or ""
        title_before = cdp.js_val("document.title") or ""

        # Perform click
        for etype in ["mousePressed", "mouseReleased"]:
            cdp.send("Input.dispatchMouseEvent", {
                "type": etype, "x": el["x"], "y": el["y"],
                "button": "left", "clickCount": 1,
            })
        time.sleep(0.8)

        # Verify: check what changed
        url_after   = cdp.js_val("window.location.href") or ""
        title_after = cdp.js_val("document.title") or ""

        verification = []

        if url_after != url_before:
            verification.append(f"URL changed: {url_before} -> {url_after}")
        if title_after != title_before:
            verification.append(f"Title changed: {title_before} -> {title_after}")

        # Check if element is still in DOM / changed state
        x_j, y_j = str(el["x"]), str(el["y"])
        state = cdp.js_val(
            "(function(){"
            "  var el = document.elementFromPoint(" + x_j + "," + y_j + ");"
            "  if (!el) return 'element gone (page may have changed)';"
            "  return 'element still present: ' + el.tagName + ' ' + (el.innerText||'').trim().substring(0,40);"
            "})()"
        )
        verification.append(state or "no state info")

        status = "VERIFIED" if (url_after != url_before or title_after != title_before) else "SENT (no navigation detected)"
        result = f"Clicked [{element_id}] {el['role']} '{el['text']}' at ({el['x']},{el['y']}) — {status}"
        if verification:
            result += "\nVerification:\n" + "\n".join(f"  - {v}" for v in verification)
        return result
    finally:
        cdp.close()


def type_into_element(element_id: int, text: str) -> str:
    """Type text into a registered input, then verify value was set."""
    el = get_element_by_id(element_id)
    if not el:
        return f"Element #{element_id} not found. Run browser_snapshot first."
    if el["role"] not in ("INPUT", "TEXTAREA"):
        return f"Element #{element_id} is {el['role']}, not an input"
    cdp = _CDP()
    try:
        x, y   = el["x"], el["y"]
        text_j = json.dumps(text)
        x_j, y_j = str(x), str(y)

        # Click to focus
        for etype in ["mousePressed", "mouseReleased"]:
            cdp.send("Input.dispatchMouseEvent", {
                "type": etype, "x": x, "y": y, "button": "left", "clickCount": 1
            })

        # Set value via JS
        cdp.js_val(
            "(function(){"
            "  var el = document.elementFromPoint(" + x_j + "," + y_j + ");"
            "  if (!el) return;"
            "  var iS = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype,'value');"
            "  var tS = Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype,'value');"
            "  var s  = (iS&&iS.set)||(tS&&tS.set);"
            "  if (s) s.call(el," + text_j + ");"
            "  else el.value = " + text_j + ";"
            "  el.dispatchEvent(new Event('input',{bubbles:true}));"
            "  el.dispatchEvent(new Event('change',{bubbles:true}));"
            "})()"
        )

        # Verify: read back the actual value
        actual = cdp.js_val(
            "(function(){"
            "  var el = document.elementFromPoint(" + x_j + "," + y_j + ");"
            "  return el ? (el.value || el.innerText || '') : null;"
            "})()"
        )

        if actual is None:
            return f"Typed into [{element_id}] but could not verify (element gone?)"

        actual_str = str(actual).strip()
        if text in actual_str or actual_str == text:
            return f"Typed into [{element_id}] '{el['text']}' — VERIFIED: value='{actual_str[:80]}'"
        else:
            return (f"Typed into [{element_id}] '{el['text']}' — WARNING: "
                    f"expected '{text[:40]}' but got '{actual_str[:40]}'"
                    f" (React/custom input may need different approach)")
    finally:
        cdp.close()
