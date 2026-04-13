"""genie/skills/browser/tools.py — Browser skill classes (CDP-based).

All classes migrated from the legacy skills/browser.py and skills/context.py.
No sys.path manipulation — imports use genie-native paths.
"""
from __future__ import annotations

import base64
import json
import time

from genie.core.arg import Arg
from genie.core.registry import BaseSkill


def _cdp():
    from genie.skills.browser.cdp import get_shared_cdp
    return get_shared_cdp()


def _requests():
    import requests
    return requests


CDP_URL = "http://localhost:9222"


# ── Tab Management ────────────────────────────────────────────────────────────

class BrowserListTabs(BaseSkill):
    name        = "browser_list_tabs"
    description = "List all open browser tabs with their index, title and URL"
    group       = "navigation"
    tier        = "core"
    args: list  = []

    def run(self):
        tabs  = _requests().get(f"{CDP_URL}/json", timeout=3).json()
        pages = [t for t in tabs if t.get("type") == "page"]
        lines = []
        for i, t in enumerate(pages):
            lines.append(f"[{i}] id={t.get('id','')} | {t.get('title','')[:40]} | {t.get('url','')[:60]}")
        return "\n".join(lines) if lines else "No tabs found"


class BrowserSwitchTab(BaseSkill):
    name        = "browser_switch_tab"
    description = "Switch to a tab by index (from browser_list_tabs) or by URL keyword"
    group       = "navigation"
    tier        = "core"
    args        = [
        Arg(name="index", type="int",
            description="Tab index number (from browser_list_tabs)",
            required=False, default=None),
        Arg(name="keyword", type="str",
            description="URL or title keyword to match (alternative to index)",
            required=False, default=""),
    ]

    def run(self, index=None, keyword=""):
        tabs  = _requests().get(f"{CDP_URL}/json", timeout=3).json()
        pages = [t for t in tabs if t.get("type") == "page"]
        if not pages:
            return "No tabs found"
        if index is not None:
            tab = pages[int(index)]
        elif keyword:
            tab = next((t for t in pages
                        if keyword.lower() in t.get("url","").lower()
                        or keyword.lower() in t.get("title","").lower()), None)
            if not tab:
                return f"No tab matching: {keyword}"
        else:
            return "Provide index or keyword"
        cdp = _cdp()
        cdp.send("Target.activateTarget", {"targetId": tab["id"]})
        return f"Switched to: {tab.get('title','')} | {tab.get('url','')}"


class BrowserNavigate(BaseSkill):
    name        = "browser_navigate"
    description = "Open a URL in a new browser tab"
    group       = "navigation"
    tier        = "core"
    args        = [
        Arg(name="url", type="str",
            description="The URL to open",
            required=True),
    ]

    def run(self, url=""):
        cdp = _cdp()
        cdp.send("Target.createTarget", {"url": url})
        time.sleep(2)
        tabs  = _requests().get(f"{CDP_URL}/json", timeout=3).json()
        ntab  = next((t for t in tabs
                      if t.get("type") == "page" and url in t.get("url", "")), None)
        title = ntab.get("title", "") if ntab else ""
        return f"Opened: {url} (title: {title})"


class BrowserGetURL(BaseSkill):
    name        = "browser_get_url"
    description = "Get the current page URL and title"
    group       = "navigation"
    tier        = "core"
    args: list  = []

    def run(self):
        cdp = _cdp()
        return f"URL: {cdp.js_val('window.location.href')}\nTitle: {cdp.js_val('document.title')}"


# ── Reading ───────────────────────────────────────────────────────────────────

class BrowserGetText(BaseSkill):
    name        = "browser_get_text"
    description = "Get all visible text content of the current page"
    group       = "reading"
    tier        = "core"
    args: list  = []

    def run(self):
        cdp   = _cdp()
        title = cdp.js_val("document.title") or "(no title)"
        url   = cdp.js_val("window.location.href") or "(no URL)"
        text  = cdp.js_val("document.body.innerText") or ""
        return f"[Page: {title}]\n[URL: {url}]\n\n{text.strip()[:4000]}"


