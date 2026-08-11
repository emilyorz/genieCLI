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
4. **Join order and build side matter** — Trino's CBO chooses join order
   and distribution from table stats. Broadcast is good only when the
   filtered build side fits in memory on every worker; partitioned joins
   are safer for large-large joins. Do not emit generic SQL hints.
5. **Treat `WITH` as inlined** — Trino currently inlines `WITH` relations
   where referenced; a CTE is not a cache. A deep CTE chain with JOIN/GROUP
   BY can create one huge plan. Recommend step materialization only as an
   advisory or in an explicit multi-statement mode.
6. **Prioritize raw scans over curated rescans** — repeated scans of raw /
   fact / source tables are expensive; repeated scans of small curated,
   presum, or dimension tables are usually secondary unless EXPLAIN proves
   otherwise.
7. **Push COALESCE / NULL handling into the scan** — don't wrap a whole
   column list in `COALESCE` at the top; apply it only where needed.
8. **Minimize data shuffles** — if two JOINs share the same key, keep
   them adjacent so Trino can colocate partitions. Avoid unnecessary
   GROUP BY between joins that force a re-partition.
9. **Leverage column stats** — Trino CBO performs better when table
   statistics exist. If a plan shows hash-joins on huge tables with
   bad build-side choices, suggest `ANALYZE` / stats refresh before
   forcing join distribution.

## Trino Execution Model Notes

- `WITH` / CTEs are readability constructs, not guaranteed materialized
  results. If the same CTE or subplan is referenced repeatedly, assume the
  optimizer may inline it and plan repeated work.
- One giant SQL statement can fail because the plan is too deep even when
  total cluster CPU looks sufficient. Symptoms: many fragments/exchanges,
  repeated identical subplans, high blocked time, skewed per-task input, or
  spill.
- More workers help scan and shuffle throughput. They do not fix a bad
  single-query plan, per-worker memory pressure, skewed hot keys, or a
  stage that spills heavily.
- Use `EXPLAIN (TYPE DISTRIBUTED)` to inspect fragments/exchanges and
  `EXPLAIN ANALYZE` to inspect runtime CPU, blocked time, per-task
  `Input std.dev.`, dynamic filters, and output row fan-out.
- Dynamic filtering helps selective joins when the small filtered dimension
  becomes the build side and connector support exists. Preserve selective
  dimension predicates and up-to-date stats so CBO can choose that shape.

## Step Materialization Guidance

Recommend materializing intermediate steps only when the query shape justifies
the side effects:

- Good candidates: 3+ chained CTEs, multiple heavy JOIN/GROUP BY steps, one
  CTE reused by multiple branches, or repeated scans of a large raw/fact table.
- Lower-priority candidates: repeated reads of small curated / presum /
  dimension tables, shallow CTEs used once, or simple readability CTEs.
- Preferred recommendation format: "split into managed CTAS steps in a scratch
  schema" or "use a materialized view if the base data changes slowly".
- Do not auto-return multi-statement DDL in the normal `/trino-research` loop.
  CTAS/materialized views require a scratch schema, collision-proof names,
  cleanup/TTL, CREATE/DROP privileges, and explicit user opt-in.
- Do not overwrite source tables. Never emit `DROP` / `CREATE OR REPLACE`
  against user schemas unless the user explicitly enabled a materialization
  mode with a scratch target.
- `WITH (cached = TRUE)` is not baseline OSS Trino CTE syntax. Treat it as a
  feature-probed / fork-specific capability only; do not suggest it unless the
  exact deployed engine supports it.

## Runtime Bottleneck Patterns

