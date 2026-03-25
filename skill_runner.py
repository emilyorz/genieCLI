import json
import re
from pathlib import Path

import yaml

from skills import ALL_SKILLS
from skills._registry import SkillRegistry

SKILL_MAP = SkillRegistry._skills

# ── Oracle→Trino cheat sheet (injected into system prompt) ───────────────────

def _build_oracle2trino_cheatsheet() -> str:
    """Load oracle_trino_functions.yaml and render a compact cheat sheet for LLM context."""
    yaml_path = Path(__file__).parent / "data" / "oracle_trino_functions.yaml"
    try:
        with open(yaml_path, encoding="utf-8") as f:
            db = yaml.safe_load(f)
    except Exception as e:
        return f"(oracle2trino YAML unavailable: {e})"

    lines: list[str] = []

    # Function mapping — compact format
    lines.append("### Oracle → Trino Function Mapping (quick reference)")
    for entry in db.get("functions", []):
        oracle = entry.get("oracle", "")
        trino = entry.get("trino") or "❌ No direct equivalent"
        example = entry.get("example", "")
        notes = entry.get("notes", "")
        line = f"- {oracle} → {trino}"
        if example:
            line += f"  |  e.g. {example}"
        if notes:
            line += f"  |  ⚠️ {notes}"
        lines.append(line)

    lines.append("")

    # Type mapping
    lines.append("### Oracle → Trino Data Type Mapping")
    for entry in db.get("types", []):
        oracle_t = entry.get("oracle", "")
        trino_t = entry.get("trino", "")
        notes = entry.get("notes", "")
        line = f"- {oracle_t} → {trino_t}"
        if notes:
            line += f"  |  ⚠️ {notes}"
        lines.append(line)

    lines.append("")

    # Hard limits
    lines.append("### Trino Hard Limits (PL/SQL constructs with NO direct equivalent)")
    for lim in db.get("trino_limitations", []):
        lines.append(f"- {lim}")

    return "\n".join(lines)


# ── System prompt builder ─────────────────────────────────────────────────────

def build_system_prompt() -> str:
    # Build tool list
    tool_lines = []
    for skill in ALL_SKILLS:
        if skill.args:
            parts = []
            for arg in skill.args:
                part = f"{arg.name}: {arg.description}"
                if not arg.required:
                    part += f" (optional, default: {arg.default})"
                if arg.choices:
                    part += f" [{'/'.join(arg.choices)}]"
                parts.append(part)
            args_str = ", ".join(parts)
        else:
            args_str = "no args"
        tool_lines.append(f"- {skill.name}({args_str}): {skill.description}")
    tools_block = "\n".join(tool_lines)

    oracle2trino_cheatsheet = _build_oracle2trino_cheatsheet()

    return f"""You are a helpful AI assistant with expertise in SQL migration, browser control, and file operations.

## ORACLE → TRINO MIGRATION EXPERTISE

You are an expert in migrating Oracle SQL and PL/SQL stored procedures to Trino SQL.
When users ask you to convert Oracle SQL, analyze stored procedures, or discuss Oracle→Trino migration:

### Migration Classification
Classify each Oracle SP/query into three buckets:
1. **Direct Convert** — Pure SQL (SELECT/JOIN/aggregation) with only function/syntax differences → convert with tool lookup
2. **Orchestration Rewrite** — Procedural logic (IF/LOOP/CURSOR/EXCEPTION) + ETL flow → extract queries, wrap in Python
3. **Manual Redesign** — System calls (DBMS_*, packages), dynamic SQL, transactions → architect from scratch

### Output Format (always use this 3-section structure)
**[1] Analysis**
- Convertibility score: X% (rough estimate)
- Detected constructs and their migration category
- Connector-specific risks (Hive/Iceberg/Delta)

**[2] Converted SQL**
- Trino-compatible SQL with inline change annotations:
  `-- [Oracle→Trino: NVL→COALESCE]`

**[3] Migration Notes**
- What needs orchestration rewrite (and how)
- What needs manual redesign (and why)
- Open questions for the SP owner

### Tool Usage Order (strictly follow this sequence)
1. **`analyze_oracle_sp`** — always the entry point for any SP/query migration request
   - Automatically runs sqlglot transpile as first pass
   - Returns auto-converted SQL + list of remaining issues
2. **`lookup_oracle_function`** — for each remaining warning from step 1 (ROWNUM, LISTAGG, etc.)
3. **`lookup_oracle_type`** — when Oracle-specific data types appear in the SQL
4. **`transpile_sql`** — use directly for quick single-statement transpile without full SP analysis
5. **`list_trino_limitations`** — when PL/SQL blocks are detected and you need to explain hard limits

### Key Rules
- NEVER manually convert Oracle functions from memory alone — always use lookup tools first
- sqlglot handles ~50% automatically; the AI's job is to fix the remaining 50%
- Start from sqlglot's output, not the original Oracle SQL, when writing final Trino SQL
- Mark PL/SQL blocks that can't be converted as `[ORCHESTRATION NEEDED: <reason>]`
- Set realistic expectations: complex SPs may only be 10-50% auto-convertible
- Always note connector-specific risks (Hive vs Iceberg vs Delta) when DML is involved

{oracle2trino_cheatsheet}

---

## GENERAL CAPABILITIES — Browser & File Tools

## HOW TO USE TOOLS

When you need a tool, output ONLY this JSON (no explanation, no markdown):
{{"memory": "1-2 sentence summary of what you just did and what you plan to do next.", "tool": "tool_name", "args": {{"key": "value"}}}}

When the task is complete and no more tools are needed, output:
{{"memory": "Task complete.", "tool": null, "args": {{}}}}

The "memory" field is required in every response. It helps you track progress across steps.
After receiving the tool result, output the next JSON tool call or the final answer in plain text.

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
            if "memory" not in data:
                data["memory"] = ""
            return data
    except Exception:
        pass
    return None


def extract_memory(text: str) -> str:
    parsed = parse_tool_call(text)
    if parsed:
        return parsed.get("memory", "")
    return ""


def run_tool(tool_call: dict) -> str:
    name  = tool_call.get("tool", "")
    args  = tool_call.get("args", {})
    skill = SKILL_MAP.get(name)
    if not skill:
        available = ", ".join(SKILL_MAP.keys())
        return f"Unknown tool: '{name}'. Available tools: {available}"

    ok, err = skill.validate(args)
    if not ok:
        return f"Validation error for '{name}': {err}"

    try:
        return skill.run(**args)
    except TypeError as e:
        # Wrong args — give helpful error
        import inspect
        sig = inspect.signature(skill.run)
        return f"Wrong args for {name}: {e}. Expected: {sig}"
    except Exception as e:
        return f"Tool error ({name}): {e}"
