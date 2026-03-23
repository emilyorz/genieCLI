import json
import re
import uuid
import time
import subprocess
import sys
import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

import config as cfg_module

# Global debug flag — set via main.py --debug
DEBUG = False

def _dbg(*args):
    if DEBUG:
        print("\033[90m  [DEBUG]", *args, "\033[0m")

# ─────────────────────────────────────────────────────────────────────────────
# OpenAI-compatible interface
# ─────────────────────────────────────────────────────────────────────────────

def _history_to_openai(history: list, content_as_array: bool = False) -> list:
    """
    Convert TGenie history format → OpenAI messages list.

    content_as_array=True: user messages use array format
      [{"type": "text", "text": "..."}]
    This is required by some internal proxies (e.g. Cline-style servers)
    that expect the Cline wire format.
    """
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


def _send_openai(cfg: dict, history: list, model: str, files: list = None) -> str:
    """
    Call any OpenAI-compatible or Anthropic-compatible API.

    interface=openai  : standard OpenAI /chat/completions format (default)
    interface=anthropic: Anthropic format — system extracted to top-level field,
                         used by Cline-style internal proxies
    """
    base_url  = cfg.get("openaiBaseUrl", "https://api.openai.com/v1").rstrip("/")
    api_key   = cfg.get("openaiApiKey", "")
    interface        = cfg.get("interface", "openai")
    content_as_array = cfg.get("openaiContentArray", False)

    messages = _history_to_openai(history, content_as_array=content_as_array)

    # Attach screenshot to the last user message if provided
    if files:
        import base64 as _b64
        image_parts = []
        for f in files:
            b64 = _b64.b64encode(f["data"]).decode()
            image_parts.append({
                "type": "image_url",
                "image_url": {"url": f"data:{f['content_type']};base64,{b64}"},
            })
        # Find last user message and convert to multipart content
        for i in range(len(messages) - 1, -1, -1):
            if messages[i]["role"] == "user":
                text_part = {"type": "text", "text": messages[i]["content"]}
                messages[i]["content"] = [text_part] + image_parts
                break

    # Anthropic format: extract system message to top-level field
    if interface == "anthropic":
        system_text = ""
        non_system  = []
        for m in messages:
            if m["role"] == "system":
                system_text = m["content"]
            else:
                non_system.append(m)
        payload = {"model": model, "messages": non_system}
        if system_text:
            payload["system"] = system_text
    else:
        # Standard OpenAI format — always request stream so server
        # returns SSE regardless of its default behaviour
        payload = {
            "model":          model,
            "messages":       messages,
            "stream":         True,
            "stream_options": {"include_usage": True},
        }

    headers = {
        "Content-Type":  "application/json",
        "Authorization": f"Bearer {api_key}",
    }

    _dbg(f"POST {base_url}/chat/completions")
    _dbg(f"model={model}  messages={len(messages)}")
    if DEBUG:
        for m in messages[-3:]:  # last 3 only to keep output sane
            role    = m["role"]
            content = m["content"] if isinstance(m["content"], str) else "[multipart]"
            _dbg(f"  [{role}] {str(content)[:120]}")

    # Always use stream=True so we can handle both SSE and JSON responses
    # correctly regardless of what the server decides to send back
    resp = requests.post(
        f"{base_url}/chat/completions",
        headers=headers,
        json=payload,
        timeout=120,
        stream=True,
    )

    _dbg(f"HTTP {resp.status_code}  content-type={resp.headers.get('content-type','?')}")
    resp.raise_for_status()

    content_type = resp.headers.get("content-type", "")

    # SSE stream (server-sent events) — read line by line
    if "text/event-stream" in content_type or "stream" in content_type:
        _dbg("SSE stream detected, reading with iter_lines()")
        raw_lines = "\n".join(
            line for line in resp.iter_lines(decode_unicode=True) if line
        )
        _dbg(f"SSE raw ({len(raw_lines)} chars):\n{raw_lines[:600]}")
        result = parse_sse(raw_lines)
        if not result:
            raise RuntimeError(
                f"SSE stream ended with no content.\n"
                f"Raw preview:\n{raw_lines[:600]}"
            )
        return result

    # Regular JSON response
    raw_text = resp.text.strip()
    _dbg(f"body preview: {raw_text[:300]}")

    if not raw_text:
        raise RuntimeError("Empty response from server (no body)")

    # Fallback: some servers send SSE without correct content-type header
    if raw_text.startswith("data:"):
        _dbg("Detected SSE body (no SSE content-type), switching to parse_sse()")
        return parse_sse(raw_text)

    try:
        data = resp.json()
    except Exception:
        raise RuntimeError(f"Non-JSON response ({resp.status_code}): {raw_text[:200]}")

    _dbg(f"parsed JSON keys: {list(data.keys())}")

    choice = data["choices"][0]
    msg    = choice.get("message") or {}
    # content may be None when model returns tool_calls or only reasoning tokens
    content = (
        msg.get("content")
        or msg.get("reasoning_content")   # some providers surface reasoning here
        or msg.get("refusal")             # surface refusal message if present
        or ""
    )
    return content