| Symptom                                    | Likely cause                                               | Model guidance                                                                            |
| ------------------------------------------ | ---------------------------------------------------------- | ----------------------------------------------------------------------------------------- |
| Huge scan bytes, small output              | Missing partition/predicate/projection pushdown            | Push filters to leaves; use native partition column types; list needed columns            |
| One stage/task much larger than peers      | Data skew / hot join or group key                          | Filter NULL/hot keys, pre-aggregate, consider salting only after evidence                 |
| High peak memory or spill                  | Large build side, high-cardinality aggregation/window/sort | Narrow columns, filter earlier, pre-aggregate, avoid broadcast for large build            |
| Many fragments/exchanges/repeated subplans | Nested CTE plan explosion                                  | Flatten or recommend managed step materialization                                         |
| Bad join order/distribution                | Missing/stale stats                                        | Suggest stats refresh / `ANALYZE`; only recommend session property override with evidence |

## Anti-patterns to Rewrite

| Pattern                                             | Rewrite                                                                                                          |
| --------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------- |
| `NVL(x, y)`                                         | `COALESCE(x, y)`                                                                                                 |
| `DECODE(a, b, c, d)`                                | `CASE WHEN a = b THEN c ELSE d END`                                                                              |
| `SYSDATE`                                           | `CURRENT_TIMESTAMP`                                                                                              |
| `ROWNUM <= N`                                       | `LIMIT N` (or `ROW_NUMBER() OVER(...)` for partition-scoped limits)                                              |
| Implicit cross join (`FROM a, b WHERE a.id = b.id`) | Explicit `JOIN ... ON`                                                                                           |
| `SELECT *`                                          | Named column list                                                                                                |
| `(+)` outer join                                    | Standard `LEFT JOIN` / `RIGHT JOIN`                                                                              |
| `WHERE TO_CHAR(col, 'YYYY-MM') = '2026-04'`         | `WHERE col >= DATE '2026-04-01' AND col < DATE '2026-05-01'` (sargable)                                          |
| Leading wildcard `LIKE '%foo'`                      | Consider reverse-index or full-text; if unavoidable, flag as known-slow                                          |
| `UNION` (deduplicating)                             | `UNION ALL` when inputs are guaranteed distinct — avoids sort + dedup                                            |
| `IN (SELECT ...)` correlated                        | `EXISTS (SELECT 1 FROM ... WHERE ...)` or `JOIN` — Trino handles EXISTS more efficiently for correlated patterns |
| `ORDER BY` on full result                           | Move `ORDER BY` into a CTE or subquery with `LIMIT` — sorting unbounded result sets spills to disk               |
| `CAST(partition_col AS VARCHAR)` in WHERE           | Use native type comparison — casting partition columns disables partition pruning                                |
| `JOIN ... ON UPPER(a.x) = b.y` / `ON CAST(a.id AS varchar) = b.id` / `ON a.x + 1 = b.k` (function/cast/arithmetic on a join key) | Join on the raw column so the planner can hash-join. If the value must be normalized/cast, materialize the normalized value as a stored column upstream — a computed join key forces a per-row recompute / nested-loop join. (detected by static rule `join-key-computed`) |
| `bitwise_or` / `bitwise_and` / bitmask flags in matching logic | Prefer `bool_or`, `max`, `sum(CASE …)`, or plain `CASE` status mapping. Bitmasks hide predicates from the planner and complicate equivalence. |

## P1–P8 Rewrite Strategy Menu

The named **fix menu** the optimize step applies to a flagged fragment (how to
rewrite a monster — NOT detection). Single source of truth:
`genie/skills/mcp_trino/p_strategies.py` (the `optimize()` prompt is built from it).
Apply a NAMED strategy; do not freestyle. Safety tier gates auto-apply:

- **SAFE** — value-preserving; apply freely.
- **TRAP** — usually equivalent but a known pitfall; apply only if exact
  row-equivalence holds (the recompose gate verifies and reverts otherwise).
- **DANGEROUS** — may change output; **advise only, never auto-apply**.

