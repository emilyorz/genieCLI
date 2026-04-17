#!/usr/bin/env python3
"""Debug helper: list MCP-Trino tools and show which one _resolve_query_tool picks.

Run from the genieCLI repo root:
    python scripts/debug-mcp-tools.py

Used to diagnose the "metrics-all-zero" symptom — if the resolver picks
explain_query (which doesn't actually execute the SQL), every measured run
returns CPU/Memory/Rows = 0 and the optimizer can't rank candidates.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, is_dataclass
from typing import Any

from genie.skills.mcp_trino.client import McpClient, load_mcp_config
from genie.skills.mcp_trino.research import (
    _execute_via_mcp,
    _find_sql_param,
    _resolve_query_tool,
)


def _dump(label: str, value: Any) -> None:
    print(f"---- {label} ----")
    if is_dataclass(value):
        value = asdict(value)
    if isinstance(value, (dict, list)):
        print(json.dumps(value, indent=2, default=str))
    else:
        print(repr(value))
    print()


def _probe(client: McpClient) -> None:
    print("=" * 78)
    print("PROBE MODE — running real queries to inspect server response shape")
    print("=" * 78)
    print()

    for label, sql in (
        ("SELECT 1", "SELECT 1"),
        ("EXPLAIN ANALYZE SELECT 1", "EXPLAIN ANALYZE SELECT 1"),
    ):
        print(f">>> {label}")
        try:
            result = _execute_via_mcp(client, sql)
        except Exception as exc:
            print(f"  FAILED: {exc!r}")
            print()
            continue

        # _execute_via_mcp returns dict with rows/columns/row_count/metrics/error/raw
        _dump("rows", result.get("rows"))
        _dump("columns", result.get("columns"))
        _dump("row_count", result.get("row_count"))
        _dump("metrics (parsed)", result.get("metrics"))
        _dump("error", result.get("error"))

        raw = result.get("raw")
        # Try to parse raw as JSON for clearer key inspection; fall back to text
        try:
            parsed_raw = json.loads(raw) if isinstance(raw, str) else raw
        except (json.JSONDecodeError, TypeError):
            parsed_raw = {"_unparsed_text": raw}
        _dump("raw response (parsed)", parsed_raw)
        print("=" * 78)
        print()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--probe",
        action="store_true",
        help="After listing tools, run SELECT 1 and EXPLAIN ANALYZE SELECT 1 to dump raw response shape.",
    )
    args = parser.parse_args()

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

    if args.probe:
        print()
        _probe(client)

    return 0


if __name__ == "__main__":
    sys.exit(main())