class BrowserGetElement(BaseSkill):
    name        = "browser_get_element"
    description = "Get text, value, or any attribute from elements matching a CSS selector"
    group       = "reading"
    tier        = "extended"
    args        = [
        Arg(name="selector", type="str",
            description="CSS selector (returns ALL matching elements)",
            required=True),
        Arg(name="attribute", type="str",
            description="What to get: text, value, innerHTML, href, src, or attribute name",
            required=False, default="text"),
    ]

    def run(self, selector="", attribute="text"):
        cdp      = _cdp()
        sel_json = json.dumps(selector)
        attr_map = {
            "text":      "el.innerText",
            "value":     "el.value",
            "innerHTML": "el.innerHTML",
            "href":      "el.href",
            "src":       "el.src",
        }
        attr_js = attr_map.get(attribute, f"el.getAttribute({json.dumps(attribute)})")
        val = cdp.js_val(
            "(function(){"
            "  var els=document.querySelectorAll(" + sel_json + ");"
            "  if(!els.length) return 'not found: " + selector + "';"
            "  var r=[];"
            "  els.forEach(function(el,i){"
            "    var v=" + attr_js + ";"
            "    if(v!=null) r.push('['+i+'] '+String(v).trim().substring(0,300));"
            "  });"
            "  return r.join('\\n')||'all empty';"
            "})()"
        )
        return val or "not found"


class BrowserGetBoundingBox(BaseSkill):
    name        = "browser_get_bounding_box"
    description = "Get position and size of an element — useful before clicking or screenshotting a region"
    group       = "reading"
    tier        = "extended"
    args        = [
        Arg(name="selector", type="str",
            description="CSS selector",
            required=True),
    ]

    def run(self, selector=""):
        cdp = _cdp()
        box = cdp.get_box(selector)
        if not box:
            return f"Element not found: {selector}"
        return (f"x={box['x']:.0f}, y={box['y']:.0f}, "
                f"width={box['w']:.0f}, height={box['h']:.0f}, "
                f"center=({box['x']+box['w']/2:.0f}, {box['y']+box['h']/2:.0f})")


class BrowserGetLocalStorage(BaseSkill):
    name        = "browser_get_local_storage"
    description = "Read localStorage or sessionStorage values (tokens, state, cached data)"
    group       = "reading"
    tier        = "full"
    args        = [
        Arg(name="key", type="str",
            description="Specific key to get (omit to list all keys)",
            required=False, default=""),
        Arg(name="storage", type="str",
            description="localStorage or sessionStorage",
            required=False, default="localStorage",
            choices=["localStorage", "sessionStorage"]),
    ]

    def run(self, key="", storage="localStorage"):
        cdp = _cdp()
        s = storage if storage in ("localStorage", "sessionStorage") else "localStorage"
        if key:
            val = cdp.js_val(f"{s}.getItem({json.dumps(key)})")
            return f"{key} = {val}" if val is not None else f"Key not found: {key}"
        else:
            result = cdp.js_val(
                "(function(){"
                "  var s=" + s + ";"
                "  var r=[];"
                "  for(var i=0;i<s.length;i++){"
                "    var k=s.key(i);"
                "    var v=s.getItem(k);"
                "    r.push(k+' = '+(v||'').substring(0,80));"
                "  }"
                "  return r.join('\\n')||'(empty)';"
                "})()"
            )
            return result or "(empty)"


class BrowserGetDOM(BaseSkill):
    name        = "browser_get_dom"
    description = "Inspect page DOM — find elements by selector, keyword in class/text, or list interactive elements"
    group       = "reading"
    tier        = "extended"
    args        = [
        Arg(name="query", type="str",
            description="CSS selector, keyword, or special: 'buttons', 'inputs', 'links', 'canvas', 'class:xxx'",
            required=True),
    ]

    def run(self, query=""):
        cdp = _cdp()
        q = query.strip()
        specials = {
            "buttons": "button, [role='button'], input[type='submit'], input[type='button']",
            "inputs":  "input, textarea, select",
            "links":   "a[href]",
            "canvas":  "canvas, svg",
        }

        if q.lower() in specials:
            sel  = specials[q.lower()]
            attr = "href" if q.lower() == "links" else "text"
            return BrowserGetElement().run(selector=sel, attribute=attr)

        elif q.lower().startswith("class:"):
            kw      = q[6:].strip()
            kw_json = json.dumps(kw.lower())
            result  = cdp.js_val(
                "(function(){"
                "  var kw=" + kw_json + ";"
                "  var found=new Set();"
                "  for(var el of document.querySelectorAll('*')){"
                "    var cls=(el.className||'').toString().toLowerCase();"
                "    if(cls.includes(kw)){"
                "      var c=cls.split(' ').find(function(c){return c.includes(kw);});"
                "      if(c) found.add(el.tagName+'.'+c);"
                "    }"
                "    if(found.size>=30)break;"
                "  }"
                "  return found.size?Array.from(found).join('\\n'):'Not found';"
                "})()"
            )
            return result

        else:
            sel_json = json.dumps(q)
            result   = cdp.js_val(
                "(function(){"
                "  var q=" + sel_json + ";"
                "  var bySelector=[];"
                "  try {"
                "    var els=document.querySelectorAll(q);"
                "    els.forEach(function(el,i){"
                "      if(i>=15)return;"
                "      var r=el.getBoundingClientRect();"
                "      bySelector.push(el.tagName"
                "        +' class=\"'+(el.className||'').toString().substring(0,50)+'\"'"
                "        +' text=\"'+(el.innerText||'').trim().substring(0,60)+'\"'"
                "        +' at('+Math.round(r.left)+','+Math.round(r.top)+')');"
                "    });"
                "  } catch(e) {}"
                "  if(bySelector.length) return bySelector.join('\\n');"
                "  var kw=q.toLowerCase();"
                "  var found=[];"
                "  for(var el of document.querySelectorAll('*')){"
                "    var cls=(el.className||'').toString().toLowerCase();"
                "    var txt=(el.innerText||'').toLowerCase();"
                "    if(cls.includes(kw)||txt.includes(kw)){"
                "      found.push(el.tagName+' class=\"'+(el.className||'').toString().substring(0,50)+'\"'"
                "        +' text=\"'+(el.innerText||'').trim().substring(0,60)+'\"');"
                "      if(found.length>=15)break;"
                "    }"
                "  }"
                "  return found.length?found.join('\\n'):'Nothing found for: '+q;"
                "})()"
            )
            return result or "No result"


