# TASK-LEDGER

## Basic Info

- Project: genieCLI v17 — MCP full integration sprint (endpoint + routing + discovery)
- Repo Folder: project-iterations/genieCLI/
- Iteration: 17 (continues v15 MCP routing work; also includes v16 CLI fix)
- Owner: Emily (Claude Code)
- Status: done
- Updated: 2026-04-16T11:48+0800
- Focus: Fix all MCP integration issues end-to-end — from CLI routing
  to endpoint path to hard-require MCP to dynamic tool/param discovery.

## Goal

- One-line summary:
  `/trino-research` must work end-to-end through MCP with zero
  hardcoded assumptions about server topology or tool naming.
- Done when:
  1. `genie setup` and all subcommands route correctly; ✅ (PR #33)
  2. MCP probes hit correct endpoint path; ✅ (PR #35)
  3. `/trino-research` hard-requires MCP, no silent fallback; ✅ (PR #36)
  4. MCP tool name discovered dynamically from server; ✅ (PR #37)
  5. MCP parameter name discovered dynamically from schema; ✅ (PR #38)
  6. MCP skill registration warns on failure; ✅ (PR #35)
  7. 587 tests pass after all changes; ✅

## Round 1 — CLI subcommand routing (PR #33)

### Trigger

Sam ran `genie setup` → "File not found: setup"

### Root cause

Callback positional `target: Optional[str]` consumed subcommand names.
`genie setup` → target="setup" → file-path branch → error.
`genie setup trino` → target="setup", "trino" orphaned → "No such command".

Shim existed for doctor/verify but not setup.

### Fix

Changed to variadic `args: Optional[list[str]]` so all positionals
captured. Shim reads `args[0]` as target, `args[1]` as sub-target.

### Changed files

- `genie/cli.py` — callback signature + shim routing

### Verification

- All 12 invocation patterns verified (setup/setup trino/setup mcp/
  setup check/doctor/verify/sessions/config/tools/file.sql/chat/--version)
- 587 tests pass

---

## Round 2 — MCP endpoint path + registration warning (PR #35)

### Trigger

Sam noticed `genie doctor` MCP check hits root "/" → 404.

### Root cause

`McpConfig.endpoint()` returned bare URL `http://localhost:8811`.
All consumers (doctor, setup_check, auto-route, skill registration)
POSTed to `/`. MCP Streamable HTTP servers serve at `/mcp`.

### Fix

- Default URL → `http://localhost:8811/mcp`
- `setup_mcp` wizard default → `/mcp`
- `setup_check` probe uses configured URL
- `mcp_trino/__init__.py register()`: log.warning on failure instead
  of silent return

### Changed files

- `genie/skills/mcp_trino/client.py` — default URL
- `genie/setup_wizard.py` — wizard default + setup_check
- `genie/skills/mcp_trino/__init__.py` — warning log
- `tests/test_mcp_trino.py`, `tests/test_mcp_research.py`,
  `tests/test_setup_wizard.py` — updated URL references

### Verification

- `genie doctor` → URL shows `/mcp`
- Warning log visible: `MCP Trino at .../mcp not reachable (...); MCP skills not loaded`
- 587 tests pass

---

## Round 3 — Hard-require MCP for /trino-research (PR #36)

### Trigger

Sam: "先硬性要求 MCP for trino-research"

### Context

v15 R2 goal. Auto-routing existed (chat.py L787-816) but silently
fell back to direct Trino when MCP was unreachable. With endpoint
path fixed (R2), the fallback is no longer needed.

### Fix

Replaced auto-route + silent fallback with:
- MCP not configured → error: "Run: genie setup mcp"
- MCP unreachable → error with URL + hint to use `--direct`
- `--direct` flag preserved as explicit opt-in for direct Trino

### Changed files

- `genie/chat.py` — `/trino-research` routing logic + help text

### Verification

- 587 tests pass

---

## Round 4 — Dynamic tool name discovery (PR #37)

### Trigger

Sam tested `/trino-research` → MCP error: `tool 'query' not found`.

### Root cause

`_execute_via_mcp` hardcoded `call_tool("query", {"sql": sql})`.
Sam's MCP server exposes `execute_query`, not `query`.

### Fix

New `_resolve_query_tool()`:
1. Check known names: query, trino_query, execute, execute_query, run_query
2. Fallback: scan inputSchema for tool with "sql" property
3. No match → actionable error listing available tools
4. Cache result for session

### Changed files

- `genie/skills/mcp_trino/research.py` — `_resolve_query_tool()`
- `tests/test_mcp_research.py` — mock `list_tools`

### Verification

- 587 tests pass

---

## Round 5 — Dynamic parameter name discovery (PR #38)

### Trigger

After R4, realized `call_tool(name, {"sql": sql})` still hardcodes
param name. Sam's `execute_query` might use `query` not `sql`.

### Fix

`_resolve_query_tool()` now returns `(tool_name, sql_param_name)`.
`_find_sql_param()` checks inputSchema for `sql`, `query`, `statement`.

### Changed files

- `genie/skills/mcp_trino/research.py` — `_find_sql_param()`, updated
  `_resolve_query_tool()` return type
- `tests/test_mcp_research.py` — updated reset field name

### Verification

- 587 tests pass

---

## Sam's MCP Server (discovered 2026-04-16)

Tools available on Sam's MCP Trino server at localhost:8811/mcp:

| Tool | Description |
|------|-------------|
| execute_query | Execute SQL queries on Trino |
| explain_query | Analyze query execution plans |
| get_table_schema | Inspect table structure and columns |
| list_catalogs | Discover available catalogs |
| list_schemas | Browse schemas within a catalog |
| list_tables | Discover tables and views |

`_resolve_query_tool()` will match `execute_query` from the known
names list.

## Retro

- **Worked:** Iterative debugging with Sam testing in real time.
  Each round surfaced the next issue (routing → endpoint → fallback
  → tool name → param name). Fixing incrementally and merging after
  each round kept the feedback loop tight.
- **Failed:** Could have anticipated the tool/param name issue from
  the start — MCP's whole point is dynamic discovery. Should have
  built `_resolve_query_tool()` in Round 3 instead of hardcoding.
- **Change next:**
  - Live end-to-end verify with Sam's MCP server (pending Sam's test)
  - Consider exposing `explain_query` and schema tools through
    `/trino-research` for richer optimization context
  - v15 R3 (tests + docs) can close out after Sam confirms E2E works
