---
covers:
  - "genie/skills/oracle2trino/*.md"
  - "genie/skills/oracle2trino/*.py"
  - "genie/skills/oracle2trino/*.yaml"
last_synced: "df1131522263a60bac2a7a0326499f43bc63c490"
---

## Purpose

Provides six genie skills for Oracle-to-Trino SQL migration. The package wraps
`sqlglot` (Oracle→Trino transpilation), a YAML function/type mapping database
(`data/oracle_trino_functions.yaml`), and the shared `ORACLE_CONSTRUCTS` catalog
from `genie.core.sql_patterns` to detect unsupported PL/SQL constructs. All
skills register under the `oracle2trino` group and return JSON or plain-text
suitable for LLM consumption. The sixth skill (`lint_trino_sql`) delegates to
`genie.core.lint_analyzer` to lint already-converted Trino SQL for Oracle
residuals and common anti-patterns.

## Exports

### Skills registered via `register(registry)`

| Skill class | Tool name | Signature | Description |
|---|---|---|---|
| `TranspileSQL` | `transpile_sql` | `run(sql: str) -> str` | Runs `sqlglot.transpile(read="oracle", write="trino")`, detects unsupported constructs in the original SQL, and returns a JSON `ConversionResult`. |
| `LookupOracleFunction` | `lookup_oracle_function` | `run(oracle_name: str) -> str` | Looks up an Oracle function name (case-insensitive) in the YAML DB; returns up to 5 matches with Trino equivalent, example, and notes. |
| `LookupOracleType` | `lookup_oracle_type` | `run(oracle_type: str) -> str` | Exact-match lookup of an Oracle data type in the YAML DB; returns Trino equivalent and notes. |
| `ListTrinoLimitations` | `list_trino_limitations` | `run() -> str` | Returns a numbered list of Trino hard limits from the `trino_limitations` key of the YAML DB. |
| `AnalyzeOracleSP` | `analyze_oracle_sp` | `run(sql: str, connector: str = "iceberg") -> str` | Full SP analysis: transpile + construct detection + connector-specific notes (`hive`/`iceberg`/`delta`/`generic`) + `manual_fix_notes` for each PL/SQL item. Returns JSON `ConversionResult`. |
| `LintTrinoSQL` | `lint_trino_sql` | `run(sql: str) -> str` | Delegates to `genie.core.lint_analyzer.analyze(sql)`; returns structured findings (severity, rule, score, fix suggestions) for Oracle residuals and Trino anti-patterns. |

### Module-level helpers (internal, not registered)

| Function | Signature | Description |
|---|---|---|
| `_load_db` | `() -> dict` | Lazy-loads `data/oracle_trino_functions.yaml` into module-level `_DB`; cached after first call. |
| `_truncate` | `(text: str, limit: int = 3000) -> str` | Truncates display SQL to `MAX_SQL_DISPLAY` (3000) chars. |
| `_sqlglot_transpile` | `(sql: str) -> tuple[str, list[str], bool]` | Wraps `sqlglot.transpile`; returns `(transpiled, errors, success)`. `success=False` on `ImportError` or any exception. |
| `_detect_unsupported` | `(sql: str) -> list[UnsupportedConstruct]` | Strips comments/strings then pattern-matches each entry in `ORACLE_CONSTRUCTS`; deduplicates by construct name. |

### Models (`models.py`)

| Dataclass | Fields | `to_dict` output keys |
|---|---|---|
| `UnsupportedConstruct` | `construct`, `severity`, `message`, `suggestion` | same 4 keys |
| `ConversionResult` | `converted_sql`, `unsupported`, `warnings`, `confidence`, `manual_fix_notes` | same 5 keys; `confidence` rounded to 4 decimal places |

## Invariants

- `_detect_unsupported` always runs on the **original** Oracle SQL, never on the
  `sqlglot` output; this prevents lossy/incorrect transpilations from hiding
  constructs that require manual attention.
- `_sqlglot_transpile` uses `error_level=IGNORE` so partial transpilation succeeds
  rather than raising; callers check `success=False` to distinguish hard failures
  (missing `sqlglot` package or unhandled exception) from partial results.
- `_load_db` is module-singleton (`_DB`): mutating the returned dict affects all
  subsequent calls in the same process.
- `LookupOracleFunction.run` returns at most 5 results; exact-name match takes
  priority over substring match.
- `LookupOracleType.run` is exact-match only (case-insensitive); no fuzzy fallback.
- `AnalyzeOracleSP` inserts a preamble into `manual_fix_notes` only when at least
  one PL/SQL construct is flagged; an empty SP produces an empty list.
- `register` must be called exactly once per registry instance to avoid duplicate
  skill registration.

## Change log

- `df1131522263a60bac2a7a0326499f43bc63c490`: initial module card; six skills
  documented (`transpile_sql`, `lookup_oracle_function`, `lookup_oracle_type`,
  `list_trino_limitations`, `analyze_oracle_sp`, `lint_trino_sql`); models and
  internal helpers captured.
