"""Context manager — tracks token usage and prunes history when needed.

Keeps conversation within the model's context window by:
1. Estimating token usage per message
2. Summarizing old messages when approaching the limit
3. Truncating tool results that are too large
"""
from __future__ import annotations

from dataclasses import dataclass, field

from genie.core.model_profiles import ModelProfile, estimate_tokens, get_profile


# Reserve tokens for system prompt + latest exchange + output
_SYSTEM_RESERVE_RATIO = 0.15   # 15% for system prompt
_OUTPUT_RESERVE_RATIO = 0.15   # 15% for model output
_PRUNE_TRIGGER_RATIO = 0.70   # start pruning at 70% of context window
_TOOL_RESULT_MAX_CHARS = 3000  # truncate individual tool results


@dataclass
class ContextManager:
    """Manages conversation context within model limits."""

    model_name: str
    profile: ModelProfile = field(init=False)
    _token_usage: int = field(default=0, init=False)

    def __post_init__(self) -> None:
        self.profile = get_profile(self.model_name)

    @property
    def context_window(self) -> int:
        return self.profile.context_window

    @property
    def available_for_history(self) -> int:
        """Tokens available for conversation history (after reserves)."""
        reserved = int(self.context_window * (_SYSTEM_RESERVE_RATIO + _OUTPUT_RESERVE_RATIO))
        return self.context_window - reserved

    def estimate_history_tokens(self, history: list[dict]) -> int:
        """Estimate total tokens in conversation history."""
        total = 0
        for msg in history:
            content = msg.get("content", [])
            if isinstance(content, list):
                text = " ".join(
                    item.get("text", "") for item in content
                    if isinstance(item, dict) and item.get("text")
                )
            elif isinstance(content, str):
                text = content
            else:
                text = str(content)
            # Add role overhead (~4 tokens per message)
            total += estimate_tokens(text, self.model_name) + 4
        return total

    def should_prune(self, history: list[dict]) -> bool:
        """Check if history exceeds the prune trigger threshold."""
        used = self.estimate_history_tokens(history)
        return used > int(self.available_for_history * _PRUNE_TRIGGER_RATIO)

    def prune_history(self, history: list[dict]) -> list[dict]:
        """Prune conversation history to fit within context limits.

        Strategy:
        1. Always keep the system message (index 0)
        2. Always keep the last 4 messages (recent context)
        3. Summarize middle messages into a single condensed message
        4. Truncate oversized tool results in remaining messages
        """
        if not self.should_prune(history):
            return history

        if len(history) <= 6:
            # Too short to prune meaningfully — just truncate tool results
            return [self._truncate_message(m) for m in history]

        # Split: system | middle (to summarize) | recent (keep)
        system_msgs = [m for m in history[:1] if m.get("role") == "system"]
        keep_recent = 4
        middle = history[len(system_msgs):-keep_recent]
        recent = history[-keep_recent:]

        # Summarize middle section, budget-aware
        max_summary_chars = int(self.available_for_history * 0.1 * self.profile.chars_per_token)
        summary = self._summarize_messages(middle, max_chars=max_summary_chars)

        # Use "system" role to avoid back-to-back user messages
        summary_msg = {
            "role": "system",
            "content": [{"type": "text", "text": summary, "reasonText": None}],
        }

        pruned = system_msgs + [summary_msg] + [self._truncate_message(m) for m in recent]

        return pruned

    def truncate_tool_result(self, result: str) -> str:
        """Truncate a tool result if it exceeds the max size."""
        if len(result) <= _TOOL_RESULT_MAX_CHARS:
            return result
        half = _TOOL_RESULT_MAX_CHARS // 2
        return (
            result[:half]
            + f"\n\n... [truncated {len(result) - _TOOL_RESULT_MAX_CHARS} chars] ...\n\n"
            + result[-half:]
        )

    def _truncate_message(self, msg: dict) -> dict:
        """Truncate oversized content in a message."""
        content = msg.get("content", [])
        if not isinstance(content, list):
            return msg

        new_content = []
        for item in content:
            if isinstance(item, dict) and item.get("text"):
                text = item["text"]
                if len(text) > _TOOL_RESULT_MAX_CHARS and "[Tool result:" in text:
                    text = self.truncate_tool_result(text)
                new_content.append({**item, "text": text})
            else:
                new_content.append(item)

        return {**msg, "content": new_content}

    def _summarize_messages(self, messages: list[dict], max_chars: int = 4000) -> str:
        """Create a condensed summary of messages, capped at max_chars."""
        parts = ["[Context summary of previous conversation]:"]
        total_chars = len(parts[0])

        for msg in messages:
            role = msg.get("role", "?")
            content = msg.get("content", [])
            if isinstance(content, list):
                text = " ".join(
                    item.get("text", "")[:200] for item in content
                    if isinstance(item, dict) and item.get("text")
                )
            elif isinstance(content, str):
                text = content[:200]
            else:
                text = str(content)[:200]

            if not text.strip():
                continue

            # Compress tool results to just the tool name
            if "[Tool result:" in text:
                import re
                tool_match = re.search(r"\[Tool result: (\w+)\]", text)
                tool_name = tool_match.group(1) if tool_match else "unknown"
                entry = f"- [tool:{tool_name}] executed"
            elif role == "user":
                entry = f"- User: {text[:150]}"
            elif role == "assistant":
                entry = f"- AI: {text[:150]}"
            else:
                continue

            if total_chars + len(entry) + 1 > max_chars:
                break
            parts.append(entry)
            total_chars += len(entry) + 1

        return "\n".join(parts)

    def context_status(self, history: list[dict]) -> dict:
        """Return context usage stats for diagnostics."""
        used = self.estimate_history_tokens(history)
        available = self.available_for_history
        return {
            "model": self.model_name,
            "context_window": self.context_window,
            "tokens_used": used,
            "tokens_available": available,
            "usage_pct": round(used / available * 100, 1) if available else 0,
            "should_prune": self.should_prune(history),
            "message_count": len(history),
        }
