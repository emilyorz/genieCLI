---
covers:
  - "genie/providers/*.py"
last_synced: "572f7ff30399bed1a1a3c230918ba037ae874272"
---

## Purpose

Concrete LLM backend drivers. Each provider wraps a remote HTTP API and exposes the
`Provider` protocol (`complete`, `complete_text`, `capabilities`) defined in
`genie/core/provider.py`. The three providers cover: OpenAI-compatible endpoints
(including Ollama local inference), Anthropic-format internal proxies (system prompt
extracted to a top-level field), and the internal TGenie multipart/form-data API.
`base.py` supplies the shared SSE parser used by all three.

## Exports

from __future__ import annotations
import: base64
from typing import Iterator
import: requests
from genie.core.provider import CompletionRequest, Delta, ProviderCapabilities
from genie.providers.base import parse_sse
from genie.providers.openai import _history_to_openai
function: def set_debug(enabled) -> None (anthropic.py:16)
function: def _dbg(*args) -> None (anthropic.py:21)
class: AnthropicProvider (anthropic.py:26)
  method: def __init__(self, cfg) -> None (anthropic.py:29)
  property: def name(self) -> str (anthropic.py:33)
  method: def capabilities(self) -> ProviderCapabilities (anthropic.py:36)
  method: def complete(self, req) -> Iterator[Delta] (anthropic.py:39)
  method: def complete_text(self, req) -> str (anthropic.py:43)
  method: def _call(self, req) -> str (anthropic.py:46)
from __future__ import annotations
import: json
import: re
function: def parse_sse(raw) -> str (base.py:8)
from __future__ import annotations
import: base64
from typing import Iterator
import: requests
from genie.core.provider import CompletionRequest, Delta, ProviderCapabilities
from genie.providers.base import parse_sse
function: def set_debug(enabled) -> None (openai.py:15)
function: def _dbg(*args) -> None (openai.py:20)
function: def _history_to_openai(history, content_as_array) -> list[dict] (openai.py:25)
class: OpenAIProvider (openai.py:39)
  method: def __init__(self, cfg) -> None (openai.py:42)
  property: def name(self) -> str (openai.py:46)
  method: def capabilities(self) -> ProviderCapabilities (openai.py:49)
  method: def complete(self, req) -> Iterator[Delta] (openai.py:52)
  method: def complete_text(self, req) -> str (openai.py:56)
  method: def _is_ollama(self) -> bool (openai.py:59)
  method: def _call(self, req) -> str (openai.py:64)
  method: def _call_ollama_native(self, req) -> str (openai.py:70)
  method: def _call_openai(self, req) -> str (openai.py:116)
from __future__ import annotations
import: subprocess
import: sys
import: uuid
from typing import Iterator
import: requests
import: urllib3
from genie.core.provider import CompletionRequest, Delta, ProviderCapabilities
from genie.providers.base import parse_sse
function: def set_debug(enabled) -> None (tgenie.py:20)
function: def _dbg(*args) -> None (tgenie.py:25)
class: TGenieProvider (tgenie.py:30)
  method: def __init__(self, cfg) -> None (tgenie.py:33)
  property: def name(self) -> str (tgenie.py:37)
  method: def capabilities(self) -> ProviderCapabilities (tgenie.py:40)
  method: def complete(self, req) -> Iterator[Delta] (tgenie.py:43)
  method: def complete_text(self, req) -> str (tgenie.py:47)
  method: def _call(self, req) -> str (tgenie.py:50)
  method: def _refresh_token(self) -> bool (tgenie.py:150)

## Invariants

- All three provider classes report `tool_calls=False` in `capabilities()` —
  tool-call routing is handled upstream in `chat.py`, not inside providers.
  (anthropic.py:37, openai.py:50, tgenie.py:41)
- `parse_sse` falls back to `reasoning_content` / `reasonText` when `content` is
  empty, enabling extended-thinking models to return output transparently. (base.py:38–47)
- `OpenAIProvider._call` routes to `_call_ollama_native` (non-streaming,
  `think=false`) when the base URL contains `localhost:11434` or `ollama`, bypassing
  the streaming SSE path. (openai.py:59–68)
- `_history_to_openai` silently drops any message whose role is not one of
  `user`, `assistant`, `system`. (openai.py:30)
- `AnthropicProvider._call` extracts the system message into a top-level `"system"`
  key before posting; it delegates history serialisation to `_history_to_openai`
  from `openai.py`. (anthropic.py:73–89)
- `TGenieProvider._call` sends requests with `verify=False` (TLS verification
  disabled) and suppresses `urllib3` InsecureRequestWarning at module load time.
  (tgenie.py:12, tgenie.py:121)
- On HTTP 401, `TGenieProvider._call` calls `_refresh_token()`; if that returns
  `False`, `RuntimeError("Token refresh failed. Run grab_auth.py manually.")` is
  raised immediately (tgenie.py:128–134). If `_refresh_token()` returns `True`, the
  method reloads config and recurses via `self._call(req)` with no retry counter or
  guard flag — a second 401 after the refresh re-enters the same 401 branch and
  attempts another refresh rather than raising `RuntimeError`. (tgenie.py:128–134)
- `_DEBUG` is a module-level global in each provider file; `set_debug(True)` must be
  called before the first request to see debug output. (anthropic.py:13–18,
  openai.py:12–17, tgenie.py:17–22)

## Change log

- 572f7ff30399bed1a1a3c230918ba037ae874272: fix Invariant 7 — HTTP 401 retry claim
  corrected: RuntimeError fires only when _refresh_token() returns False, not on a
  second 401; unbounded recursion risk on repeated 401s documented. (tgenie.py:128–134)
- 572f7ff30399bed1a1a3c230918ba037ae874272: initial card generated by doc-bootstrap
