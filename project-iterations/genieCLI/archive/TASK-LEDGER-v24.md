# TASK-LEDGER

## Basic Info

- Project: genieCLI v24 — UX sprint pt.3 + SKILL.md expert review
- Repo Folder: project-iterations/genieCLI/
- Iteration: 24 (4 rounds, 1 PR)
- Owner: Emily (Claude Code)
- Status: done
- Updated: 2026-04-17T06:15+0800
- Focus: SQL preview, help routing, MCP status, and expert-level
  Trino optimization knowledge in SKILL.md.

## Round 1 — SQL syntax highlight preview in plan card

The pre-launch plan card now shows the first 5 lines of the SQL with
Rich `Syntax("sql")` highlighting. Users get visual confirmation of
which query is about to be optimized without reading the file.
Truncates with "..." if SQL exceeds 5 lines.

## Round 2 — /help command routing

`/help trino-research` now shows the same detailed help card as
`/trino-research --help`. Extracted the help text into a reusable
`_print_trino_research_help()` function called from both paths.
Unknown sub-commands fall through to the full help listing with a note.

## Round 3 — MCP status in startup banner

Chat startup now probes MCP reachability (if configured) and shows:

- `mcp    ok http://localhost:8811/mcp (6 tools)` — green
- `mcp    offline http://localhost:8811/mcp` — red
- `mcp    not configured` — dim

Uses a 3-second timeout probe (same as doctor) so startup doesn't
hang. Exceptions are silently swallowed.

## Round 4 — Trino SKILL.md expert content review

SKILL.md body expanded from 3.5KB to ~6KB with expert-level content:

**New sections:**

- **Connector-Specific Optimizations** — Hive (partition column filter
  requirements, ORC/Parquet pushdown rules, bucket columns), Iceberg
  (hidden partitioning, time-travel perf, metadata table warnings,
  DELETE rewrite pattern), Delta Lake (Z-ordering, transaction log)
- **Join Strategy Selection** — table: scenario × strategy × hint
  for BROADCAST / PARTITIONED / REPLICATE
- **Window Function Optimization** — ROW_NUMBER > RANK when only
  top-1 needed; avoid mixed PARTITION BY keys; LAG/LEAD IGNORE NULLS

**Expanded existing sections:**

- Anti-patterns: added UNION→UNION ALL, IN→EXISTS, ORDER BY pushdown,
  CAST on partition columns
- Common Wins: added processed_rows guidance, UDF warning, Parquet
  row-group pruning
- What NOT to Do: added no stored procedures, no temp tables

## Changes

| File                                 | Change                                                                                        |
| ------------------------------------ | --------------------------------------------------------------------------------------------- |
| `genie/skills/mcp_trino/research.py` | Plan card: SQL preview with Rich Syntax                                                       |
| `genie/chat.py`                      | Extracted `_print_trino_research_help()`; `/help <cmd>` routing; MCP status in startup banner |
| `genie/skills/mcp_trino/SKILL.md`    | Expert content expansion (v1.1.0 → v1.2.0)                                                    |

## Verification

- 629 tests pass — no regression
- SKILL.md body loads correctly (verified via registry in prior sessions)

## Retro

- **Worked:** The SKILL.md expansion is the highest-leverage change —
  it directly improves optimization quality without any code changes.
  The connector-specific section (Hive partition pruning rules,
  Iceberg hidden partitioning) fills a gap where the AI would otherwise
  propose optimizations that Trino's specific connector can't push down.
- **Failed:** Nothing — all four rounds were additive and clean.
- **Change next:**
  - Add more connector-specific rules as Sam tests against real tables
  - Consider splitting the guide into sections per-connector (can be
    toggled based on which connector the SQL targets)
  - The MCP banner probe runs synchronously — for slow networks it
    could delay startup. Consider async or skip-on-slow.
