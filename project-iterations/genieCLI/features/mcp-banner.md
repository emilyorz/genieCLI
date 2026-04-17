# Feature: mcp-banner

> Startup banner showing MCP server reachability status. Shown once when `genie chat` launches.

## Current capability

- When `[mcp.trino].enabled = true` in config, `genie chat` startup probes the configured MCP endpoint with a 3-second timeout.
- Banner shows one of:
  - `mcp    ok http://localhost:8811/mcp (6 tools)` — green (reachable, tool count from handshake)
  - `mcp    offline http://localhost:8811/mcp` — red (unreachable or timeout)
  - `mcp    not configured` — dim (no MCP config)
- Probe runs synchronously on the main thread — cold startup with an unreachable endpoint blocks for ~3000 ms.
- Exceptions during probe are silently swallowed (banner falls through to `offline`).

## Design log (append-only)

- **v24:** Added the banner probe. Decision: probe synchronously, reuse `doctor`'s 3s timeout. Reason: simplest path to first-pass visibility; keep it symmetric with `genie doctor` so users learn one failure mode. Alternatives considered: (a) async background probe with late banner update — rejected for v24 because Rich console redraw during prompt readline is fragile; (b) skip on first run, cache last-known status — rejected because staleness bugs would hide real connectivity changes. Chose synchronous because v24 scope was visibility, not latency.

## Limits (append-only)

- **v24:** Does not handle slow networks well — 3s timeout is felt as a freeze on every cold start. Revisit when: user reports startup lag on home network or latency is measured in the wild.

## Open questions

- Should the banner update if MCP becomes reachable mid-session? Currently it's a one-shot at startup — drift goes unnoticed until next launch.
- If MCP is unreachable at startup but `/trino-research` is later invoked, does the routing fall back gracefully? (Behavior exists in chat.py but not explicitly tested.)

## Iteration touchpoints

- **v24:** Added synchronous banner probe with 3s timeout and three-state rendering (ok/offline/not-configured).
