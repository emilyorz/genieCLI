"""TGenie backend provider (internal multipart/form-data API)."""
from __future__ import annotations

import subprocess
import sys
import uuid
from typing import Iterator

import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

from genie.core.provider import CompletionRequest, Delta, ProviderCapabilities
from genie.providers.base import parse_sse

_DEBUG = False


def set_debug(enabled: bool) -> None:
    global _DEBUG
    _DEBUG = enabled


def _dbg(*args) -> None:
    if _DEBUG:
        print("\033[90m  [DEBUG]", *args, "\033[0m")


class TGenieProvider:
    """Provider that calls the internal TGenie multipart API."""

    def __init__(self, cfg: dict) -> None:
        self._cfg = cfg

    @property
    def name(self) -> str:
        return "tgenie"

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(streaming=True, vision=True, tool_calls=False)

    def complete(self, req: CompletionRequest) -> Iterator[Delta]:
        text = self._call(req)
        yield Delta(text=text, finish_reason="stop")

    def complete_text(self, req: CompletionRequest) -> str:
        return self._call(req)

    def _call(self, req: CompletionRequest) -> str:
        cfg = self._cfg
        messages_json = __import__("json").dumps(req.messages, ensure_ascii=False)
        boundary = "----WebKitFormBoundary" + uuid.uuid4().hex[:16]
        CRLF = "\r\n"

        parts = []
        parts.append(
            f"--{boundary}{CRLF}"
            f'Content-Disposition: form-data; name="modelName"{CRLF}{CRLF}'
            f"{req.model}{CRLF}"
        )
        parts.append(
            f"--{boundary}{CRLF}"
            f'Content-Disposition: form-data; name="messages"{CRLF}{CRLF}'
            f"{messages_json}{CRLF}"
        )
        parts.append(
            f"--{boundary}{CRLF}"
            f'Content-Disposition: form-data; name="reasoningEffort"{CRLF}{CRLF}'
            f"{req.reasoning}{CRLF}"
        )

        body = "".join(parts).encode("utf-8")

        if req.files:
            for f in req.files:
                header = (
                    f"--{boundary}{CRLF}"
                    f'Content-Disposition: form-data; name="files"; filename="{f["filename"]}"{CRLF}'
                    f'Content-Type: {f["content_type"]}{CRLF}{CRLF}'
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
        _dbg(f"model={req.model}  reasoning={req.reasoning}  body={len(body)}b")

        try:
            resp = session.post(
                f"{cfg['endpoint']}/api/main/oaicompatible",
                headers=headers,
                data=body,
                timeout=120,
                verify=False,
            )
        except requests.RequestException as exc:
            raise RuntimeError(str(exc))

        _dbg(f"HTTP {resp.status_code}  content-type={resp.headers.get('content-type','?')}")
        _dbg(f"body preview: {resp.text[:300]}")

        if resp.status_code == 401:
            if self._refresh_token():
                from genie.core.config import load
                new_cfg = load()
                self._cfg.update(new_cfg)
                return self._call(req)
            raise RuntimeError("Token refresh failed. Run grab_auth.py manually.")

        resp.raise_for_status()
        raw = resp.text
        if raw.lstrip().startswith("data:"):
            return parse_sse(raw)
        parsed = resp.json()
        choice = parsed["choices"][0]
        msg = choice.get("message") or choice.get("delta") or {}
        return (
            msg.get("content")
            or msg.get("reasoning_content")
            or msg.get("reasonText")
            or ""
        )

    def _refresh_token(self) -> bool:
        print("\n  [Auth] Token expired, refreshing...")
        try:
            result = subprocess.run(
                [sys.executable, "grab_auth.py"],
                capture_output=False,
                timeout=60,
            )
            return result.returncode == 0
        except Exception as exc:
            print(f"  [Auth] Refresh failed: {exc}")
            return False
