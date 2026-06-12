---
covers:
  - "genie/skills/trino_query/*.md"
  - "genie/skills/trino_query/*.py"
last_synced: "df1131522263a60bac2a7a0326499f43bc63c490"
---

## Purpose

Provides the `trino_query` skill group: direct Trino SQL execution and
introspection over a local profile-based connection. Owns four registered
tools (`trino_query`, `trino_explain`, `trino_schema`, `trino_optimize`),
the connection profile manager (`connection.py`), a pure-static SQL
anti-pattern scanner (`detection_scan.py`), a structural plan-signature
comparator (`plan_signature.py`), and the auto-fix optimizer
(`optimize.py`). This package is the *direct* (non-MCP) execution path;
the MCP path lives in `genie/skills/mcp_trino/`.

## Exports

**`__init__.py`**

- `QueryMetrics` — dataclass: 15 performance fields captured from
  `cursor.stats` (cpu_time_ms, wall_time_ms, peak_memory_bytes,
  processed_rows, total_splits, …). `.to_json() -> dict`,
  `.summary_line() -> str`.
- `QueryResult` — dataclass: rows, columns, duration_ms, truncated,
  error, query_id, metrics. `.to_json() -> dict`.
- `_extract_metrics(stats: dict) -> QueryMetrics` — maps Trino
  cursor.stats key names to `QueryMetrics` fields; used by both
  `__init__` and `optimize`.
- `_clean_row(row: tuple) -> list` — serialises Decimal/date/datetime/
  timedelta/bytes to JSON-safe types.
- `TrinoQuerySkill` — executes SQL, returns JSON with rows + metrics.
- `TrinoExplainSkill` — runs `EXPLAIN (TYPE logical|distributed)`.
- `TrinoSchemaSkill` — `SHOW CATALOGS/SCHEMAS/TABLES` or `DESCRIBE`.
- `register(registry)` — registers all four skills.

**`connection.py`**

- `TrinoProfile` — dataclass (host, port, user, scheme, catalog, schema,
  label). `.connect(catalog, schema)` returns a `trino.dbapi` connection.
  `.display_name() -> str`.
- `get_active_profile() -> TrinoProfile` — reads active profile from
  `~/.config/genie/trino.json`; creates `local` default if absent.
- `list_profiles() -> dict[str, TrinoProfile]`
- `get_active_name() -> str`
- `set_active(name: str) -> bool`
- `add_profile(name, profile) -> None`
- `remove_profile(name) -> bool` — refuses to remove the active profile.
- `status_line() -> str` — one-line CLI banner entry.

**`detection_scan.py`**

- `DetectionFinding` — frozen dataclass: rule_id, action
  (`block|rewrite|advise|pass`), severity, message, suggestion, line.
- `scan_sql(sql: str) -> list[DetectionFinding]` — pure static scan,
  never raises. Handles full statements and bare fragments (auto-wraps
  in `SELECT 1 WHERE <frag>` or `SELECT * FROM (<frag>) _frag` based on
  parsed AST shape). Clean SQL returns one `none/pass` sentinel; parse
  error returns one `parse-error/advise` sentinel.

**`plan_signature.py`**

- `PlanSignature` — type alias for `tuple`.
- `plan_signature(plan) -> Optional[PlanSignature]` — structural
  signature of a Trino `EXPLAIN (FORMAT JSON)` plan tree. Drops
  volatile fields (estimates, symbol names, cost numbers); keeps
  operator kind, join type, agg functions, table identifiers. Returns
  `None` when plan is unparseable (callers fall through to row-equiv).
- `structural_equivalent(plan_a, plan_b) -> bool` — returns `False`
  (not `None`) when either signature is unavailable.

**`optimize.py`**

- `TrinoOptimizeSkill` — 4-step flow: lint original → execute baseline →
  auto-fix → execute fixed → emit side-by-side comparison.
- `_auto_fix(sql, findings) -> tuple[str, list[str]]` — applies rule-
  driven text rewrites: NVL→COALESCE, DECODE→CASE WHEN,
  SYSDATE→CURRENT_TIMESTAMP, SELECT \*→named columns (via live DESCRIBE).

**`sql_static/`** (sub-package — covered by `*.py` glob)

- `analyze(sql) -> StaticAnalysisReport` — runs 10 sqlglot-based rules
  (r1–r10) and returns findings with severity.
- `rule_ids.py` — string constants for all rule IDs (shared with
  `mcp_trino/rule_gate.py` and `mcp_trino/pre_execution_diagnosis.py`).
- Individual rule modules (`rules/r1_*` … `rules/r10_*`) each export
  `apply(sql, statements) -> list[Finding]`.

## Invariants

- Connection is **profile-based only** — no env-var fallback.
  `get_active_profile()` auto-creates a `local` profile at
  `localhost:8085` if the config file is absent.
- `scan_sql` and all `sql_static` functions are **zero-network**:
  pure Python / sqlglot AST, no Trino cluster required.
- `detection_scan` imports `rule_gate` lazily (function-local import)
  to avoid circular imports between `trino_query/` and `mcp_trino/`.
- `plan_signature` deliberately **ignores** cost estimates and generated
  symbol names; structural equivalence is topology-only.
- `TrinoOptimizeSkill._auto_fix` only rewrites what lint found — it does
  not speculatively rewrite clean SQL.
- `_extract_metrics` maps Trino camelCase stat keys; missing keys
  default to 0 (never raises KeyError).
- `register()` in `__init__.py` imports `TrinoOptimizeSkill` lazily
  (inside the function) to avoid top-level import cost.
- The `research.py` file provides the `--direct` optimization loop
  (non-MCP path for `/trino-research`); its public entry point is
  `run_trino_research(provider, cfg, model, reasoning, output,
  build_prompt) -> None`.

## Change log

- df11315: initial doc-layer card for genie/skills/trino_query
