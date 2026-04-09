"""Model capability profiles — context windows, skill tiers, and limits.

Maps model names/patterns to their known capabilities so the harness can
adapt system prompt size, skill loading, and context pruning strategy.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal
import re


SkillTier = Literal["core", "extended", "full"]


@dataclass(frozen=True)
class ModelProfile:
    """Capability profile for an LLM model."""
    name: str
    context_window: int          # max tokens (input + output)
    max_output: int              # max output tokens
    skill_tier: SkillTier        # which skill tier to use
    supports_vision: bool = False
    supports_tool_calls: bool = False  # native function calling
    chars_per_token: float = 3.5      # rough char→token ratio


# ── Known profiles ───────────────────────────────────────────────────────────

_PROFILES: list[ModelProfile] = [
    # Gemini
    ModelProfile("gemini-2.5-flash",   1_048_576, 65_536, "core",     supports_vision=True),
    ModelProfile("gemini-2.5-pro",     1_048_576, 65_536, "extended", supports_vision=True),
    ModelProfile("gemini-2.0-flash",   1_048_576, 8_192,  "core",     supports_vision=True),
    # OpenAI
    ModelProfile("gpt-4o",            128_000,   16_384, "full",     supports_vision=True, supports_tool_calls=True),
    ModelProfile("gpt-4o-mini",       128_000,   16_384, "extended", supports_vision=True, supports_tool_calls=True),
    ModelProfile("gpt-4.1",           1_048_576, 32_768, "full",     supports_vision=True, supports_tool_calls=True),
    ModelProfile("gpt-4.1-mini",      1_048_576, 32_768, "extended", supports_vision=True, supports_tool_calls=True),
    ModelProfile("gpt-4.1-nano",      1_048_576, 32_768, "core",     supports_vision=True, supports_tool_calls=True),
    # Anthropic
    ModelProfile("claude-sonnet-4",    200_000,   8_192,  "full",     supports_vision=True, supports_tool_calls=True),
    ModelProfile("claude-opus-4",      200_000,   32_000, "full",     supports_vision=True, supports_tool_calls=True),
    ModelProfile("claude-haiku-3.5",   200_000,   8_192,  "extended", supports_vision=True, supports_tool_calls=True),
    # Ollama / local
    ModelProfile("qwen3.5:4b",         32_768,    4_096,  "core"),
    ModelProfile("qwen3.5:9b",         32_768,    4_096,  "core"),
    ModelProfile("qwen3:1.7b",         32_768,    2_048,  "core"),
    ModelProfile("gemma4:e4b",         32_768,    4_096,  "core"),
    ModelProfile("llama3:8b",          8_192,     2_048,  "core"),
]

# Pattern-based fallbacks for unknown models
_FALLBACK_PATTERNS: list[tuple[str, ModelProfile]] = [
    (r"gemini.*flash",  ModelProfile("gemini-flash-fallback",  1_048_576, 8_192,  "core",     supports_vision=True)),
    (r"gemini.*pro",    ModelProfile("gemini-pro-fallback",    1_048_576, 65_536, "extended", supports_vision=True)),
    (r"gpt-4",          ModelProfile("gpt4-fallback",          128_000,   16_384, "full",     supports_vision=True, supports_tool_calls=True)),
    (r"claude",         ModelProfile("claude-fallback",        200_000,   8_192,  "full",     supports_vision=True, supports_tool_calls=True)),
    (r"qwen",           ModelProfile("qwen-fallback",          32_768,    4_096,  "core")),
    (r"llama|gemma|phi", ModelProfile("local-fallback",        8_192,     2_048,  "core")),
]

# Conservative default for completely unknown models
_DEFAULT_PROFILE = ModelProfile("unknown", 8_192, 2_048, "core")


def get_profile(model_name: str) -> ModelProfile:
    """Look up the capability profile for a model name."""
    name_lower = model_name.lower().strip()

    # Exact match (case-insensitive)
    for p in _PROFILES:
        if p.name.lower() == name_lower:
            return p

    # Pattern match
    for pattern, profile in _FALLBACK_PATTERNS:
        if re.search(pattern, name_lower):
            return profile

    return _DEFAULT_PROFILE


def estimate_tokens(text: str, model_name: str = "") -> int:
    """Rough token count from character count.

    Uses model-specific chars_per_token ratio when available.
    """
    profile = get_profile(model_name) if model_name else _DEFAULT_PROFILE
    return max(1, int(len(text) / profile.chars_per_token))
