---
name: oracle2trino
description: >-
  Oracle SQL to Trino SQL migration tools. Use for transpiling Oracle SQL,
  looking up function/type equivalents, listing Trino limitations, and
  analyzing Oracle stored procedures for migration readiness.
version: 1.0.0
group: oracle2trino
tier: core
requires:
  python:
    - sqlglot
    - pyyaml
---

# Oracle to Trino Migration

Provides five tools for Oracle-to-Trino SQL migration:

- **transpile_sql** — Transpile Oracle SQL to Trino SQL with confidence scoring
- **lookup_oracle_function** — Find Trino equivalent for an Oracle function
- **lookup_oracle_type** — Find Trino equivalent for an Oracle data type
- **list_trino_limitations** — List known Trino hard limits affecting migration
- **analyze_oracle_sp** — Analyze an Oracle stored procedure for migration complexity