def new_msg(role: str, text: str) -> dict:
    return {
        "id":         str(uuid.uuid4()),
        "role":       role,
        "content":    [{"type": "text", "text": text, "reasonText": None}],
        "form":       "text",
        "timestamp":  int(time.time() * 1000),
        "tokenCount": max(1, len(text) // 4),
    }


def parse_sse(raw: str) -> str:
    """
    Parse SSE stream from TGenie backend.
    Collects delta.content (final answer).
    When reasoning is enabled the server may also emit delta.reasoning_content
    (thinking tokens) — those are skipped; we only want the answer.
    If content is completely empty after the full stream, fall back to
    collecting reasoning_content so the caller at least gets something.
    """
    full      = ""
    reasoning = ""
    for line in raw.splitlines():
        line = line.strip()
        if re.match(r'^data:\s*\{"done"\s*:\s*true', line):
            break
        m = re.match(r'^data:\s*(\{.+\})$', line)
        if m:
            try:
                chunk = json.loads(m.group(1))
                delta = chunk["choices"][0]["delta"]
                # Primary: final answer content
                text = delta.get("content") or ""
                if text:
                    full += text
                # Secondary: reasoning/thinking tokens (collect as fallback)
                rtext = delta.get("reasoning_content") or delta.get("reasonText") or ""
                if rtext:
                    reasoning += rtext
            except Exception:
                pass
    # If server only sent reasoning tokens and no content, surface reasoning
    # so the user sees something instead of [ERROR] Empty response
    return full or reasoning


def _do_request(cfg: dict, history: list, model: str, reasoning: str, files: list = None) -> str:
    """
    files: list of dicts with keys: filename, content_type, data (bytes)
    e.g. [{"filename": "screenshot.png", "content_type": "image/png", "data": b"..."}]
    """
    messages_json = json.dumps(history, ensure_ascii=False)
    boundary      = "----WebKitFormBoundary" + uuid.uuid4().hex[:16]
    CRLF          = "\r\n"

    # Build body as bytes to support binary file data
    parts = []

    # modelName
    parts.append(
        f"--{boundary}{CRLF}"
        f'Content-Disposition: form-data; name="modelName"{CRLF}{CRLF}'
        f"{model}{CRLF}"
    )
    # messages
    parts.append(
        f"--{boundary}{CRLF}"
        f'Content-Disposition: form-data; name="messages"{CRLF}{CRLF}'
        f"{messages_json}{CRLF}"
    )
    # reasoningEffort
    parts.append(
        f"--{boundary}{CRLF}"
        f'Content-Disposition: form-data; name="reasoningEffort"{CRLF}{CRLF}'
        f"{reasoning}{CRLF}"
    )

    body = "".join(parts).encode("utf-8")

    # files (binary)
    if files:
        for f in files:
            fname    = f["filename"]
            fctype   = f["content_type"]
            header = (
                f"--{boundary}{CRLF}"
                f'Content-Disposition: form-data; name="files"; filename="{fname}"{CRLF}'
                f'Content-Type: {fctype}{CRLF}{CRLF}'
            ).encode("utf-8")
            body += header + f["data"] + CRLF.encode("utf-8")

    body += f"--{boundary}--{CRLF}".encode("utf-8")

    authority = cfg["endpoint"].replace("https://", "")
    headers = {
        "authority":          authority,
        "accept":             "*/*",
        "accept-encoding":    "gzip, deflate, br",
        "accept-language":    "zh-TW,zh;q=0.9,en-US;q=0.8,en;q=0.7",
        "authorization":      f"Bearer {cfg['authToken']}",
        "origin":             cfg["frontendUrl"],
        "referer":            cfg["frontendUrl"] + "/",
        "sec-ch-ua":          '"Not:A-Brand";v="99", "Google Chrome";v="131"',
        "sec-ch-ua-mobile":   "?0",
        "sec-ch-ua-platform": '"Windows"',
        "sec-fetch-dest":     "empty",
        "sec-fetch-mode":     "cors",
        "sec-fetch-site":     "same-site",
        "content-type":       f"multipart/form-data; boundary={boundary}",
        "user-agent":         "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    }
    if cfg.get("customHeader"):
        headers["x-custom-header"] = cfg["customHeader"]

    session = requests.Session()
    for ck in cfg.get("cookies", []):
        session.cookies.set(ck["name"], ck["value"], domain=ck["domain"])

    _dbg(f"POST {cfg['endpoint']}/api/main/oaicompatible")
    _dbg(f"model={model}  reasoning={reasoning}  body={len(body)}b")

    resp = session.post(
        f"{cfg['endpoint']}/api/main/oaicompatible",
        headers=headers,
        data=body,
        timeout=120,
        verify=False,
    )

    _dbg(f"HTTP {resp.status_code}  content-type={resp.headers.get('content-type','?')}")
    _dbg(f"body preview: {resp.text[:300]}")

    resp.raise_for_status()
    raw = resp.text
    if raw.lstrip().startswith("data:"):
        return parse_sse(raw)
    parsed = resp.json()
    choice = parsed["choices"][0]
    msg    = choice.get("message") or choice.get("delta") or {}
    reply  = (
        msg.get("content")
        or msg.get("reasoning_content")
        or msg.get("reasonText")
        or ""
    )
    return reply


def _refresh_token() -> bool:
    """Run grab_auth.py to get a fresh token. Returns True if successful."""
    print("\n  [Auth] Token expired, refreshing...")
    try:
        result = subprocess.run(
            [sys.executable, "grab_auth.py"],
            capture_output=False,
            timeout=60,
        )
        return result.returncode == 0
    except Exception as e:
        print(f"  [Auth] Refresh failed: {e}")
        return False


def send(cfg: dict, history: list, model: str, reasoning: str, files: list = None) -> str:
    interface = cfg.get("interface", "tgenie")

    # ── OpenAI-compatible path ─────────────────────────────────────────────
    if interface == "openai":
        try:
            return _send_openai(cfg, history, model, files)
        except requests.HTTPError as e:
            raise RuntimeError(f"OpenAI HTTP {e.response.status_code}: {e.response.text[:300]}")
        except Exception as e:
            raise RuntimeError(str(e))

    # ── TGenie backend path (default) ──────────────────────────────────────
    try:
        return _do_request(cfg, history, model, reasoning, files)

    except requests.HTTPError as e:
        if e.response is not None and e.response.status_code == 401:
            # Token expired — refresh and retry once
            if _refresh_token():
                # Reload config with new token
                new_cfg = cfg_module.load()
                cfg.update(new_cfg)
                print("  [Auth] Token refreshed, retrying...")
                try:
                    return _do_request(cfg, history, model, reasoning, files)
                except Exception as e2:
                    raise RuntimeError(f"Retry failed: {e2}")
            else:
                raise RuntimeError("Token refresh failed. Run grab_auth.py manually.")
        raise RuntimeError(f"HTTP {e.response.status_code}: {e.response.text[:200]}")

    except Exception as e:
        raise RuntimeError(str(e))
