"""Shared HTTP helpers, SSE parsing, and retry logic for all providers."""
from __future__ import annotations

import json
import re


def parse_sse(raw: str) -> str:
    """Parse a server-sent events stream into a single content string.

    Handles:
    - TGenie: ``data: {"done": true}`` terminator
    - OpenAI / Cline proxy: ``data: [DONE]`` terminator
    - delta.content for final answer, delta.reasoning_content for thinking tokens

    Falls back to reasoning tokens when content is empty (extended-thinking mode).
    """
    full = ""
    reasoning = ""
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        if line == "data: [DONE]" or re.match(r'^data:\s*\{"done"\s*:\s*true', line):
            continue
        if not line.startswith("data:"):
            continue
        json_str = line[5:].strip()
        if not json_str or not json_str.startswith("{"):
            continue
        try:
            chunk = json.loads(json_str)
            choice = chunk.get("choices", [{}])[0]
            delta = choice.get("delta", {})
            text = delta.get("content") or None
            if text and isinstance(text, str):
                full += text
            rtext = (
                delta.get("reasoning_content")
                or delta.get("reasonText")
                or ""
            )
            if rtext:
                reasoning += rtext
        except (json.JSONDecodeError, KeyError, IndexError):
            pass
    return full or reasoning
