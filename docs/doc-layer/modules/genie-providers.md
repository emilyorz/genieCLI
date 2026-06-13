---
covers:
  - "genie/providers/*.py"
last_synced: "dfeabc1a64fb3dcf297942cf39e4cf5ba55f334b"
---

## Purpose

Concrete LLM backend adapters for genieCLI. Each provider wraps a distinct HTTP
wire protocol — OpenAI-compatible `/v1/chat/completions`, Anthropic-format
(system prompt extracted to a top-level field), Ollama native `/api/chat`, and the
internal TGenie multipart/form-data API — and exposes a uniform interface:
`complete()` (streaming `Iterator[Delta]`) and `complete_text()` (blocking `str`).
`base.py` supplies the shared SSE parser used by every provider.

## Exports

> See exports file: /Users/leeabc/work/emilyorz/genieCLI/docs/doc-layer/exports/genie-providers.md

- `parse_sse`: Shared SSE parser; falls back to reasoning tokens when content is empty.
- `AnthropicProvider`: Extracts `system` message to top-level Anthropic wire field.
- `OpenAIProvider`: Routes to Ollama native path or standard `/v1/chat/completions`.
- `TGenieProvider`: Multipart/form-data adapter; auto-refreshes expired auth tokens.
- `_history_to_openai`: Converts internal message history to OpenAI message list.
- `_is_ollama`: Detects Ollama by inspecting base URL for `localhost:11434` / `ollama`.
- `_refresh_token`: Spawns `grab_auth.py` subprocess on 401; retries the call once.

## Invariants

- `parse_sse` falls back to reasoning tokens when content accumulates to empty — `base.py:47` — `return full or reasoning`
- SSE terminator `data: [DONE]` and `{"done": true}` are both silently skipped — `base.py:24` — `if line == "data: [DONE]" or re.match(r'^data:\s*\{"done"\s*:\s*true', line):`
- `OpenAIProvider` routes Ollama to the native `/api/chat` path, not `/v1` — `openai.py:66` — `if self._is_ollama() and not req.files:`
- Ollama native path sets `stream: False` and `think: False` explicitly — `openai.py:85-86` — `"stream": False,`
- `AnthropicProvider` strips `system` role from the messages array and promotes to top-level payload key — `anthropic.py:88-89` — `if system_text:`
- All three concrete providers declare `tool_calls=False` in their capabilities — `anthropic.py:37` — `return ProviderCapabilities(streaming=True, vision=True, tool_calls=False)`
- `TGenieProvider` on HTTP 401 calls `_refresh_token()` and retries exactly once — `tgenie.py:129-133` — `if resp.status_code == 401:`
- TGenie disables SSL verification unconditionally — `tgenie.py:120` — `verify=False,`
- `urllib3` InsecureRequestWarning is suppressed globally at module import — `tgenie.py:12` — `urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)`
- `_history_to_openai` silently drops messages whose role is not `user`, `assistant`, or `system` — `openai.py:30` — `if role not in ("user", "assistant", "system"):`

## Change log

- dfeabc1a64fb3dcf297942cf39e4cf5ba55f334b: initial card created for doc-bootstrap run
