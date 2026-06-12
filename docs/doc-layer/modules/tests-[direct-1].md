---
covers:
  - "tests/__init__.py"
  - "tests/conftest.py"
  - "tests/test_anthropic_provider.py"
  - "tests/test_branch_command.py"
  - "tests/test_checkpoint.py"
  - "tests/test_cli.py"
  - "tests/test_cli_commands.py"
  - "tests/test_cli_coverage.py"
  - "tests/test_context_manager.py"
  - "tests/test_cost_reader.py"
  - "tests/test_ctas_inner_select.py"
  - "tests/test_detection_scan.py"
  - "tests/test_direct_metadata_acceptance.py"
  - "tests/test_direction_efficacy.py"
  - "tests/test_dual_path_rule_id_equivalence.py"
  - "tests/test_explain_depth_acceptance.py"
  - "tests/test_input_completer.py"
  - "tests/test_integration_context_profiles.py"
  - "tests/test_journal.py"
  - "tests/test_mcp_plan_cost_loop.py"
last_synced: "572f7ff30399bed1a1a3c230918ba037ae874272"
---

## Purpose

Unit and acceptance test suite for genieCLI's core tier-1 modules. Covers provider
wiring (Anthropic), CLI config and commands, context management, session checkpointing,
SQL utilities (CTAS extraction, detection scan, explain-depth analysis), journal I/O,
MCP plan-cost loop, and the --direct metadata fetching path. `conftest.py` supplies
shared `FakeProvider` and `NullSink` fixtures used across the suite. Acceptance files
(`test_direct_metadata_acceptance.py`, `test_explain_depth_acceptance.py`,
`test_dual_path_rule_id_equivalence.py`) pin cross-path parity contracts that must
hold whenever MCP and --direct paths diverge.

## Exports

# No tracked files found for module 'tests/[direct-1]'

## Invariants

- `FakeProvider.complete` pops responses in FIFO order and records every
  `CompletionRequest` in `self.calls`; empty responses list yields an empty string
  (conftest.py:30-33).
- `NullSink.confirm` always returns `True`, so tests using it must not rely on
  negative confirmation paths (conftest.py:61-62).
- `test_direct_metadata_acceptance.py` contains **no** `@pytest.mark.xfail` decorators;
  all tests run unconditionally. The module docstring (line 3) carries the historical
  prose "All tests are xfail(strict=True) until the develop step implements the
  feature" as pre-implementation commentary that was not removed after the develop step
  landed; it does not describe the current test behaviour
  (test_direct_metadata_acceptance.py:1-17).
- `test_dual_path_rule_id_equivalence.py` pins that every rule ID in `ALL_RULE_IDS`
  maps to an entry in both `_RULE_KIND_MAP` and `_STATIC_ACTIONS`; adding a new rule
  without updating those tables will fail `test_pin_table_covers_all_rule_ids`
  (test_dual_path_rule_id_equivalence.py:157-165).
- `test_checkpoint.py` imports `_run_git`, `_sanitize_label`, `checkpoint_create`,
  `checkpoint_restore`, `git_is_clean`, `git_is_repo` directly; renaming any of these
  in the runtime module breaks the import at test_checkpoint.py:8-15.
- `test_explain_depth_acceptance.py` loads plan fixtures from
  `tests/fixtures/explain_plans/` via `_load(name)`; deleting or renaming a fixture
  JSON file will raise `FileNotFoundError` in tests that reference it
  (test_explain_depth_acceptance.py:84-97).
- `test_mcp_plan_cost_loop.py` verifies that when `long_query_opt_in=False`, the
  standard loop (not plan-cost) runs; the `_run_mcp_plan_cost_loop` dispatch gate must
  respect this flag (test_mcp_plan_cost_loop.py:299-337).
- `test_integration_context_profiles.py` asserts that `ContextManager` window sizes
  differ between weak and strong model profiles; any collapsing of profile tiers will
  fail `test_weak_model_has_different_context_limits_than_strong`
  (test_integration_context_profiles.py:67-76).

## Change log

- 572f7ff30399bed1a1a3c230918ba037ae874272: initial card generation for direct-1 test module batch
- 572f7ff30399bed1a1a3c230918ba037ae874272: fix hard_fail — remove fabricated xfail invariant; replace with accurate note that tests run unconditionally and docstring commentary is historical