class BrowserInterceptXHR(BaseSkill):
    name        = "browser_intercept_xhr"
    description = "Capture XHR/fetch API responses by temporarily hooking the page network calls. Useful for reading chart data, API responses that aren't visible in DOM."
    group       = "reading"
    tier        = "full"
    args        = [
        Arg(name="url_keyword", type="str",
            description="Keyword to match in request URL (e.g. 'query', 'api', 'data')",
            required=False, default=""),
        Arg(name="action_js", type="str",
            description="JS to trigger the request (e.g. click a button). Leave empty to just wait.",
            required=False, default=""),
        Arg(name="timeout", type="int",
            description="Seconds to wait for response",
            required=False, default=8),
    ]

    def run(self, url_keyword="", action_js="", timeout=8):
        cdp     = _cdp()
        kw_json = json.dumps(url_keyword)
        cdp.js(
            "(function(){"
            "  window.__intercepted__ = [];"
            "  var kw=" + kw_json + ";"
            "  var origFetch=window.fetch;"
            "  window.fetch=function(url,opts){"
            "    return origFetch(url,opts).then(function(r){"
            "      if(!kw||String(url).includes(kw)){"
            "        r.clone().text().then(function(t){"
            "          window.__intercepted__.push({url:String(url),body:t.substring(0,2000)});"
            "        });"
            "      }"
            "      return r;"
            "    });"
            "  };"
            "  var origOpen=XMLHttpRequest.prototype.open;"
            "  var origSend=XMLHttpRequest.prototype.send;"
            "  XMLHttpRequest.prototype.open=function(m,u){"
            "    this.__url__=u; return origOpen.apply(this,arguments);"
            "  };"
            "  XMLHttpRequest.prototype.send=function(){"
            "    var self=this;"
            "    this.addEventListener('load',function(){"
            "      if(!kw||String(self.__url__).includes(kw)){"
            "        window.__intercepted__.push({url:String(self.__url__),body:(self.responseText||'').substring(0,2000)});"
            "      }"
            "    });"
            "    return origSend.apply(this,arguments);"
            "  };"
            "})()"
        )
        if action_js:
            cdp.js(action_js)
        deadline = time.time() + float(timeout)
        while time.time() < deadline:
            time.sleep(0.5)
            count = cdp.js_val("window.__intercepted__.length")
            if count and int(count) > 0:
                break
        results = cdp.js_val("JSON.stringify(window.__intercepted__ || [])")
        cdp.js("delete window.__intercepted__")
        if not results or results == "[]":
            return f"No responses captured matching '{url_keyword}'. Try a different keyword or wait longer."
        captured = json.loads(results)
        lines = []
        for i, r in enumerate(captured[:5]):
            lines.append(f"[{i}] URL: {r['url']}\nBody: {r['body'][:500]}")
        return f"Captured {len(captured)} response(s):\n\n" + "\n---\n".join(lines)


# ── Interaction helpers ───────────────────────────────────────────────────────

def _resolve_coords(cdp, selector="", x=None, y=None):
    if selector:
        pt = cdp.center_of(selector)
        if not pt:
            return f"Element not found: {selector}"
        return pt
    elif x is not None and y is not None:
        return (float(x), float(y))
    else:
        return "Provide selector or x,y"


def _mouse_click(cdp, cx, cy, button="left", click_count=1, repeat=1):
    for _ in range(repeat):
        for etype in ["mousePressed", "mouseReleased"]:
            cdp.send("Input.dispatchMouseEvent", {
                "type": etype, "x": cx, "y": cy,
                "button": button, "clickCount": click_count,
            })
        if repeat > 1:
            time.sleep(0.05)


