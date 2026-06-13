---
covers:
  - "genie/skills/trino_query/*.md"
  - "genie/skills/trino_query/*.py"
last_synced: "dfeabc1a64fb3dcf297942cf39e4cf5ba55f334b"
---

## Purpose

`genie/skills/trino_query` owns all direct Trino interaction: profile-based connection management, query execution with metrics capture, static SQL analysis (10 sqlglot AST rules), query optimization (lint + auto-fix + before/after comparison), plan-signature structural equivalence for the iteration guard, and the full `/trino-research` autoresearch loop (both has-data and no-data paths). It is the only layer that holds a live `trino.dbapi` connection; all other skills consume results through the public classes and functions here.

## Exports

> See exports file: /Users/leeabc/work/emilyorz/genieCLI/docs/doc-layer/exports/genie-skills-trino-query.md

- TrinoQuerySkill: genie skill — execute SQL and return formatted results
- TrinoExplainSkill: genie skill — run EXPLAIN on a SQL statement
- TrinoOptimizeSkill: genie skill — lint, auto-fix, and compare before/after metrics
- QueryMetrics: dataclass capturing Trino cursor stats (cpu, wall, memory, rows)
- TrinoProfile: dataclass for one named connection profile; `connect()` returns dbapi conn
- scan_sql: pure static scan returning DetectionFindings with 4-action tier labels
- analyze: sqlglot-based 10-rule static analysis, never raises, returns StaticAnalysisReport
- run_trino_research: top-level entry for /trino-research; dispatches has-data or no-data path

## Invariants

- Connection config file — connection.py:37 — `CONFIG_PATH = Path.home() / ".config" / "genie" / "trino.json"`
- Profile selection is CLI-driven, not env-var — connection.py:4 — `Active profile is selected via CLI (/trino use <name>), not env vars.`
- `_try_execute` never raises — optimize.py:24 — `"""Execute SQL, return {rows, columns, metrics, error, query_id}. Never raises."""`
- `analyze` never raises — sql_static/__init__.py:90 — `"""Parse *sql* once, run all rules, never raise."""`
- Static analysis silently skips a failing rule and logs debug — sql_static/__init__.py:131 — `logger.debug("static rule %s failed: %s", rule_fn.__module__, exc)`
- `scan_sql` handles bare SQL fragments by wrapping in a synthetic scaffold — detection_scan.py:42 — `"""Scan *sql* (whole query OR bare fragment) and return DetectionFindings."""`
- `structural_equivalent` compares plan signatures without running the query — plan_signature.py:138 — `def structural_equivalent(plan_a, plan_b) -> bool`
- `_load_rules` defines the fixed 10-rule order (r1–r10) — sql_static/__init__.py:62 — `def _load_rules() -> list`
- `QueryMetrics` is a pure dataclass; all fields default to 0 — __init__.py:24 — `class QueryMetrics:`
- `_execute_sql_sync` closes the connection after every call — research.py:51 — `conn.close()`

## Change log

- dfeabc1a64fb3dcf297942cf39e4cf5ba55f334b: initial doc-bootstrap card
