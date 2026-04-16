# TASK-LEDGER

## Basic Info

- Project: genieCLI v17 — MCP endpoint path fix + registration warning
- Repo Folder: project-iterations/genieCLI/
- Iteration: 17 (continues v15 MCP routing work)
- Owner: Emily (Claude Code)
- Status: done
- Updated: 2026-04-16T10:35+0800
- Focus: Fix MCP probes hitting root "/" instead of "/mcp"; add
  warning on MCP skill registration failure.

## Goal

- One-line summary:
  All MCP probes (doctor, setup check, /trino-research auto-route,
  skill registration) must hit the correct MCP endpoint path, and
  registration failures must be visible.
- Done when:
  1. McpConfig default URL includes /mcp path; ✅
  2. setup_mcp wizard default includes /mcp; ✅
  3. setup_check probe uses configured URL (with path); ✅
  4. genie doctor MCP check hits /mcp; ✅
  5. MCP skill registration logs warning on failure; ✅
  6. 587 tests pass, 0 failures; ✅

## Context (from v15)

v15 R1 diagnosed `/trino-research` as wired to direct Trino only.
Since then, auto-routing was added (chat.py L787-816): probe MCP
first, fall back to direct Trino if unreachable. But the probe hit
`/` (root) instead of `/mcp`, so MCP was always "unreachable" even
when the server was running → silent fallback → users saw "still
uses Trino" after configuring MCP.

## Root cause

`McpConfig.endpoint()` returned the bare URL (`http://localhost:8811`)
with no path. All consumers (doctor, setup_check, auto-route probe,
skill registration) POST to root `/`. MCP Streamable HTTP servers
typically serve at `/mcp`.

## Changes

| File | Change |
|------|--------|
| `genie/skills/mcp_trino/client.py` | Default URL → `http://localhost:8811/mcp` |
| `genie/setup_wizard.py` | Default URL in wizard prompt + setup_check fallback → `/mcp` |
| `genie/skills/mcp_trino/__init__.py` | `register()`: log.warning on probe failure instead of silent return |
| `tests/test_mcp_trino.py` | Updated default URL assertions |
| `tests/test_mcp_research.py` | Updated URL references |
| `tests/test_setup_wizard.py` | Updated setup_mcp test inputs/assertions |

## Verification

- `genie doctor` → MCP probe hits `/mcp` (visible in error URL)
- `genie setup check` → same
- Warning log prints: `MCP Trino at http://localhost:8811/mcp not reachable (...); MCP skills not loaded`
- 587 tests pass, 10 skipped, 0 failures

## Known issues (not in scope)

- `genie setup --help` shows "Unknown target: --help" instead of
  setup-specific help (pre-existing from v16 variadic arg change)
- MCP skill registration warning prints during all commands (skill
  discovery runs in callback) — cosmetic, low priority

## Retro

- **Worked:** Changing the default URL is a 1-line root cause fix
  that cascades to all consumers (doctor, setup_check, auto-route,
  skill registration).
- **Failed:** Nothing — the fix was straightforward once root cause
  was identified.
- **Change next:** v15 R2 (rewire /trino-research to hard-require
  MCP, no fallback) can now proceed since the endpoint path is correct.
  Sam needs to confirm his MCP server is running at 8811/mcp before
  live verification.
