---
name: mcp-trino
description: >-
  MCP client for Trino — connects to a Trino MCP server and exposes its
  tools as genieCLI skills. Supports dynamic tool discovery via MCP protocol.
version: 1.1.0
group: mcp_trino
tier: core
---

# MCP Trino — Query Optimization Guide

This skill connects to a Trino MCP server and drives `/trino-research` — an
autonomous iterative loop that rewrites SQL to reduce a target metric
(default: `query_time_ms`).

The content below is **loaded into the AI's system prompt at every
`/trino-research` invocation**. Edit this file to tune optimization
behavior without touching Python code.

## Optimization Priorities (apply in order)

1. **Prune data early** — predicate pushdown into the first scan. Add
   partition filters, date ranges, and primary-key bounds at the leaves
   of the plan, not in outer WHERE clauses.
2. **Replace SELECT \*** with explicit column lists. Columnar scans only
   read what you reference.
3. **Avoid blocking aggregations** — prefer `APPROX_DISTINCT(x)` over
   `COUNT(DISTINCT x)` unless exact semantics are required. Same for
   `APPROX_PERCENTILE` over sort-based percentiles.
4. **Join order matters** — put the smallest filtered relation first so
   the broadcast side stays small. Use `BROADCAST` hint for small
   dimension tables, `PARTITIONED` for equi-joins on large facts.
5. **CTEs over correlated subqueries** — Trino materializes CTEs once;
   correlated subqueries re-execute per row.
6. **Push COALESCE / NULL handling into the scan** — don't wrap a whole
   column list in `COALESCE` at the top; apply it only where needed.

## Anti-patterns to Rewrite

| Pattern | Rewrite |
|---------|---------|
| `NVL(x, y)` | `COALESCE(x, y)` |
| `DECODE(a, b, c, d)` | `CASE WHEN a = b THEN c ELSE d END` |
| `SYSDATE` | `CURRENT_TIMESTAMP` |
| `ROWNUM <= N` | `LIMIT N` (or `ROW_NUMBER() OVER(...)` for partition-scoped limits) |
| Implicit cross join (`FROM a, b WHERE a.id = b.id`) | Explicit `JOIN ... ON` |
| `SELECT *` | Named column list |
| `(+)` outer join | Standard `LEFT JOIN` / `RIGHT JOIN` |
| `WHERE TO_CHAR(col, 'YYYY-MM') = '2026-04'` | `WHERE col >= DATE '2026-04-01' AND col < DATE '2026-05-01'` (sargable) |
| Leading wildcard `LIKE '%foo'` | Consider reverse-index or full-text; if unavoidable, flag as known-slow |

## Common Wins by Metric

- **query_time_ms / wall_time_ms**: partition filter, broadcast hint for
  small dim, APPROX_DISTINCT, drop unused columns
- **cpu_time_ms**: APPROX aggregations, reduce rows scanned, remove
  unnecessary GROUP BY keys
- **peak_memory_bytes**: avoid `ORDER BY` on huge result sets (stream
  instead), `DISTINCT` → `GROUP BY`, spill-friendly joins
- **physical_input_bytes**: partition pruning, projection pushdown
  (named columns), column stats-aware filter placement

## What NOT to Do

- Do not change result semantics. Row count, column set, and values
  must match within the defined consistency tolerance.
- Do not add hints you cannot justify from the current plan.
- Do not bundle multiple independent changes in one iteration — the
  measurement compares iteration-to-iteration, so compound changes
  hide which tweak helped.
- Do not use proprietary / Oracle-only functions.
- Do not add trailing semicolons to the returned SQL.

## Per-iteration Response Format

Return ONLY:

1. One short line (≤80 chars) describing the hypothesis — this becomes
   the history row. Example: `"Add partition filter on event_date"`.
2. A ```sql block with the COMPLETE rewritten query.

Do not return diffs, explanations, or tool calls.

## Configuration

Add to `~/.genie/config.toml`:

```toml
[mcp.trino]
url = "http://localhost:8811/mcp"
enabled = true
```

Or set environment variables:

```bash
export GENIE_MCP_TRINO_URL=http://localhost:8811/mcp
```
