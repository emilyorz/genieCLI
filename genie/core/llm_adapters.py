"""llm_adapters.py — Shared LLM adapter factory for genieCLI skills.

Extracted from genie/skills/mcp_trino/write_analysis.py so that both the
MCP path and the --direct advisory path can share the same factory without
circular imports.
"""
from __future__ import annotations


def _make_advisory_llm_fn(provider, model: str, reasoning: str):
    """Adapt provider.complete_text(CompletionRequest) to LlmFn (prompt:str -> str).

    On a provider exception the closure RAISES — decompose/optimize catch LLM
    exceptions internally and fall back (heuristic monsters / passthrough), so the
    exception never escapes to the caller. None/""/whitespace coerce to "".
    """
    from genie.core.provider import CompletionRequest
    from genie.session.manager import new_msg

    def _llm(prompt: str) -> str:
        req = CompletionRequest(
            messages=[
                new_msg("system", "You provide advisory-only Trino SQL fragment rewrites."),
                new_msg("user", prompt),
            ],
            model=model,
            reasoning=reasoning,
        )
        text = provider.complete_text(req)
        return str(text or "")

    return _llm
