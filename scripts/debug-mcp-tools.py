#!/usr/bin/env python3
"""Debug helper: list MCP-Trino tools and show which one _resolve_query_tool picks.

Run from the genieCLI repo root:
    python scripts/debug-mcp-tools.py

Used to diagnose the "metrics-all-zero" symptom — if the resolver picks
explain_query (which doesn't actually execute the SQL), every measured run
returns CPU/Memory/Rows = 0 and the optimizer can't rank candidates.
"""

from __future__ import annotations

import json
import sys

from genie.skills.mcp_trino.client import McpClient, load_mcp_config
from genie.skills.mcp_trino.research import (
    _resolve_query_tool,
    _find_sql_param,
)


def main() -> int:
    cfg = load_mcp_config()
    if not cfg.enabled:
        print("MCP not enabled in config. Edit your config.toml and set [mcp.trino].enabled = true.", file=sys.stderr)
        return 1

    print(f"MCP server: {cfg.url}")
    print()

    client = McpClient(cfg)
    try:
        tools = client.list_tools()
    except Exception as exc:
        print(f"FAILED to list tools: {exc}", file=sys.stderr)
        return 2

    if not tools:
        print("Server returned an empty tool list.", file=sys.stderr)
        return 3

    print(f"Tools exposed by server: {len(tools)}")
    print("-" * 78)
    for t in tools:
        name = t.get("name", "?")
        sql_param = _find_sql_param(t)
        props = t.get("inputSchema", {}).get("properties", {})
        prop_names = sorted(props.keys())
        print(f"  {name:30s}  sql_param={sql_param!r:12s}  all_params={prop_names}")
    print()

    resolved = _resolve_query_tool(client)
    tool_name, sql_param = resolved
    print(f"_resolve_query_tool picks: tool_name={tool_name!r}  sql_param={sql_param!r}")
    print()

    candidates = ("query", "trino_query", "execute", "execute_query", "run_query")
    if tool_name in candidates:
        print(f"OK: picked from explicit candidates list. Should execute SQL for real.")
    elif "explain" in tool_name.lower():
        print(f"BUG CONFIRMED: picked an explain-class tool. It returns the plan only,")
        print(f"               so _measure_mcp will see CPU/Memory/Rows = 0.")
        print(f"               Fix: extend candidates to match prefixed names")
        print(f"               (e.g. endswith(c)) and exclude explain*/describe*/show*")
        print(f"               from the greedy fallback in _resolve_query_tool.")
    else:
        print(f"UNEXPECTED: picked {tool_name!r} via fallback. Verify it actually runs the SQL.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
