import json
import re
from skills import ALL_SKILLS

SKILL_MAP = {s.name: s for s in ALL_SKILLS}


def build_system_prompt() -> str:
    # Build tool list
    tool_lines = []
    for skill in ALL_SKILLS:
        args_str = ", ".join(
            f"{k}: {v}" for k, v in skill.args_schema.items()
        ) or "no args"
        tool_lines.append(f"- {skill.name}({args_str}): {skill.description}")
    tools_block = "\n".join(tool_lines)

    return f"""You are an AI browser agent. You can control a Chrome browser and read/write files.

## HOW TO USE TOOLS

When you need a tool, output ONLY this JSON (one line, no explanation):
{{"tool": "tool_name", "args": {{"key": "value"}}}}

After receiving the tool result, continue reasoning or give the final answer in plain text.
You can call tools multiple times in sequence to complete a task.

## DECISION STRATEGY

Always start with the least information needed:
1. Need to interact with the page? → browser_snapshot FIRST (gives numbered element IDs)
2. Need to see visuals / charts? → browser_screenshot or browser_screenshot_element
3. Need raw numbers from dashboard? → browser_get_numbers
4. Data is loaded via API (not in DOM)? → browser_intercept_xhr
5. Nothing else works? → browser_execute_js

AVOID browser_get_dom unless you specifically need to debug selectors.
It returns too much noise. Use browser_snapshot instead.

## WORKFLOW PATTERNS

### Pattern 1: Interact with page (PREFERRED)
1. browser_snapshot → get numbered element list
2. browser_click_element(element_id=3) → click by ID, no selector needed
3. browser_type_element(element_id=5, text="hello") → type by ID

### Pattern 2: Visual analysis
1. browser_screenshot → see full page
2. browser_screenshot_element(selector="...") → zoom into chart/panel
3. Describe what you see, identify coordinates if needed
4. browser_click(x=320, y=450) → click by coordinate

### Pattern 3: Extract dashboard/chart data
1. browser_get_numbers → extract all visible numeric values (fast)
2. browser_screenshot_element → zoom into chart for visual analysis
3. browser_mouse_sweep → collect tooltip values
4. browser_intercept_xhr → capture raw API response if above fails

### Pattern 4: Fill a form
1. browser_snapshot → find input IDs
2. browser_type_element(element_id=2, text="value") → fill fields
3. browser_click_element(element_id=8) → submit

### Pattern 5: Handle dynamic content
1. browser_click_element → trigger action
2. browser_wait(selector="...", condition="appear") → wait
3. browser_snapshot → re-assess updated page

## IMPORTANT RULES
- ONE tool call per response
- ALWAYS use browser_snapshot before browser_click_element or browser_type_element
- Element IDs reset every time browser_snapshot is called
- If element_id not found → call browser_snapshot again (page may have changed)
- Prefer element IDs over CSS selectors — less brittle
- If completely stuck → browser_screenshot to re-assess visually

## AVAILABLE TOOLS

{tools_block}
"""


def parse_tool_call(text: str):
    if not text:
        return None

    # Strip markdown code blocks
    text = re.sub(r'```(?:json)?\s*', '', text).strip()

    # Find JSON object — be greedy to handle nested braces
    m = re.search(r'\{.*\}', text, re.DOTALL)
    if not m:
        return None

    raw = m.group()

    # Fix common AI mistakes
    raw = re.sub(r'"\s+(\w)', r'"\1', raw)   # " args" -> "args"
    raw = re.sub(r',\s*\}', '}', raw)         # trailing comma
    raw = re.sub(r',\s*\]', ']', raw)

    try:
        data = json.loads(raw)
        if "tool" in data:
            if "args" not in data:
                data["args"] = {}
            return data
    except Exception:
        pass
    return None


def run_tool(tool_call: dict) -> str:
    name  = tool_call.get("tool", "")
    args  = tool_call.get("args", {})
    skill = SKILL_MAP.get(name)
    if not skill:
        available = ", ".join(SKILL_MAP.keys())
        return f"Unknown tool: '{name}'. Available tools: {available}"
    try:
        return skill.run(**args)
    except TypeError as e:
        # Wrong args — give helpful error
        import inspect
        sig = inspect.signature(skill.run)
        return f"Wrong args for {name}: {e}. Expected: {sig}"
    except Exception as e:
        return f"Tool error ({name}): {e}"
