# Feature: mcp-banner

> Startup banner showing MCP server reachability status. Shown once when `genie chat` launches.

## Current capability

- When `[mcp.trino].enabled = true` in config, `genie chat` startup probes the configured MCP endpoint with a 200 ms fast-fail timeout (v26; was 3 s in v24–v25).
- Banner shows one of:
  - `mcp    ok http://localhost:8811/mcp (6 tools)` — green (reachable, tool count from handshake)
  - `mcp    offline http://localhost:8811/mcp` — red (unreachable or timeout)
  - `mcp    not configured` — dim (no MCP config)
- Probe runs synchronously on the main thread — cold startup blocks for ≤ ~210 ms on a hanging endpoint (timeout fires on the first handshake request and the exception aborts the chain), and <15 ms on a TCP-refused endpoint. Measured v26 on 2026-04-18 with `GENIE_MCP_TRINO_URL=http://192.0.2.1:8811/mcp` (hang) vs `localhost:1` (refused).
- Exceptions during probe are silently swallowed (banner falls through to `offline`).

## Design log (append-only)

- **v24:** Added the banner probe. Decision: probe synchronously, reuse `doctor`'s 3s timeout. Reason: simplest path to first-pass visibility; keep it symmetric with `genie doctor` so users learn one failure mode. Alternatives considered: (a) async background probe with late banner update — rejected for v24 because Rich console redraw during prompt readline is fragile; (b) skip on first run, cache last-known status — rejected because staleness bugs would hide real connectivity changes. Chose synchronous because v24 scope was visibility, not latency.
- **v26:** Reduced probe timeout from 3 s to 200 ms (fast-fail). Chose fast-fail over threading (the v25-carryover plan) because the v24 design log already flagged threading as fragile (Rich redraw vs readline). Fast-fail is a one-line diff in `chat.py` — measured worst case on a hanging TCP endpoint is ~210 ms (the first handshake request hits the timeout and the exception aborts the rest of the chain), and the common "connection refused" path remains effectively free (~10 ms). Trade-off: very slow-but-eventually-reachable networks will now false-flag `offline` at startup; mitigated by the fact that actual MCP research calls still use `mcp_cfg.timeout` (30 s default), so routing still works even if the banner is pessimistic. Async threading is still on the table if this trade-off bites in practice.

## Limits (append-only)

- **v24:** Does not handle slow networks well — 3s timeout is felt as a freeze on every cold start. Revisit when: user reports startup lag on home network or latency is measured in the wild.
- **v26:** 200 ms probe timeout will mis-classify slow-but-reachable endpoints as `offline` at startup. Revisit by moving to async probe (threading or asyncio) when (a) a real endpoint Sam uses needs >200 ms handshake, or (b) the banner's mis-flag confuses onboarding.

## Open questions

- Should the banner update if MCP becomes reachable mid-session? Currently it's a one-shot at startup — drift goes unnoticed until next launch.
- If MCP is unreachable at startup but `/trino-research` is later invoked, does the routing fall back gracefully? (Behavior exists in chat.py but not explicitly tested.)

## Iteration touchpoints

- **v24:** Added synchronous banner probe with 3s timeout and three-state rendering (ok/offline/not-configured).
- **v25:** No code change — async refactor was the original v25 focus but got pre-empted by `/trino-research` metric-pipeline bugs Sam surfaced at office. The 3s sync timeout still blocks startup. Re-listed as v26 Carryover #1 (⭐ P0 S).
- **v26:** One-line change in `genie/chat.py` — probe timeout hard-set to 200 ms (was `min(mcp_cfg.timeout, 3)` = 3 s). Fast-fail chosen over threading; rationale in Design log v26 entry.
