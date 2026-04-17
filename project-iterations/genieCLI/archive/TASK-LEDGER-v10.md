# TASK-LEDGER

## Basic Info

- Project: genieCLI MCP Trino client + autoresearch enhancement
- Repo Folder: project-iterations/genieCLI/
- Naming note: this is a formal long-running workflow, not an "experiments" sandbox.
- Iteration: 10
- Owner: Emily (tmux emily-claude)
- Status: active
- Last Updated: 2026-04-13T14:20+08:00
- Current Focus: MCP client implementation + autoresearch enhancement flow + fixed-format report

## Goal

- One-line summary:
  Add MCP client to genieCLI, build an autoresearch query enhancement flow over MCP, and define a fixed-format report template.
- Done when:
  1. MCP client (`genie/skills/mcp_trino/`) connects to a configurable Trino MCP server;
  2. MCP tools are dynamically discovered and registered as genieCLI skills;
  3. Autoresearch enhancement loop runs 5 iterations via MCP, with metric/consistency checks;
  4. Fixed-format report template outputs: original SQL, original result, enhanced result, performance comparison, metrics, query time, data consistency;
  5. Config supports `~/.genie/config.toml` `[mcp.trino]`, `~/.config/genie/mcp.json`, env vars;
  6. Top-level experiment files moved to `experiments/`;
  7. All unit tests pass (30 new tests + no regressions);
  8. Live verification by Sam on dev server with localhost:8811.

## Carryover

- genieCLI roadmap Phase 3 = "MCP 接 Trino" — this iteration implements the client side.
- Trino MCP server at `localhost:8811` is Sam's dev environment — Emily cannot access it directly.
- Existing `trino-research` uses `trino.dbapi` direct connections; MCP enhancement is a parallel path.
- MCP skills are additive — they don't replace the existing direct-connection skills.
- 20 pre-existing trino linter/integration test failures (same baseline as v9).

## Todo

| ID | Status | Pri | Task | Owner | Note |
|----|--------|-----|------|-------|------|
| T1 | done | P0 | Implement MCP client (`client.py`) — JSON-RPC 2.0 Streamable HTTP transport | Emily | `McpClient`, `McpConfig`, `load_mcp_config()`, `save_mcp_config()` |
| T2 | done | P0 | Implement MCP skill registration (`__init__.py`) — dynamic tool discovery + BaseSkill wrappers | Emily | `McpTrinoSkill`, `McpTrinoStatusSkill`, `_build_args()`, `register()` |
| T3 | done | P1 | MCP config system — TOML `[mcp.trino]`, JSON `mcp.json`, env vars (`GENIE_MCP_TRINO_*`) | Emily | 3-layer config: JSON < TOML < env |
| T4 | done | P2 | Move top-level experiment files to `experiments/` | Emily | 6 files moved |
| T5 | done | P1 | Unit tests for MCP client + skill registration | Emily | 14 tests in `tests/test_mcp_trino.py` |
| T6 | done | P0 | Implement MCP autoresearch enhancement loop (`research.py`) | Emily | `run_mcp_enhancement()` — 5-iteration loop with metric/equivalence guards |
| T7 | done | P0 | Define fixed-format report template (`generate_report()`) | Emily | 9 sections: Meta, Perf Comparison, Summary, Iteration History, Original SQL/Result, Enhanced SQL/Result, Footer |
| T8 | done | P1 | Unit tests for MCP research + report | Emily | 16 tests in `tests/test_mcp_research.py` |
| T9 | blocked | P0 | Live verify: run enhancement against Sam's MCP server at localhost:8811 | Sam | Emily cannot reach localhost:8811 — must be done on Sam's dev server |
| T10 | not started | P1 | Commit all changes to genieCLI repo | Emily | After Sam confirms approach |

## Verify

- Evidence checked: 2026-04-13
- Source of evidence: pytest (30 tests: 14 client + 16 research)
- Verification result: PARTIAL PASS
  - 30/30 new MCP tests pass
  - 574 existing tests pass (20 pre-existing trino failures, same baseline)
  - Live MCP verification: BLOCKED — localhost:8811 not reachable from this machine

## Blocked

- **T9**: Live verification blocked — localhost:8811 is Sam's dev server, not accessible from Emily's environment. Sam must pull the code and test on his machine.

## Reports

### Ledger setup — 2026-04-13T13:52+08:00

- Created v10 ledger. T1-T5 completed before ledger was formalized.

### Round 1 — 2026-04-13T14:20+08:00

- **PLAN**: Implement MCP client, autoresearch enhancement, fixed report template, tests
- **DISPATCH**: T1-T8 executed (MCP client, skills, config, research loop, report, tests, cleanup)
- **VERIFY**: 30/30 unit tests pass; live verify BLOCKED (T9)
- **UPDATE**: Ledger updated with all task statuses
- **CHECK**: Code-complete for all implementable tasks. Remaining blocker: T9 (live verify on Sam's dev server)

Files created/modified:
- NEW: `genie/skills/mcp_trino/__init__.py` — MCP skill registration + dynamic wrappers
- NEW: `genie/skills/mcp_trino/client.py` — MCP JSON-RPC 2.0 client
- NEW: `genie/skills/mcp_trino/research.py` — autoresearch enhancement loop + fixed report template
- NEW: `genie/skills/mcp_trino/SKILL.md` — skill metadata
- NEW: `tests/test_mcp_trino.py` — 14 client/skill tests
- NEW: `tests/test_mcp_research.py` — 16 research/report tests
- NEW: `experiments/README.md` — index for moved files
- MOVED: 6 experiment files from root → `experiments/`
- MODIFIED: `project-iterations/genieCLI/STATUS.md` — points to v10

### Report Template (fixed format)

The report always contains these 9 sections in this exact order:
1. **Meta** — timestamp, MCP server URL, metric, verify runs, iteration count
2. **Performance Comparison** — table: query_time, cpu_time, wall_time, rows, splits, memory, input bytes (original vs enhanced vs delta vs %)
3. **Summary** — baseline/best metric, improvement, row counts, data consistency verdict
4. **Iteration History** — table: round, status, metric value, delta, hypothesis
5. **Original SQL** — code block
6. **Original Result (sample)** — first 10 rows as table
7. **Enhanced SQL** — code block (or "no improvement" message)
8. **Enhanced Result (sample)** — first 10 rows as table
9. **Footer** — generation timestamp
