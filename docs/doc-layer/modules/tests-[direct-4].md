---
covers:
  - "tests/test_trino_datasize_parser.py"
  - "tests/test_trino_integration.py"
  - "tests/test_trino_linter.py"
  - "tests/test_trino_optimize.py"
  - "tests/test_trino_query_skill.py"
  - "tests/test_write_analysis_decompose.py"
  - "tests/test_zero_cost_directed_report.py"
last_synced: "572f7ff30399bed1a1a3c230918ba037ae874272"
---

## Purpose

Test suite for Trino-related runtime components: the datasize parser, live Trino
integration, SQL linter rules and scoring, the five-stage trino_optimize pipeline
(Baseline / Decompose / Optimize / Recompose / Verify), the trino_query skill and
connection-profile subsystem, write-analysis advisory decomposition with column/
semantic safety gates, and the zero-cost directed-diagnosis report for both MCP
and `--direct` execution paths. All tests except `test_trino_integration.py` are
pure-unit and require no live cluster.

## Exports

# No tracked files found for module 'tests/[direct-4]'

## Invariants

- `test_trino_integration.py` skips the entire module when Trino is not reachable
  at `localhost:8085`; the guard is set at module level via `pytestmark`
  (`test_trino_integration.py:21`).

- `test_trino_optimize.py` declares itself fully pure-unit; the file docstring
  states "All pure-unit; no live Trino cluster needed. Stubs via closures."
  (`test_trino_optimize.py:3-5`).

- The write-analysis advisory path is documented as non-executing: the module
  docstring states it never calls `verify`, `baseline`, `explain_runner`, or
  `query_runner` (`test_write_analysis_decompose.py:1-9`). The test
  `test_decompose_never_calls_verify_or_baseline` enforces the subset it can
  directly patch — `verify` and `baseline` — by raising `AssertionError` if
  either is invoked; `explain_runner` is not patched or asserted in that test
  (`test_write_analysis_decompose.py:417-428`).

- Column safety gate: a fragment rewrite that changes the output column set is
  always reverted; pinned by `test_column_gate_reverts_column_adding_rewrite` and
  `test_column_gate_reverts_star_expansion`
  (`test_write_analysis_decompose.py:57`, `89`).

- Semantic safety gate: rewrites that drop WHERE clauses, change JOIN types, add
  DISTINCT, alter ORDER BY with LIMIT, or switch INTERSECT/EXCEPT are reverted;
  multiple parametrized cases cover these (`test_write_analysis_decompose.py:172–803`).

- Zero-cost report: `--diagnose-only` must not trigger any `_measure` /
  `_measure_mcp` call; `test_mcp_diagnose_only_emits_report_without_running_any_query`
  asserts zero query execution (`test_zero_cost_directed_report.py:140`).

- MCP path emits `LongQueryAbort` carrying `report_markdown` on gate-trip; direct
  path returns `{"status": "diagnosed", "report_markdown": ...}`; both are verified
  independently and then cross-checked for identical report headers
  (`test_zero_cost_directed_report.py:172`, `310`, `345`).

- `_parse_trino_datasize` uses binary base (1 kB = 1024 B) and returns `None` for
  all error cases; overflow must not raise (`test_trino_datasize_parser.py:77`).

- Linter tests use `_needs_sqlglot` skip marker; tests that require AST parsing
  are skipped when sqlglot is absent (`test_trino_linter.py:17`).

## Change log

- 572f7ff30399bed1a1a3c230918ba037ae874272: initial card created
- 572f7ff30399bed1a1a3c230918ba037ae874272: fix invariant — test:417 only patches verify/baseline; explain_runner exclusion is docstring-level only