_CLICK_ARGS = [
    Arg(name="selector", type="str",
        description="CSS selector (optional if using x,y)",
        required=False, default=""),
    Arg(name="x", type="float",
        description="X coordinate (optional if using selector)",
        required=False, default=None),
    Arg(name="y", type="float",
        description="Y coordinate (optional if using selector)",
        required=False, default=None),
]


# ── Interaction ───────────────────────────────────────────────────────────────

class BrowserClick(BaseSkill):
    name        = "browser_click"
    description = "Single click on an element (CSS selector) or at x,y coordinates. Advanced — prefer browser_click_element with snapshot IDs."
    group       = "interaction"
    tier        = "extended"
    args        = list(_CLICK_ARGS)

    def run(self, selector="", x=None, y=None):
        cdp    = _cdp()
        result = _resolve_coords(cdp, selector, x, y)
        if isinstance(result, str):
            return result
        cx, cy = result
        url_before = cdp.js_val("window.location.href") or ""
        _mouse_click(cdp, cx, cy, button="left", click_count=1)
        time.sleep(0.5)
        url_after = cdp.js_val("window.location.href") or ""
        nav = f" -> navigated to {url_after}" if url_after != url_before else " (no navigation)"
        return f"Clicked at ({cx:.0f}, {cy:.0f}){nav}"


class BrowserDoubleClick(BaseSkill):
    name        = "browser_double_click"
    description = "Double-click on an element or coordinates"
    group       = "interaction"
    tier        = "extended"
    args        = list(_CLICK_ARGS)

    def run(self, selector="", x=None, y=None):
        cdp    = _cdp()
        result = _resolve_coords(cdp, selector, x, y)
        if isinstance(result, str):
            return result
        cx, cy = result
        _mouse_click(cdp, cx, cy, button="left", click_count=2, repeat=2)
        return f"Double-clicked at ({cx:.0f}, {cy:.0f})"


class BrowserRightClick(BaseSkill):
    name        = "browser_right_click"
    description = "Right-click to open context menu on an element or coordinates"
    group       = "interaction"
    tier        = "full"
    args        = list(_CLICK_ARGS)

    def run(self, selector="", x=None, y=None):
        cdp    = _cdp()
        result = _resolve_coords(cdp, selector, x, y)
        if isinstance(result, str):
            return result
        cx, cy = result
        _mouse_click(cdp, cx, cy, button="right", click_count=1)
        time.sleep(0.3)
        return f"Right-clicked at ({cx:.0f}, {cy:.0f})"


class BrowserDrag(BaseSkill):
    name        = "browser_drag"
    description = "Drag from one point to another — for sliders, drag-and-drop, resizing"
    group       = "interaction"
    tier        = "full"
    args        = [
        Arg(name="from_selector", type="str",
            description="CSS selector of element to drag from (optional)",
            required=False, default=""),
        Arg(name="to_selector", type="str",
            description="CSS selector of drop target (optional)",
            required=False, default=""),
        Arg(name="from_x", type="float",
            description="Start X coordinate",
            required=False, default=None),
        Arg(name="from_y", type="float",
            description="Start Y coordinate",
            required=False, default=None),
        Arg(name="to_x", type="float",
            description="End X coordinate",
            required=False, default=None),
        Arg(name="to_y", type="float",
            description="End Y coordinate",
            required=False, default=None),
        Arg(name="steps", type="int",
            description="Smoothness steps",
            required=False, default=10),
    ]

    def run(self, from_selector="", to_selector="", from_x=None, from_y=None,
            to_x=None, to_y=None, steps=10):
        cdp = _cdp()
        if from_selector:
            pt = cdp.center_of(from_selector)
            if not pt:
                return f"Source not found: {from_selector}"
            fx, fy = pt
        else:
            fx, fy = float(from_x), float(from_y)
        if to_selector:
            pt = cdp.center_of(to_selector)
            if not pt:
                return f"Target not found: {to_selector}"
            tx, ty = pt
        else:
            tx, ty = float(to_x), float(to_y)
        steps = int(steps)
        cdp.send("Input.dispatchMouseEvent", {
            "type": "mousePressed", "x": fx, "y": fy, "button": "left", "clickCount": 1
        })
        for i in range(1, steps + 1):
            ix = fx + (tx - fx) * i / steps
            iy = fy + (ty - fy) * i / steps
            cdp.send("Input.dispatchMouseEvent", {
                "type": "mouseMoved", "x": ix, "y": iy, "button": "left"
            })
            time.sleep(0.02)
        cdp.send("Input.dispatchMouseEvent", {
            "type": "mouseReleased", "x": tx, "y": ty, "button": "left", "clickCount": 1
        })
        return f"Dragged from ({fx:.0f},{fy:.0f}) to ({tx:.0f},{ty:.0f})"


