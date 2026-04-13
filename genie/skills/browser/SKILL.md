---
name: browser
description: >-
  Chrome CDP browser automation tools. Use for web page interaction,
  navigation, element inspection, screenshots, JavaScript execution,
  and DOM manipulation via Chrome DevTools Protocol.
version: 1.0.0
group: browser
tier: core
requires:
  python:
    - websocket-client
  system:
    - "Chrome with remote-debugging enabled (--remote-debugging-port=9222)"
---

# Browser Automation

## Mandatory Workflow

Every browser task MUST follow this 4-step cycle. Do NOT skip steps.

### Step 1: LOOK — Take a snapshot first

Before touching anything, call **browser_snapshot** to see what is on the page.
This returns numbered interactive elements (buttons, inputs, links) with their IDs.

Do NOT call browser_click or browser_type before you have a snapshot.

### Step 2: PICK — Choose the element by ID

Read the snapshot output. Find the element you need by its **[ID] number**.
If the element is not visible, scroll first, then snapshot again.

### Step 3: ACT — Use the ID-based tools

- To click: **browser_click_element**(element_id=N)
- To type: **browser_type_element**(element_id=N, text="...")
- To scroll: **browser_scroll**(direction="down")

Do NOT guess CSS selectors. Always use element IDs from the snapshot.

### Step 4: VERIFY — Confirm the action worked

After acting, call **browser_snapshot** again (or **browser_screenshot**) to
confirm the page changed as expected. If it didn't, re-read the snapshot
and try a different element — do NOT repeat the same action blindly.

## Tool Selection Rules

**For clicking and typing, always prefer the ID-based tools:**

| Want to do | Use this | NOT this |
|------------|----------|----------|
| Click a button/link | browser_click_element(element_id=N) | ~~browser_click(selector=...)~~ |
| Type into a field | browser_type_element(element_id=N, text=...) | ~~browser_type(selector=...)~~ |
| Read the page | browser_snapshot | ~~browser_get_text~~ (text-only, no IDs) |

The CSS-selector tools (browser_click, browser_type, browser_get_element) are
available for advanced scenarios but should NOT be your first choice.

## Common Patterns

**Navigate to a page and interact:**
1. browser_navigate(url="...")
2. browser_snapshot
3. browser_click_element(element_id=N)
4. browser_snapshot (verify)

**Fill a form:**
1. browser_snapshot
2. browser_type_element(element_id=N, text="...")  (repeat for each field)
3. browser_click_element(element_id=N)  (submit button)
4. browser_snapshot (verify)

**Read dashboard data:**
1. browser_snapshot (or browser_screenshot for visual data)
2. Read the values from the snapshot output

## Tool Groups (30 tools)

| Group | Core tools | Description |
|-------|------------|-------------|
| **navigation** | list_tabs, switch_tab, navigate, get_url | Tab and URL management |
| **context** | snapshot, click_element, type_element | ID-based page interaction (preferred) |
| **interaction** | scroll, click, type, ... | CSS-selector interaction (advanced) |
| **reading** | get_text, get_element, get_dom, ... | Page content extraction |
| **visual** | screenshot, screenshot_element | Visual capture for AI analysis |
| **power** | execute_js | Raw JavaScript execution (last resort) |
