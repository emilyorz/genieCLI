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
