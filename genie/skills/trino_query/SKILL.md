---
name: trino-query
description: >-
  Execute SQL on local Trino with performance metrics. Use for running
  queries, viewing EXPLAIN plans, inspecting catalog/schema/table
  metadata, and optimizing query performance.
version: 1.0.0
group: trino_query
tier: core
requires:
  python:
    - trino
---

# Trino Query

Provides four tools for Trino SQL execution and introspection:

- **trino_query** — Execute SQL and return results with performance metrics
- **trino_explain** — Run EXPLAIN and return the query plan
- **trino_schema** — Inspect catalogs, schemas, tables, and columns
- **trino_optimize** — Analyze query performance and suggest optimizations

## P1–P8 Rewrite Strategy Menu

`trino_optimize` rewrites a flagged fragment by applying a **named** strategy from
the P1–P8 fix menu (not freestyle). Source of truth + safety tiers:
`genie/skills/mcp_trino/p_strategies.py`; the full guide is in
`genie/skills/mcp_trino/SKILL.md` ("P1–P8 Rewrite Strategy Menu"). Summary:

| #  | Strategy | Tier | Auto-apply? |
| -- | -------- | ---- | ----------- |
| P1 | function-pushup | SAFE | yes |
| P2 | exists-to-left-join | TRAP | only if join key unique (verify row-equivalence) |
| P3 | like-to-contains | DANGEROUS | advise only |
| P4 | listagg-to-slice | DANGEROUS | advise only |
| P5 | predicate-partition-pushdown | TRAP | yes, verify when crossing an OUTER join |
| P6 | lambda-rewrite | DANGEROUS | advise only |
| P7 | skinny-join | SAFE | yes |
| P8 | broadcast-hint | SAFE | yes |

SAFE = value-preserving; TRAP = rewrite only behind a row-equivalence check;
DANGEROUS = surfaced as advice, never auto-applied.

## Rule-Matching / Non-Equi Join (summary)

Full playbook: `genie/skills/mcp_trino/SKILL.md` → **Rule-Matching / Non-Equi Join Playbook**.

For massive base × small rule / non-equi / correlated EXISTS workloads, try in order:
**P1 → P7 → P8(advice only) → P2(unique key) → P5 → P10(cte-merge) → P6(conditional) → P3/P4(advise-first)**.
P6 lambda is **not** default. Never put `SET SESSION` in candidate SQL. One change per iteration.