class BrowserType(BaseSkill):
    name        = "browser_type"
    description = "Type text into an input, textarea, or contenteditable element. Advanced — prefer browser_type_element with snapshot IDs."
    group       = "interaction"
    tier        = "extended"
    args        = [
        Arg(name="selector", type="str",
            description="CSS selector of the field",
            required=True),
        Arg(name="text", type="str",
            description="Text to type",
            required=True),
        Arg(name="clear", type="bool",
            description="Clear existing value first: true or false",
            required=False, default=True),
    ]

    def run(self, selector="", text="", clear=True):
        cdp       = _cdp()
        sel_json  = json.dumps(selector)
        text_json = json.dumps(text)
        clr       = "true" if str(clear).lower() != "false" else "false"
        val = cdp.js_val(
            "(function(){"
            "  var el=document.querySelector(" + sel_json + ");"
            "  if(!el) return 'not found';"
            "  el.focus();"
            "  if(" + clr + "){"
            "    var iS=Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype,'value');"
            "    var tS=Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype,'value');"
            "    var setter=(iS&&iS.set)||(tS&&tS.set);"
            "    if(setter) setter.call(el,'');"
            "    else el.value='';"
            "  }"
            "  var iS=Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype,'value');"
            "  var tS=Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype,'value');"
            "  var setter=(iS&&iS.set)||(tS&&tS.set);"
            "  if(setter) setter.call(el," + text_json + ");"
            "  else el.value=" + text_json + ";"
            "  el.dispatchEvent(new Event('input',{bubbles:true}));"
            "  el.dispatchEvent(new Event('change',{bubbles:true}));"
            "  return 'ok: '+el.tagName;"
            "})()"
        )
        actual = cdp.js_val(
            "(function(){"
            "  var el=document.querySelector(" + sel_json + ");"
            "  return el?(el.value||el.innerText||''):null;"
            "})()"
        )
        if actual is None:
            return f"Type result: {val} (could not verify)"
        actual_s = str(actual).strip()[:60]
        if str(text) in actual_s or actual_s == str(text):
            return f"Type result: {val} — VERIFIED: value='{actual_s}'"
        return f"Type result: {val} — WARNING: got '{actual_s}' (may need different approach)"


class BrowserKeyboard(BaseSkill):
    name        = "browser_keyboard"
    description = "Press keyboard keys: Enter, Tab, Escape, ArrowUp, ArrowDown, ArrowLeft, ArrowRight, Backspace, Space, F5, or ctrl+a, ctrl+c, ctrl+v etc."
    group       = "interaction"
    tier        = "extended"
    args        = [
        Arg(name="key", type="str",
            description="Key or combo e.g. 'Enter', 'Tab', 'ctrl+a', 'ctrl+v'",
            required=False, default="Enter"),
    ]

    def run(self, key="Enter"):
        cdp      = _cdp()
        parts    = key.lower().split("+")
        main_key = parts[-1].capitalize()
        mods     = 0
        if "ctrl" in parts:  mods |= 2
        if "shift" in parts: mods |= 8
        if "alt" in parts:   mods |= 1
        for etype in ["keyDown", "keyUp"]:
            cdp.send("Input.dispatchKeyEvent", {
                "type":      etype,
                "key":       main_key,
                "modifiers": mods,
            })
        return f"Pressed: {key}"


class BrowserHover(BaseSkill):
    name        = "browser_hover"
    description = "Hover mouse over an element or coordinates to trigger tooltips, dropdowns, or hover effects"
    group       = "interaction"
    tier        = "extended"
    args        = [
        Arg(name="selector", type="str",
            description="CSS selector (optional if using x,y)",
            required=False, default=""),
        Arg(name="x", type="float",
            description="X coordinate",
            required=False, default=None),
        Arg(name="y", type="float",
            description="Y coordinate",
            required=False, default=None),
        Arg(name="duration", type="float",
            description="Hover duration in seconds",
            required=False, default=0.8),
    ]

    def run(self, selector="", x=None, y=None, duration=0.8):
        cdp = _cdp()
        if selector:
            pt = cdp.center_of(selector)
            if not pt:
                return f"Element not found: {selector}"
            hx, hy = pt
        elif x is not None and y is not None:
            hx, hy = float(x), float(y)
        else:
            return "Provide selector or x,y"
        cdp.send("Input.dispatchMouseEvent", {
            "type": "mouseMoved", "x": hx, "y": hy, "modifiers": 0
        })
        time.sleep(float(duration))
        tip = cdp.js_val(
            "(function(){"
            "  var sels=['[class*=\"tooltip\"]','[class*=\"Tooltip\"]',"
            "    '[role=\"tooltip\"]','.grafana-tooltip','.graph-tooltip',"
            "    '[class*=\"tippy\"]','[class*=\"popover\"]','[class*=\"Popover\"]'];"
            "  for(var s of sels){"
            "    var el=document.querySelector(s);"
            "    if(el&&el.innerText&&el.innerText.trim()) return el.innerText.trim();"
            "  }"
            "  return null;"
            "})()"
        )
        result = f"Hovered at ({hx:.0f}, {hy:.0f})"
        if tip:
            result += f"\nTooltip: {tip}"
        return result


