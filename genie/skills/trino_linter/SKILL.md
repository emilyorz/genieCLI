---
name: trino-linter
description: >-
  Static SQL linter for Trino queries. Detects Oracle residuals,
  Trino anti-patterns, and common SQL issues without executing the query.
version: 1.0.0
group: trino_linter
tier: core
requires:
  python:
    - sqlglot
---

# Trino Linter

Provides one tool for static SQL analysis:

- **trino_linter** — Analyze SQL for Oracle residuals (NVL, DECODE, SYSDATE, ROWNUM, (+) joins) and Trino anti-patterns (SELECT *, correlated subqueries, missing partition filters).