| #  | Strategy | Tier | When → How |
| -- | -------- | ---- | ---------- |
| P1 | function-pushup | SAFE | function/cast/arithmetic wraps a JOIN/WHERE/partition key → move it off the column so pruning works |
| P2 | exists-to-left-join | TRAP | correlated `EXISTS` enrich → `LEFT JOIN`, **only when the join key is unique** (else fan-out duplicates rows → MAX/SUM/LISTAGG change) |
| P3 | like-to-contains | DANGEROUS | `LIKE '%v%'` → `contains(split(col, delim), 'v')` — substring vs exact-token semantics differ |
| P4 | listagg-to-slice | DANGEROUS | `LISTAGG` → `array_join(slice(array_agg(x),1,N),…)` — caps/**truncates** groups > N |
| P5 | predicate-partition-pushdown | TRAP | filter evaluated late → push WHERE/partition predicate down to the scan to prune early (safe for inner joins/base filters; verify when crossing an OUTER-join null-producing side) |
| P6 | lambda-rewrite | DANGEROUS | per-row array/map expansion → higher-order lambda (transform/filter/reduce) — heavy; null/empty/order easy to change |
| P7 | skinny-join | SAFE | join inputs carry unused columns → project only keys + needed columns before the join (narrow the shuffle) |
| P8 | broadcast-hint | SAFE | small build side distributed (PARTITIONED) → broadcast hint to replicate it and avoid a large shuffle |

## Rule-Matching / Non-Equi Join Playbook

Use this section when the workload looks like **massive base table × small/medium
rule table**, **non-equi / cross join rule matching**, or **correlated
EXISTS/IN/CASE enrichment** — not for ordinary equi-join tuning.

This playbook does **not** replace P1–P8. It only chooses **which named strategy
to try first**. Cross-reference the menu above; do not freestyle a new strategy.

### Scenario cues

- Join/`CASE` conditions use `LIKE`, `strpos`, `contains` on concatenated fields,
  range/non-equi predicates, or many OR-ed rule branches
- Rule/dimension side is much smaller than the fact/base side
- Correlated `EXISTS` / `IN (SELECT …)` inside join predicates or per-row `CASE`
- Functions (`COALESCE`, `CONCAT`/`||`, `SPLIT`, `CAST`, `UPPER`) sit on join keys
- Previous candidates timed out or spilled while “matching rules”

### Forced strategy order (one step per iteration)

If system directions / rule-gate / critical-path brief already name a hotspot,
prefer that hotspot and still apply **one** P-strategy only. Otherwise walk:

1. **[P1 SAFE] function-pushup** — move `COALESCE`/`CONCAT`/`SPLIT`/`CAST`/
   arithmetic **off** `ON`/`WHERE` join keys into upstream source CTEs; join on
   precomputed columns (see static rule `join-key-computed`).
2. **[P7 SAFE] skinny-join** — project base/rule sides to PK + predicate columns
   before the heavy match; rejoin the wide base **after** 1:1 aggregate by PK
   (late materialization).
3. **[P8 SAFE] broadcast advice only** — if the filtered rule/build side is
   provably small, recommend broadcast via harness advice. **Never** put
   `SET SESSION` or `-- set session …` magic comments inside candidate SQL.
4. **[P2 TRAP] exists-to-left-join** — convert correlated `EXISTS`/`IN` enrich to
   upstream `LEFT JOIN` **only when the join key is unique**. Without uniqueness
   evidence, **advise only** (fan-out changes `MAX`/`SUM`/`LISTAGG`).
5. **[P5 TRAP] predicate/partition pushdown** — push time/partition filters to
   leaves so pruning survives the rewrite.
6. **[P6 DANGEROUS] lambda / single-row rule array** — **not the default.**
   Only after safer steps fail **and** all of:
   - rule table is small enough to `array_agg` into one row safely
   - `filter`/`reduce` semantics are provably equivalent to the original match
   - intermediate columns use a `calc_` prefix **inside CTEs**, final SELECT
     column names still match baseline
   Pattern sketch (advise/candidate only when gates above hold): package rules
   with `array_agg(CAST(ROW(...) AS ROW(...)))`, `CROSS JOIN` the single row,
   `filter(all_rules, r -> …)` then `reduce(...)` per base row.
7. **[P3 / P4 DANGEROUS] advise-first**
   - `LIKE '%v%'` → `contains(split(...))` changes substring vs token semantics
   - `LISTAGG` → `array_join(slice(array_sort(array_agg(x)), 1, N), ',')` is a
     **lossy cap** (`slice` truncates). Also note `array_agg` itself can OOM
     before `slice` helps. Without explicit acceptance of different results,
     **do not emit as a candidate** (row-equivalence will kill it).

### Bitwise anti-pattern

- **Never** use `bitwise_or` / `bitwise_and` / bitmasks for rule flags.
- Prefer `bool_or`, `max`, `sum(CASE …)`, plain `CASE`, or (only if already in a
  justified P6 path) `reduce` over booleans/status strings.

### Hard constraints (research loop)

- Candidate SQL must **not** contain any `SET SESSION` / session-set magic comment;
  broadcast and resource knobs stay in advice, not in the SQL body.
- **P6 is not the default.** Prefer P1/P7 first; use P6 only with the gates above.
- **P2 without unique-key evidence** is advise-only (fan-out risk).
- `slice(..., 1, N)` is lossy truncation — not a silent drop-in for `LISTAGG`.
- **Exactly one focused change per iteration.** Multi-step refactors become
  multiple iterations, each independently measurable.
- Do **not** re-emit AST trees or long “extreme optimization” bullet essays.
  Critical path / directions from the system are authoritative when present.
- Response format unchanged: one short hypothesis line (≤80 chars) + complete
  ```sql``` block; no trailing semicolon; preserve result semantics.

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

| Scenario                                | Strategy                 | How to guide                                                                                             |
| --------------------------------------- | ------------------------ | -------------------------------------------------------------------------------------------------------- |
| Small filtered dimension joined to fact | Broadcast can help       | Keep the small side as build side; verify it fits per-worker memory                                      |
| Two large tables on equi-key            | Partitioned hash join    | Default/safest; reduce both sides before join                                                            |
| Medium table joined to large fact       | Depends on filtered size | Let CBO choose when stats exist; otherwise recommend stats refresh first                                 |
| Selective dimension filter              | Dynamic filtering        | Preserve dimension-side filters and equi-join keys so probe-side scan can prune                          |
| Cross join                              | Avoid unless intentional | Require explicit `CROSS JOIN` and explain the fan-out                                                    |
| Bad CBO choice with evidence            | Session property only    | Mention `join_distribution_type` / `join_reordering_strategy` as environment-level levers, not SQL hints |

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

- **query_time_ms / wall_time_ms**: partition filter, broadcast join for
  proven small build side, step-materialization recommendation for deep
  CTE plans, APPROX_DISTINCT, drop unused columns, reduce join fan-out
- **cpu_time_ms**: APPROX aggregations, reduce rows scanned, remove
  unnecessary GROUP BY keys, avoid UDF-heavy expressions in WHERE
- **peak_memory_bytes**: avoid `ORDER BY` on huge result sets (stream
  instead), `DISTINCT` → `GROUP BY`, reduce build-side size, avoid
  broadcast when the build side is too large for each worker
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
- Do not return CTAS / `CREATE TABLE` / `DROP TABLE` chains from the normal
  single-query optimization loop. Step materialization is advisory unless
  a dedicated materialization mode is explicitly enabled.
- Do not use `CREATE TEMPORARY TABLE` — Trino doesn't support temp
  tables. Use CTEs or `CREATE TABLE ... WITH (format = 'PARQUET')` if
  materialization is truly needed (but that changes semantics).
- Do not suggest `WITH (cached = TRUE)` unless a capability probe confirms
  the deployed engine supports it; it is not baseline OSS Trino CTE syntax.
- Do not refer to `EXPLAIN (FORMAT EMBEDDED)`; use supported formats
  `TEXT`, `GRAPHVIZ`, or `JSON`.

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