class BrowserMouseSweep(BaseSkill):
    name        = "browser_mouse_sweep"
    description = "Sweep mouse across an element to collect tooltip values at each step — ideal for charts and graphs"
    group       = "interaction"
    tier        = "full"
    args        = [
        Arg(name="selector", type="str",
            description="CSS selector of the chart element to sweep",
            required=True),
        Arg(name="steps", type="int",
            description="Number of sample points",
            required=False, default=15),
        Arg(name="duration", type="float",
            description="Pause at each point in seconds",
            required=False, default=0.2),
        Arg(name="direction", type="str",
            description="Sweep direction",
            required=False, default="horizontal",
            choices=["horizontal", "vertical"]),
    ]

    def run(self, selector="", steps=15, duration=0.2, direction="horizontal"):
        cdp      = _cdp()
        steps    = int(steps)
        duration = float(duration)
        box      = cdp.get_box(selector)
        if not box:
            return f"Element not found: {selector}"
        if direction == "vertical":
            points = [
                (box["x"] + box["w"] * 0.5,
                 box["y"] + box["h"] * i / max(steps-1, 1))
                for i in range(steps)
            ]
        else:
            points = [
                (box["x"] + box["w"] * 0.05 + (box["w"]*0.9) * i / max(steps-1, 1),
                 box["y"] + box["h"] * 0.4)
                for i in range(steps)
            ]
        time.sleep(0.3)
        collected = []
        tip_js = (
            "(function(){"
            "  var sels=['[class*=\"tooltip\"]','[class*=\"Tooltip\"]',"
            "    '[role=\"tooltip\"]','.grafana-tooltip','.graph-tooltip',"
            "    '[class*=\"tippy\"]','[class*=\"popover\"]'];"
            "  for(var s of sels){"
            "    var el=document.querySelector(s);"
            "    if(el&&el.innerText&&el.innerText.trim()) return el.innerText.trim();"
            "  }"
            "  return null;"
            "})()"
        )
        for hx, hy in points:
            cdp.send("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": hx, "y": hy})
            time.sleep(duration)
            tip = cdp.js_val(tip_js)
            if tip and tip not in collected:
                collected.append(tip)
        cdp.send("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": 0, "y": 0})
        if not collected:
            return "No tooltips found. Try browser_get_dom with query='tooltip' to find selector names."
        return f"Collected {len(collected)} values:\n\n" + "\n---\n".join(collected)


class BrowserScroll(BaseSkill):
    name        = "browser_scroll"
    description = "Scroll the page or a specific element"
    group       = "interaction"
    tier        = "core"
    args        = [
        Arg(name="direction", type="str",
            description="Scroll direction",
            required=False, default="down",
            choices=["up", "down", "left", "right"]),
        Arg(name="amount", type="int",
            description="Pixels to scroll",
            required=False, default=500),
        Arg(name="selector", type="str",
            description="Scroll inside this element instead of the page (optional)",
            required=False, default=""),
    ]

    def run(self, direction="down", amount=500, selector=""):
        cdp = _cdp()
        px  = int(amount)
        dx  = px  if direction == "right" else (-px if direction == "left" else 0)
        dy  = px  if direction == "down"  else (-px if direction == "up"   else 0)
        if selector:
            sel_json = json.dumps(selector)
            cdp.js(
                "(function(){"
                "  var el=document.querySelector(" + sel_json + ");"
                "  if(el) el.scrollBy(" + str(dx) + "," + str(dy) + ");"
                "})()"
            )
        else:
            cdp.js(f"window.scrollBy({dx},{dy})")
        return f"Scrolled {direction} {px}px" + (f" in {selector}" if selector else "")


class BrowserSelect(BaseSkill):
    name        = "browser_select"
    description = "Select an option from a <select> dropdown"
    group       = "interaction"
    tier        = "extended"
    args        = [
        Arg(name="selector", type="str",
            description="CSS selector of the <select> element",
            required=True),
        Arg(name="value", type="str",
            description="Option value or visible text to select",
            required=True),
    ]

    def run(self, selector="", value=""):
        cdp      = _cdp()
        sel_json = json.dumps(selector)
        val_json = json.dumps(value)
        result   = cdp.js_val(
            "(function(){"
            "  var el=document.querySelector(" + sel_json + ");"
            "  if(!el) return 'not found';"
            "  var opts=Array.from(el.options);"
            "  var opt=opts.find(function(o){"
            "    return o.value===" + val_json + "||o.text===" + val_json + ";"
            "  });"
            "  if(!opt) return 'option not found: '+" + val_json + ";"
            "  el.value=opt.value;"
            "  el.dispatchEvent(new Event('change',{bubbles:true}));"
            "  return 'selected: '+opt.text;"
            "})()"
        )
        return result


