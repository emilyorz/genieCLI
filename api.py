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
    full = ""
    for line in raw.splitlines():
        line = line.strip()
        if re.match(r'^data:\s*\{"done"\s*:\s*true', line):
            break
        m = re.match(r'^data:\s*(\{.+\})$', line)
        if m:
            try:
                chunk = json.loads(m.group(1))
                delta = chunk["choices"][0]["delta"].get("content", "")
                if delta:
                    full += delta
            except Exception:
                pass
    return full


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
            header = (
                f"--{boundary}{CRLF}"
                f'Content-Disposition: form-data; name="files"; filename="{f['filename']}"{CRLF}'
                f'Content-Type: {f['content_type']}{CRLF}{CRLF}'
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

    resp = session.post(
        f"{cfg['endpoint']}/api/main/oaicompatible",
        headers=headers,
        data=body,
        timeout=120,
        verify=False,
    )
    resp.raise_for_status()
    raw = resp.text
    if raw.lstrip().startswith("data:"):
        return parse_sse(raw)
    parsed = resp.json()
    reply  = parsed["choices"][0].get("message", {}).get("content")
    if not reply:
        reply = parsed["choices"][0].get("delta", {}).get("content")
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
