"""MCP client — JSON-RPC 2.0 over HTTP (Streamable HTTP transport).

Supports the MCP Streamable HTTP transport:
- POST to server URL with JSON-RPC 2.0 requests
- Session management via Mcp-Session-Id header
- Fallback to legacy /sse endpoint if needed
"""
from __future__ import annotations

import json
import os
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import requests

# Config path — same directory as trino.json
_CONFIG_DIR = Path.home() / ".config" / "genie"
_MCP_CONFIG_PATH = _CONFIG_DIR / "mcp.json"

# Also check ~/.genie/config.toml for [mcp.trino] section
_TOML_PATH = Path.home() / ".genie" / "config.toml"


@dataclass
class McpConfig:
    """MCP server connection config."""
    url: str = "http://localhost:8811"
    enabled: bool = True
    timeout: int = 30

    def endpoint(self) -> str:
        """Return the base URL (no trailing slash)."""
        return self.url.rstrip("/")


def load_mcp_config() -> McpConfig:
    """Load MCP Trino config from multiple sources (highest priority first):

    1. Environment variables: GENIE_MCP_TRINO_URL, GENIE_MCP_TRINO_ENABLED
    2. TOML: ~/.genie/config.toml [mcp.trino] section
    3. JSON: ~/.config/genie/mcp.json
    4. Defaults
    """
    config = McpConfig()

    # 1. JSON config
    if _MCP_CONFIG_PATH.exists():
        try:
            data = json.loads(_MCP_CONFIG_PATH.read_text(encoding="utf-8"))
            trino_cfg = data.get("trino", {})
            if "url" in trino_cfg:
                config.url = trino_cfg["url"]
            if "enabled" in trino_cfg:
                config.enabled = bool(trino_cfg["enabled"])
            if "timeout" in trino_cfg:
                config.timeout = int(trino_cfg["timeout"])
        except Exception:
            pass

    # 2. TOML config (overrides JSON)
    if _TOML_PATH.exists():
        try:
            try:
                import tomllib
            except ImportError:
                import tomli as tomllib
            data = tomllib.loads(_TOML_PATH.read_text(encoding="utf-8"))
            mcp_section = data.get("mcp", {})
            trino_cfg = mcp_section.get("trino", {})
            if "url" in trino_cfg:
                config.url = trino_cfg["url"]
            if "enabled" in trino_cfg:
                config.enabled = bool(trino_cfg["enabled"])
            if "timeout" in trino_cfg:
                config.timeout = int(trino_cfg["timeout"])
        except Exception:
            pass

    # 3. Environment variables (highest priority)
    env_url = os.environ.get("GENIE_MCP_TRINO_URL")
    if env_url:
        config.url = env_url
    env_enabled = os.environ.get("GENIE_MCP_TRINO_ENABLED")
    if env_enabled is not None:
        config.enabled = env_enabled.lower() in ("1", "true", "yes")

    return config


def save_mcp_config(config: McpConfig) -> None:
    """Save MCP config to ~/.config/genie/mcp.json."""
    _CONFIG_DIR.mkdir(parents=True, exist_ok=True)

    data = {}
    if _MCP_CONFIG_PATH.exists():
        try:
            data = json.loads(_MCP_CONFIG_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass

    data["trino"] = {
        "url": config.url,
        "enabled": config.enabled,
        "timeout": config.timeout,
    }
    _MCP_CONFIG_PATH.write_text(
        json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
    )


class McpClient:
    """MCP client using Streamable HTTP transport (JSON-RPC 2.0 over HTTP POST)."""

    def __init__(self, config: McpConfig) -> None:
        self.config = config
        self._session_id: str | None = None
        self._request_id = 0
        self._http = requests.Session()
        self._http.headers.update({
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        })
        self._initialized = False

    def _next_id(self) -> int:
        self._request_id += 1
        return self._request_id

    def _post(self, method: str, params: dict | None = None) -> Any:
        """Send a JSON-RPC 2.0 request and return the result."""
        payload = {
            "jsonrpc": "2.0",
            "id": self._next_id(),
            "method": method,
        }
        if params is not None:
            payload["params"] = params

        headers = {}
        if self._session_id:
            headers["Mcp-Session-Id"] = self._session_id

        resp = self._http.post(
            self.config.endpoint(),
            json=payload,
            headers=headers,
            timeout=self.config.timeout,
        )

        # Capture session ID from response
        session_id = resp.headers.get("Mcp-Session-Id")
        if session_id:
            self._session_id = session_id

        content_type = resp.headers.get("Content-Type", "")

        if "text/event-stream" in content_type:
            return self._parse_sse_response(resp.text)

        resp.raise_for_status()
        body = resp.json()

        if "error" in body:
            err = body["error"]
            raise McpError(err.get("code", -1), err.get("message", "Unknown MCP error"))

        return body.get("result")

    def _parse_sse_response(self, text: str) -> Any:
        """Parse SSE stream and extract the JSON-RPC result."""
        result = None
        for line in text.split("\n"):
            line = line.strip()
            if line.startswith("data: "):
                data_str = line[6:]
                try:
                    data = json.loads(data_str)
                    if "result" in data:
                        result = data["result"]
                    elif "error" in data:
                        err = data["error"]
                        raise McpError(err.get("code", -1), err.get("message", ""))
                except json.JSONDecodeError:
                    continue
        return result

    def _ensure_initialized(self) -> None:
        """Send initialize request if not already done."""
        if self._initialized:
            return
        result = self._post("initialize", {
            "protocolVersion": "2025-03-26",
            "capabilities": {},
            "clientInfo": {
                "name": "genieCLI",
                "version": "5.0.0",
            },
        })
        # Send initialized notification
        self._http.post(
            self.config.endpoint(),
            json={"jsonrpc": "2.0", "method": "notifications/initialized"},
            headers={"Mcp-Session-Id": self._session_id} if self._session_id else {},
            timeout=self.config.timeout,
        )
        self._initialized = True

    def server_info(self) -> dict:
        """Get server info from initialize response."""
        self._ensure_initialized()
        result = self._post("initialize", {
            "protocolVersion": "2025-03-26",
            "capabilities": {},
            "clientInfo": {"name": "genieCLI", "version": "5.0.0"},
        })
        return result or {}

    def list_tools(self) -> list[dict]:
        """Discover available tools from the MCP server."""
        self._ensure_initialized()
        result = self._post("tools/list")
        if isinstance(result, dict):
            return result.get("tools", [])
        return result or []

    def call_tool(self, name: str, arguments: dict) -> str:
        """Call an MCP tool and return the result as a string."""
        self._ensure_initialized()
        result = self._post("tools/call", {
            "name": name,
            "arguments": arguments,
        })
        if result is None:
            return "null"
        # MCP tools return content array
        if isinstance(result, dict) and "content" in result:
            parts = []
            for item in result["content"]:
                if item.get("type") == "text":
                    parts.append(item.get("text", ""))
                elif item.get("type") == "resource":
                    parts.append(json.dumps(item, ensure_ascii=False))
                else:
                    parts.append(json.dumps(item, ensure_ascii=False))
            return "\n".join(parts) if parts else json.dumps(result, ensure_ascii=False)
        return json.dumps(result, ensure_ascii=False) if not isinstance(result, str) else result


class McpError(Exception):
    """MCP JSON-RPC error."""

    def __init__(self, code: int, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(f"MCP error {code}: {message}")