class BrowserCheckbox(BaseSkill):
    name        = "browser_checkbox"
    description = "Check, uncheck, or toggle a checkbox or radio button"
    group       = "interaction"
    tier        = "extended"
    args        = [
        Arg(name="selector", type="str",
            description="CSS selector of the checkbox/radio",
            required=True),
        Arg(name="action", type="str",
            description="check, uncheck, or toggle",
            required=False, default="toggle",
            choices=["check", "uncheck", "toggle"]),
    ]

    def run(self, selector="", action="toggle"):
        cdp      = _cdp()
        sel_json = json.dumps(selector)
        act_json = json.dumps(action)
        result   = cdp.js_val(
            "(function(){"
            "  var el=document.querySelector(" + sel_json + ");"
            "  if(!el) return 'not found';"
            "  var act=" + act_json + ";"
            "  if(act==='check') el.checked=true;"
            "  else if(act==='uncheck') el.checked=false;"
            "  else el.checked=!el.checked;"
            "  el.dispatchEvent(new Event('change',{bubbles:true}));"
            "  return 'checkbox is now: '+(el.checked?'checked':'unchecked');"
            "})()"
        )
        return result


class BrowserHandleDialog(BaseSkill):
    name        = "browser_handle_dialog"
    description = "Accept or dismiss browser alert/confirm/prompt dialogs"
    group       = "interaction"
    tier        = "extended"
    args        = [
        Arg(name="action", type="str",
            description="accept or dismiss",
            required=False, default="accept",
            choices=["accept", "dismiss"]),
        Arg(name="prompt_text", type="str",
            description="Text to enter if it's a prompt dialog (optional)",
            required=False, default=""),
    ]

    def run(self, action="accept", prompt_text=""):
        cdp = _cdp()
        cdp.send("Page.handleJavaScriptDialog", {
            "accept":     action == "accept",
            "promptText": prompt_text,
        })
        return f"Dialog {action}ed"


class BrowserWait(BaseSkill):
    name        = "browser_wait"
    description = "Wait for an element to appear, disappear, or for page to finish loading"
    group       = "interaction"
    tier        = "extended"
    args        = [
        Arg(name="selector", type="str",
            description="CSS selector to wait for",
            required=True),
        Arg(name="condition", type="str",
            description="appear or disappear",
            required=False, default="appear",
            choices=["appear", "disappear"]),
        Arg(name="timeout", type="int",
            description="Max seconds to wait",
            required=False, default=10),
    ]

    def run(self, selector="", condition="appear", timeout=10):
        cdp      = _cdp()
        sel_json = json.dumps(selector)
        deadline = time.time() + float(timeout)
        while time.time() < deadline:
            exists = cdp.js_val("document.querySelector(" + sel_json + ")!==null")
            if condition == "appear" and exists:
                return f"Element appeared: {selector}"
            if condition == "disappear" and not exists:
                return f"Element disappeared: {selector}"
            time.sleep(0.5)
        return f"Timeout after {timeout}s waiting for {selector} to {condition}"


class BrowserExecuteJS(BaseSkill):
    name        = "browser_execute_js"
    description = "Execute arbitrary JavaScript on the page and return the result. Most powerful skill — use when nothing else works."
    group       = "power"
    tier        = "full"
    args        = [
        Arg(name="code", type="str",
            description="JavaScript code to execute. Use return to get a value.",
            required=True),
    ]

    def run(self, code=""):
        cdp     = _cdp()
        wrapped = f"(function(){{ {code} }})()"
        val     = cdp.js_val(wrapped)
        if val is None:
            return "Executed (no return value)"
        result = str(val)
        return result[:3000] if len(result) > 3000 else result


# ── Visual ────────────────────────────────────────────────────────────────────

class BrowserScreenshot(BaseSkill):
    name        = "browser_screenshot"
    description = "Take a full-page screenshot and send to AI for visual analysis"
    group       = "visual"
    tier        = "core"
    args        = [
        Arg(name="filename", type="str",
            description="Output filename",
            required=False, default="screenshot.png"),
    ]

    def run(self, filename="screenshot.png"):
        cdp    = _cdp()
        result = cdp.send("Page.captureScreenshot", {"format": "png"})
        data   = result.get("data", "")
        if not data:
            return "Screenshot failed"
        img_bytes = base64.b64decode(data)
        with open(filename, "wb") as f:
            f.write(img_bytes)
        return f"__SCREENSHOT__:{filename}:{data}"


