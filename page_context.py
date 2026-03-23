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
import sys
import os

# Import shared CDP singleton from skills package
def _get_cdp():
    root = os.path.dirname(os.path.abspath(__file__))
    if root not in sys.path:
        sys.path.insert(0, root)
    from skills._cdp import get_shared_cdp
    return get_shared_cdp()


# ── Element Registry ──────────────────────────────────────────────────────────

# Global registry: element_id -> {id, tag, text, x, y, selector, role, backend_node_id}
_ELEMENT_REGISTRY = {}
_NEXT_ID = 1

# Tracks element hashes from previous snapshot to mark new elements with *
_PREVIOUS_ELEMENT_HASHES: set = set()


def clear_registry():
    global _ELEMENT_REGISTRY, _NEXT_ID
    _ELEMENT_REGISTRY = {}
    _NEXT_ID = 1


def get_element_by_id(element_id: int):
    return _ELEMENT_REGISTRY.get(element_id)


def _element_hash(tag: str, text: str, placeholder: str = "") -> str:
    label = (text or placeholder)[:30]
    return f"{tag}::{label}"


def _format_element_line(eid: int, el: dict, is_new: bool) -> str:
    tag    = el["tag"]
    text   = el["text"] or ""
    depth  = el.get("depth", 0)
    indent = "  " * depth
    prefix = "*" if is_new else ""

    if tag == "input":
        attrs = []
        if el.get("input_type"):
            attrs.append(f'type="{el["input_type"]}"')
        if el.get("placeholder"):
            attrs.append(f'placeholder="{el["placeholder"]}"')
        attrs.append(f'value="{el.get("value", "")}"')
        return f"{indent}{prefix}[{eid}]<{tag} {' '.join(attrs)}>"
    else:
        return f"{indent}{prefix}[{eid}]<{tag}>{text}</{tag}>"


