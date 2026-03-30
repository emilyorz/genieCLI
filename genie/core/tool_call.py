"""Tool call parsing — extracted from legacy skill_runner.py."""
from __future__ import annotations

import json
import re


def parse_tool_call(text: str) -> dict | None:
    """Extract a JSON tool call from an AI response.

    Handles markdown code fences, trailing commas, and common AI formatting
    mistakes.  Returns None if no valid tool call found.
    """
    if not text:
        return None

    # Strip markdown code blocks
    text = re.sub(r"```(?:json)?\s*", "", text).strip()

    # Find JSON object — greedy to handle nested braces
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        return None

    raw = m.group()

    # Fix common AI mistakes
    raw = re.sub(r'"\s+(\w)', r'"\1', raw)  # stray whitespace after quote
    raw = re.sub(r",\s*\}", "}", raw)        # trailing comma in object
    raw = re.sub(r",\s*\]", "]", raw)        # trailing comma in array

    try:
        data = json.loads(raw)
    except Exception:
        return None

    if "tool" not in data:
        return None

    data.setdefault("args", {})
    data.setdefault("memory", "")
    return data


def extract_memory(text: str) -> str:
    parsed = parse_tool_call(text)
    return parsed.get("memory", "") if parsed else ""


def normalize_result(result) -> str:
    """Coerce a tool result to str — shared by chat loop and autoresearch."""
    if result is None:
        return ""
    return result if isinstance(result, str) else str(result)