class BrowserScreenshotElement(BaseSkill):
    name        = "browser_screenshot_element"
    description = "Screenshot only a specific element — better for AI analysis of charts, panels, or small regions"
    group       = "visual"
    tier        = "extended"
    args        = [
        Arg(name="selector", type="str",
            description="CSS selector of the element to capture",
            required=True),
        Arg(name="filename", type="str",
            description="Output filename",
            required=False, default="element.png"),
        Arg(name="padding", type="int",
            description="Extra pixels around element",
            required=False, default=10),
    ]

    def run(self, selector="", filename="element.png", padding=10):
        cdp = _cdp()
        box = cdp.get_box(selector)
        if not box:
            return f"Element not found: {selector}"
        pad  = int(padding)
        clip = {
            "x":      max(0, box["x"] - pad),
            "y":      max(0, box["y"] - pad),
            "width":  box["w"] + pad * 2,
            "height": box["h"] + pad * 2,
            "scale":  2,
        }
        result = cdp.send("Page.captureScreenshot", {"format": "png", "clip": clip})
        data   = result.get("data", "")
        if not data:
            return "Screenshot failed"
        img_bytes = base64.b64decode(data)
        with open(filename, "wb") as f:
            f.write(img_bytes)
        return f"__SCREENSHOT__:{filename}:{data}"


# ── Context skills (from legacy skills/context.py) ────────────────────────────

def _page_ctx():
    from genie.skills.browser import page_context
    return page_context


class BrowserSnapshot(BaseSkill):
    name        = "browser_snapshot"
    description = (
        "Take a structured snapshot of the page: numbered interactive elements "
        "(buttons, inputs, links) with positions, plus a short text summary. "
        "USE THIS FIRST before any interaction — it gives you element IDs to click/type into."
    )
    group = "context"
    tier  = "core"
    args  = [
        Arg(name="include_text", type="bool",
            description="Include visible text summary: true or false",
            required=False, default=True),
    ]

    def run(self, include_text=True):
        incl = str(include_text).lower() != "false"
        return _page_ctx().get_page_snapshot(include_text=incl)


class BrowserClickElement(BaseSkill):
    name        = "browser_click_element"
    description = (
        "Click an element by its ID from browser_snapshot. "
        "More reliable than CSS selectors — always use after browser_snapshot."
    )
    group = "context"
    tier  = "core"
    args  = [
        Arg(name="element_id", type="int",
            description="The number shown in brackets from browser_snapshot e.g. 3",
            required=True),
    ]

    def run(self, element_id=0):
        return _page_ctx().click_element(int(element_id))


class BrowserTypeElement(BaseSkill):
    name        = "browser_type_element"
    description = (
        "Type text into an input by its ID from browser_snapshot. "
        "Always use after browser_snapshot to find the input ID."
    )
    group = "context"
    tier  = "core"
    args  = [
        Arg(name="element_id", type="int",
            description="The number shown in brackets from browser_snapshot",
            required=True),
        Arg(name="text", type="str",
            description="Text to type",
            required=True),
    ]

    def run(self, element_id=0, text=""):
        return _page_ctx().type_into_element(int(element_id), text)


class BrowserGetNumbers(BaseSkill):
    name        = "browser_get_numbers"
    description = (
        "Extract all numeric values visible on the page — "
        "metrics, stats, chart labels, table values. "
        "Useful for dashboards, Grafana, monitoring pages."
    )
    group = "context"
    tier  = "extended"
    args: list = []

    def run(self):
        return _page_ctx().get_numbers_on_page()


# ── Registry ──────────────────────────────────────────────────────────────────

ALL_BROWSER_SKILLS = [
    BrowserListTabs,
    BrowserSwitchTab,
    BrowserNavigate,
    BrowserGetURL,
    BrowserGetText,
    BrowserGetElement,
    BrowserGetBoundingBox,
    BrowserGetLocalStorage,
    BrowserGetDOM,
    BrowserInterceptXHR,
    BrowserClick,
    BrowserDoubleClick,
    BrowserRightClick,
    BrowserDrag,
    BrowserType,
    BrowserKeyboard,
    BrowserHover,
    BrowserMouseSweep,
    BrowserScroll,
    BrowserSelect,
    BrowserCheckbox,
    BrowserHandleDialog,
    BrowserWait,
    BrowserExecuteJS,
    BrowserScreenshot,
    BrowserScreenshotElement,
    BrowserSnapshot,
    BrowserClickElement,
    BrowserTypeElement,
    BrowserGetNumbers,
]
