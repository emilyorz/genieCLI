"""mcp_trino — MCP client skill for genieCLI.

Connects to a Trino MCP server and dynamically registers its tools
as genieCLI skills. Uses JSON-RPC 2.0 over HTTP (Streamable HTTP transport).
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from genie.core.arg import Arg
from genie.core.registry import BaseSkill

from .client import McpClient, McpConfig, load_mcp_config


class McpTrinoSkill(BaseSkill):
    """Dynamically-generated skill wrapping a single MCP tool."""

    group = "mcp_trino"
    tier = "core"

    def __init__(self, tool_def: dict, client: McpClient) -> None:
        self.name = f"mcp_{tool_def['name']}"
        self.description = tool_def.get("description", "")
        self._mcp_tool_name = tool_def["name"]
        self._client = client
        self.args = _build_args(tool_def.get("inputSchema", {}))

    def run(self, **kwargs) -> str:
        return self._client.call_tool(self._mcp_tool_name, kwargs)

    def run_tool(self, name: str, args: dict, ctx: "SkillContext") -> str:
        if name != self.name:
            return f"Unknown tool: '{name}'"
        ok, err = self.validate(args)
        if not ok:
            return f"Validation error for '{name}': {err}"
        try:
            return self.run(**args)
        except Exception as exc:
            return f"MCP tool error ({name}): {exc}"


class McpTrinoStatusSkill(BaseSkill):
    """Show MCP Trino connection status and available tools."""

    name = "mcp_trino_status"
    description = "Show MCP Trino server connection status and available tools."
    group = "mcp_trino"
    tier = "core"
    args = []

    def __init__(self, client: McpClient) -> None:
        self._client = client

    def run(self, **kwargs) -> str:
        try:
            info = self._client.server_info()
            tools = self._client.list_tools()
            tool_names = [t["name"] for t in tools]
            return json.dumps({
                "status": "connected",
                "server": info,
                "url": self._client.config.url,
                "tools": tool_names,
                "tool_count": len(tool_names),
            }, ensure_ascii=False)
        except Exception as exc:
            return json.dumps({
                "status": "error",
                "url": self._client.config.url,
                "error": str(exc),
            }, ensure_ascii=False)


def _build_args(schema: dict) -> list[Arg]:
    """Convert JSON Schema properties to genieCLI Arg list."""
    props = schema.get("properties", {})
    required = set(schema.get("required", []))
    args = []
    for name, prop in props.items():
        json_type = prop.get("type", "string")
        type_map = {"string": "str", "integer": "int", "number": "float",
                     "boolean": "bool", "array": "str", "object": "str"}
        arg_type = type_map.get(json_type, "str")
        enum_vals = prop.get("enum")
        args.append(Arg(
            name=name,
            type=arg_type,
            description=prop.get("description", ""),
            required=name in required,
            default=prop.get("default"),
            choices=enum_vals,
        ))
    return args


def register(registry) -> None:
    """Discover MCP Trino server and register its tools."""
    config = load_mcp_config()
    if not config.enabled:
        return

    client = McpClient(config)

    try:
        tools = client.list_tools()
    except Exception:
        # Server not reachable — skip silently during startup
        return

    # Register each MCP tool as a genieCLI skill
    for tool_def in tools:
        skill = McpTrinoSkill(tool_def, client)
        registry.register(skill)

    # Register status tool
    registry.register(McpTrinoStatusSkill(client))
