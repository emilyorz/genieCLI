---
covers:
  - "genie/providers/*.py"
last_synced: "df1131522263a60bac2a7a0326499f43bc63c490"
---

## Purpose

Implements the LLM backend adapters that satisfy the `Provider` protocol defined in
`genie/core/provider.py`. The package owns four files: a shared SSE-parsing helper
(`base.py`), an OpenAI-compatible adapter (`openai.py`), an Anthropic-format adapter
(`anthropic.py`), and an internal TGenie multipart adapter (`tgenie.py`). All concrete
providers translate a `CompletionRequest` into an HTTP call and return plain text; they
do not own retry logic, context management, or tool dispatch.

## Exports

### `genie/providers/base.py`

- `parse_sse(raw: str) -> str` — parses a raw SSE stream into a single content string.
  Handles TGenie `{"done": true}` and OpenAI `[DONE]` terminators. Falls back to
  `reasoning_content` / `reasonText` when `content` is empty (extended-thinking mode).

### `genie/providers/openai.py`

- `OpenAIProvider` — `Provider` implementation for OpenAI-compatible endpoints
  (OpenAI, Groq, Ollama, LM Studio, etc.).
  - `__init__(cfg: dict)` — accepts the loaded config dict; reads `openaiBaseUrl`,
    `openaiApiKey`, `openaiContentArray`.
  - `name -> str` — returns `"openai"`.
  - `capabilities() -> ProviderCapabilities` — `streaming=True, vision=True,
    tool_calls=False`.
  - `complete(req) -> Iterator[Delta]` — wraps `_call` in a single-item iterator.
  - `complete_text(req) -> str` — calls `_call` directly.
  - `_is_ollama() -> bool` — detects local Ollama by base URL substring.
  - `_call(req) -> str` — routes to `_call_ollama_native` or `_call_openai`.
  - `_call_ollama_native(req) -> str` — uses `/api/chat` native endpoint; sets
    `think=False` and `num_ctx/num_predict=8192`.
  - `_call_openai(req) -> str` — standard `/v1/chat/completions` with SSE streaming;
    splices base64 image parts into the last user message when `req.files` is set.
- `set_debug(enabled: bool) -> None` — toggles module-level `_DEBUG` flag.
- `_history_to_openai(history, content_as_array) -> list[dict]` — converts genie
  session messages to the OpenAI wire format; used by both `openai.py` and
  `anthropic.py`.

### `genie/providers/anthropic.py`

- `AnthropicProvider` — `Provider` implementation for Anthropic-format proxies
  (Cline-style endpoints that expect `system` as a top-level field).
  - `__init__(cfg: dict)` — same config shape as `OpenAIProvider`.
  - `name -> str` — returns `"anthropic"`.
  - `capabilities() -> ProviderCapabilities` — `streaming=True, vision=True,
    tool_calls=False`.
  - `complete(req) -> Iterator[Delta]` / `complete_text(req) -> str` — same thin
    wrappers over `_call`.
  - `_call(req) -> str` — extracts the system message from the message list and
    promotes it to a top-level `"system"` key; otherwise identical SSE/JSON
    fallback path as `OpenAIProvider._call_openai`.
- `set_debug(enabled: bool) -> None`

### `genie/providers/tgenie.py`

- `TGenieProvider` — `Provider` for the internal TGenie multipart/form-data API.
  - `__init__(cfg: dict)` — reads `endpoint`, `authToken`, `frontendUrl`,
    `customHeader`, `cookies`.
  - `name -> str` — returns `"tgenie"`.
  - `capabilities() -> ProviderCapabilities` — `streaming=True, vision=True,
    tool_calls=False`.
  - `complete(req) -> Iterator[Delta]` / `complete_text(req) -> str`
  - `_call(req) -> str` — builds `multipart/form-data` body with `modelName`,
    `messages`, `reasoningEffort` fields; attaches binary file parts when
    `req.files` is set; handles 401 by calling `_refresh_token`.
  - `_refresh_token() -> bool` — runs `grab_auth.py` as a subprocess to renew
    the bearer token.
- `set_debug(enabled: bool) -> None`

## Invariants

- All three concrete providers implement the `Provider` protocol from
  `genie/core/provider.py`; callers depend only on `complete` / `complete_text` /
  `capabilities` / `name`.
- `tool_calls=False` for all providers — tool dispatch is handled entirely by
  `genie/chat.py` via text parsing, not by the LLM API layer.
- `parse_sse` is the single SSE-decoding path shared by all three providers; any
  streaming format change must be made there, not duplicated per provider.
- `AnthropicProvider` reuses `_history_to_openai` from `openai.py` — a circular-
  import risk if `openai.py` ever imports from `anthropic.py`.
- `TGenieProvider` mutates `self._cfg` in-place during token refresh (`_call` calls
  `load()` and does `self._cfg.update(...)`); not thread-safe.
- Providers do not implement retry; callers are responsible for handling
  `RuntimeError` raised on HTTP failures.
- `set_debug` uses a module-level global (`_DEBUG`); calling it from multiple
  threads is not safe.

## Change log

- df1131522263a60bac2a7a0326499f43bc63c490: initial card authored at HEAD
