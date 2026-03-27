"""OpenAI-compatible provider (OpenAI, Groq, Ollama, LM Studio, etc.)."""
from __future__ import annotations

import base64
from typing import Iterator

import requests

from genie.core.provider import CompletionRequest, Delta, ProviderCapabilities
from genie.providers.base import parse_sse

_DEBUG = False


def set_debug(enabled: bool) -> None:
    global _DEBUG
    _DEBUG = enabled


def _dbg(*args) -> None:
    if _DEBUG:
        print("\033[90m  [DEBUG]", *args, "\033[0m")


def _history_to_openai(history: list[dict], content_as_array: bool = False) -> list[dict]:
    msgs = []
    for m in history:
        role = m["role"]
        text = m["content"][0]["text"] if m.get("content") else ""
        if role not in ("user", "assistant", "system"):
            continue
        if content_as_array and role == "user":
            msgs.append({"role": role, "content": [{"type": "text", "text": text}]})
        else:
            msgs.append({"role": role, "content": text})
    return msgs


class OpenAIProvider:
    """Provider for OpenAI-compatible /chat/completions endpoints."""

    def __init__(self, cfg: dict) -> None:
        self._cfg = cfg

    @property
    def name(self) -> str:
        return "openai"

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(streaming=True, vision=True, tool_calls=False)

    def complete(self, req: CompletionRequest) -> Iterator[Delta]:
        text = self._call(req)
        yield Delta(text=text, finish_reason="stop")

    def complete_text(self, req: CompletionRequest) -> str:
        return self._call(req)

    def _call(self, req: CompletionRequest) -> str:
        cfg = self._cfg
        base_url = cfg.get("openaiBaseUrl", "https://api.openai.com/v1").rstrip("/")
        api_key = cfg.get("openaiApiKey", "")
        content_as_array = cfg.get("openaiContentArray", False)

        messages = _history_to_openai(req.messages, content_as_array=content_as_array)

        if req.files:
            image_parts = []
            for f in req.files:
                b64 = base64.b64encode(f["data"]).decode()
                image_parts.append({
                    "type": "image_url",
                    "image_url": {"url": f"data:{f['content_type']};base64,{b64}"},
                })
            for i in range(len(messages) - 1, -1, -1):
                if messages[i]["role"] == "user":
                    raw = messages[i]["content"]
                    text = (
                        next((p["text"] for p in raw if isinstance(p, dict) and p.get("text")), "")
                        if isinstance(raw, list)
                        else (raw if isinstance(raw, str) else "")
                    )
                    messages[i]["content"] = [{"type": "text", "text": text}] + image_parts
                    break

        payload = {
            "model": req.model,
            "messages": messages,
            "stream": True,
            "stream_options": {"include_usage": True},
        }

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        }

        _dbg(f"POST {base_url}/chat/completions  model={req.model}  messages={len(messages)}")

        try:
            resp = requests.post(
                f"{base_url}/chat/completions",
                headers=headers,
                json=payload,
                timeout=120,
                stream=True,
            )
        except requests.RequestException as exc:
            raise RuntimeError(str(exc))

        _dbg(f"HTTP {resp.status_code}  content-type={resp.headers.get('content-type','?')}")
        resp.raise_for_status()

        content_type = resp.headers.get("content-type", "")

        if "text/event-stream" in content_type or "stream" in content_type:
            raw_lines = "\n".join(
                line for line in resp.iter_lines(decode_unicode=True) if line
            )
            result = parse_sse(raw_lines)
            if not result:
                raise RuntimeError(f"SSE stream ended with no content.\nRaw preview:\n{raw_lines[:600]}")
            return result

        raw_lines_list = [line for line in resp.iter_lines(decode_unicode=True) if line]
        raw_text = "\n".join(raw_lines_list).strip()

        if not raw_text:
            raise RuntimeError("Empty response from server (no body)")

        if any(ln.startswith("data:") for ln in raw_lines_list):
            return parse_sse(raw_text)

        try:
            data = resp.json()
        except Exception:
            raise RuntimeError(f"Non-JSON response ({resp.status_code}): {raw_text[:200]}")

        choice = data["choices"][0]
        msg = choice.get("message") or {}
        return (
            msg.get("content")
            or msg.get("reasoning_content")
            or msg.get("refusal")
            or ""
        )