def _register(tag, text, x, y, selector, role="", backend_node_id=0):
    global _NEXT_ID
    eid = _NEXT_ID
    _NEXT_ID += 1
    _ELEMENT_REGISTRY[eid] = {
        "id":              eid,
        "tag":             tag,
        "text":            text,
        "x":               x,
        "y":               y,
        "selector":        selector,
        "role":            role,
        "backend_node_id": backend_node_id,
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
    cdp   = _get_cdp()
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

            var depth = 0;
            var p = el.parentElement;
            while (p && p !== document.body && depth < 10) {
                depth++;
                p = p.parentElement;
            }

            results.push({
                type:        item.type,
                text:        text,
                x:           Math.round(r.left + r.width / 2),
                y:           Math.round(r.top  + r.height / 2),
                tag:         el.tagName.toLowerCase(),
                cls:         cls,
                disabled:    el.disabled || false,
                depth:       depth,
                input_type:  el.getAttribute('type') || '',
                placeholder: el.getAttribute('placeholder') || '',
                value:       (el.value || '').substring(0, 50),
            });
        });
    });

    return JSON.stringify(results);
})()
""")

    elements = json.loads(raw) if raw else []

    # Enrich elements with backendNodeId via CDP DOM.getNodeForLocation
    for el in elements:
        if el.get("disabled"):
            continue
        try:
            node_result = cdp.send("DOM.getNodeForLocation", {
                "x": el["x"],
                "y": el["y"],
                "includeUserAgentShadowDOM": False,
            })
            el["backend_node_id"] = node_result.get("backendNodeId", 0)
        except Exception:
            el["backend_node_id"] = 0

    # Register elements and build display lines
    clear_registry()
    lines = [
        f"[Page] {title}",
        f"[URL]  {url}",
        "",
        "=== Interactive Elements ===",
    ]

    new_hashes: set = set()
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
            backend_node_id=el.get("backend_node_id", 0),
        )
        h      = _element_hash(el["tag"], el["text"], el.get("placeholder", ""))
        is_new = h not in _PREVIOUS_ELEMENT_HASHES
        new_hashes.add(h)
        lines.append(_format_element_line(eid, el, is_new))

    _PREVIOUS_ELEMENT_HASHES.clear()
    _PREVIOUS_ELEMENT_HASHES.update(new_hashes)

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


def get_numbers_on_page() -> str:
    """
    Extract all numbers/values visible on page — useful for charts,
    dashboards, metrics where AI needs actual data values.
    """
    cdp = _get_cdp()
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


def click_element(element_id: int) -> str:
    """Click a registered element by its ID using backendNodeId when available, else coordinates."""
    import time
    el = get_element_by_id(element_id)
    if not el:
        return f"Element #{element_id} not found. Run browser_snapshot first."

    cdp = _get_cdp()

    # Method 1: backendNodeId click (more reliable, avoids coordinate mismatch)
    if el.get("backend_node_id") and el["backend_node_id"] > 0:
        try:
            result    = cdp.send("DOM.resolveNode", {"backendNodeId": el["backend_node_id"]})
            object_id = result.get("object", {}).get("objectId")
            if object_id:
                cdp.send("Runtime.callFunctionOn", {
                    "functionDeclaration": "function() { this.click(); return true; }",
                    "objectId":            object_id,
                    "returnByValue":       True,
                })
                try:
                    cdp.send("Runtime.releaseObject", {"objectId": object_id})
                except Exception:
                    pass
                return f"Clicked [{element_id}] '{el['text']}' via backendNodeId (more reliable)"
        except Exception:
            pass  # fallback to coordinate click

    # Fallback: coordinate click
    url_before = cdp.js_val("window.location.href") or ""
    for etype in ["mousePressed", "mouseReleased"]:
        cdp.send("Input.dispatchMouseEvent", {
            "type": etype, "x": el["x"], "y": el["y"],
            "button": "left", "clickCount": 1,
        })
    time.sleep(0.8)
    url_after = cdp.js_val("window.location.href") or ""
    status    = "navigated" if url_after != url_before else "clicked"
    return f"Clicked [{element_id}] '{el['text']}' at ({el['x']},{el['y']}) via coordinates ({status})"


def type_into_element(element_id: int, text: str) -> str:
    """Type text into a registered input using React-compatible setter, with keystroke fallback."""
    el = get_element_by_id(element_id)
    if not el:
        return f"Element #{element_id} not found. Run browser_snapshot first."
    if el["role"] not in ("INPUT", "TEXTAREA"):
        return f"Element #{element_id} is {el['role']}, not an input"

    cdp    = _get_cdp()
    x_j    = str(el["x"])
    y_j    = str(el["y"])
    text_j = json.dumps(text)

    # Method 1: React-compatible input via native prototype setter
    result = cdp.js_val(
        "(function(){"
        "  var el = document.elementFromPoint(" + x_j + "," + y_j + ");"
        "  if (!el) return 'not found';"
        "  el.focus();"
        "  var iSetter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value');"
        "  var tSetter = Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype, 'value');"
        "  var setter = (iSetter && iSetter.set) || (tSetter && tSetter.set);"
        "  if (setter) {"
        "    setter.call(el, '');"
        "    setter.call(el, " + text_j + ");"
        "  } else {"
        "    el.value = '';"
        "    el.value = " + text_j + ";"
        "  }"
        "  el.dispatchEvent(new Event('input', {bubbles: true}));"
        "  el.dispatchEvent(new Event('change', {bubbles: true}));"
        "  return el.value;"
        "})()"
    )

    if result is None:
        return f"Element at ({el['x']},{el['y']}) not found"

    actual = str(result).strip()

    # Verify value was set
    if text in actual or actual == text:
        return f"Typed into [{element_id}] '{el['text']}' — VERIFIED: value='{actual[:60]}'"

    # Method 2 fallback: keystroke-by-keystroke (for inputs with live validation)
    cdp.js_val(
        "(function(){"
        "  var el = document.elementFromPoint(" + x_j + "," + y_j + ");"
        "  if (el) { el.focus(); el.select(); }"
        "})()"
    )
    cdp.send("Input.dispatchKeyEvent", {"type": "keyDown", "key": "a", "modifiers": 2})
    cdp.send("Input.dispatchKeyEvent", {"type": "keyUp",   "key": "a", "modifiers": 2})

    for char in text:
        cdp.send("Input.dispatchKeyEvent", {"type": "keyDown", "text": char, "key": char})
        cdp.send("Input.dispatchKeyEvent", {"type": "keyUp",   "text": char, "key": char})

    # blur to trigger validation
    cdp.js_val(
        "(function(){"
        "  var el = document.elementFromPoint(" + x_j + "," + y_j + ");"
        "  if (el) el.blur();"
        "})()"
    )

    return f"Typed into [{element_id}] '{el['text']}' via keystrokes (React fallback)"
