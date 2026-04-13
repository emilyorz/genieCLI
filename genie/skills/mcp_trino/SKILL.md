---
name: mcp-trino
description: >-
  MCP client for Trino — connects to a Trino MCP server and exposes its
  tools as genieCLI skills. Supports dynamic tool discovery via MCP protocol.
version: 1.0.0
group: mcp_trino
tier: core
---

# MCP Trino Client

Connects to a Trino MCP server (JSON-RPC 2.0 over HTTP/SSE) and dynamically
registers all tools the server exposes as genieCLI skills.

## Configuration

Add to `~/.genie/config.toml`:

```toml
[mcp.trino]
url = "http://localhost:8811"
enabled = true
```

Or set environment variables:

```bash
export GENIE_MCP_TRINO_URL=http://localhost:8811
```
