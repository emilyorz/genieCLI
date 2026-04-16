---
name: mcp-trino
description: >-
  MCP client for Trino — connects to a Trino MCP server and exposes its
  tools as genieCLI skills. Supports dynamic tool discovery via MCP protocol.
version: 1.2.0
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
7. **Minimize data shuffles** — if two JOINs share the same key, keep
   them adjacent so Trino can colocate partitions. Avoid unnecessary
   GROUP BY between joins that force a re-partition.
8. **Leverage column stats** — Trino CBO performs better when table
   statistics exist. If a plan shows hash-joins on huge tables with
   no broadcast, suggest re-analyzing table stats rather than adding
   manual hints.

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
| `UNION` (deduplicating) | `UNION ALL` when inputs are guaranteed distinct — avoids sort + dedup |
| `IN (SELECT ...)` correlated | `EXISTS (SELECT 1 FROM ... WHERE ...)` or `JOIN` — Trino handles EXISTS more efficiently for correlated patterns |
| `ORDER BY` on full result | Move `ORDER BY` into a CTE or subquery with `LIMIT` — sorting unbounded result sets spills to disk |
| `CAST(partition_col AS VARCHAR)` in WHERE | Use native type comparison — casting partition columns disables partition pruning |

## Connector-Specific Optimizations

### Hive Connector
- Partition columns must appear as direct equality or range filters
  (not inside functions) for partition pruning to work.
- ORC/Parquet pushdown supports `=`, `<`, `>`, `BETWEEN`, `IN` but
  NOT `LIKE`, `!=`, or function-wrapped comparisons.
- Bucketed tables: filter on the bucket column to reduce split count.

### Iceberg Connector
- Iceberg supports hidden partitioning (e.g. `day(ts)`, `bucket(id, 16)`).
  Filter on the SOURCE column (`ts >= ...`), not the partition transform —
  Trino + Iceberg will derive the partition filter automatically.
- Time-travel queries (`FOR TIMESTAMP AS OF`) scan a snapshot; older
  snapshots may have compacted fewer files = slower scan.
- Iceberg metadata tables (`$files`, `$manifests`, `$history`) are useful
  for debugging but expensive — never call them inside the optimization loop.
- `DELETE WHERE` on Iceberg rewrites entire files. For large deletes,
  consider rewriting as `CREATE TABLE ... AS SELECT ... WHERE NOT ...`.

### Delta Lake Connector
- Z-ordering columns benefit from range-filter predicates — equality
  is less useful.
- Delta's transaction log is scanned at query time; tables with many
  small commits are slower than tables with fewer large commits.

## Join Strategy Selection

| Scenario | Strategy | How to hint |
|----------|----------|-------------|
| Small dim (< 10K rows) joined to fact | BROADCAST | Ensure the small table is the build side |
| Two large tables on equi-key | PARTITIONED (hash) | Default; no hint needed |
| Large table joined to medium (10K–1M) | BROADCAST if < broadcast limit | Check `query.max-broadcast-table-size` |
| Cross join (intentional) | REPLICATE | Explicit `CROSS JOIN` — never accidentally create one |
| Self-join | PARTITIONED | Trino handles same-table equi-join efficiently |

## Window Function Optimization

- `ROW_NUMBER() OVER (PARTITION BY ... ORDER BY ...)` is cheaper than
  `RANK()` when you only need one row per partition — use
  `WHERE rn = 1` outside a CTE.
- Avoid multiple window functions with different `PARTITION BY` keys in
  the same SELECT — each distinct key requires a separate sort. Split
  into CTEs if needed.
- `LAG`/`LEAD` with `IGNORE NULLS` is supported since Trino 400+.
- `NTILE`, `PERCENT_RANK`, `CUME_DIST` all require full sort — prefer
  approximate alternatives when exactness isn't needed.

## Common Wins by Metric

- **query_time_ms / wall_time_ms**: partition filter, broadcast hint for
  small dim, APPROX_DISTINCT, drop unused columns, reduce join fan-out
- **cpu_time_ms**: APPROX aggregations, reduce rows scanned, remove
  unnecessary GROUP BY keys, avoid UDF-heavy expressions in WHERE
- **peak_memory_bytes**: avoid `ORDER BY` on huge result sets (stream
  instead), `DISTINCT` → `GROUP BY`, spill-friendly joins, limit
  broadcast table size
- **physical_input_bytes**: partition pruning, projection pushdown
  (named columns), column stats-aware filter placement, Parquet
  row-group pruning via min/max stats
- **processed_rows**: the clearest signal of scan scope — if this
  number is much larger than the output, there's a filter that
  should push deeper, or a join that's creating fan-out

## What NOT to Do

- Do not change result semantics. Row count, column set, and values
  must match within the defined consistency tolerance.
- Do not add hints you cannot justify from the current plan.
- Do not bundle multiple independent changes in one iteration — the
  measurement compares iteration-to-iteration, so compound changes
  hide which tweak helped.
- Do not use proprietary / Oracle-only functions.
- Do not add trailing semicolons to the returned SQL.
- Do not rewrite a query into a stored procedure or UDF call — Trino
  has no stored procedures.
- Do not use `CREATE TEMPORARY TABLE` — Trino doesn't support temp
  tables. Use CTEs or `CREATE TABLE ... WITH (format = 'PARQUET')` if
  materialization is truly needed (but that changes semantics).

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
