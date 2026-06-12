---
covers:
  - "tests/test_pre_execution_diagnosis_wiring.py"
  - "tests/test_preflight_decision_builder.py"
  - "tests/test_preflight_state_machine_acceptance.py"
  - "tests/test_providers.py"
  - "tests/test_r10_join_key_computed.py"
  - "tests/test_registry.py"
  - "tests/test_rows_equivalent.py"
  - "tests/test_rule_id_contract.py"
  - "tests/test_run_loop_mode_dispatch.py"
  - "tests/test_run_manager.py"
  - "tests/test_session_manager.py"
  - "tests/test_setup_wizard.py"
  - "tests/test_skill_discovery_post_cut.py"
  - "tests/test_skill_tiers.py"
  - "tests/test_sql_patterns.py"
  - "tests/test_sql_static_orchestrator.py"
  - "tests/test_sql_static_rules.py"
  - "tests/test_static_summary_line.py"
  - "tests/test_tgenie_provider.py"
  - "tests/test_tool_call.py"
last_synced: "572f7ff30399bed1a1a3c230918ba037ae874272"
---

## Purpose

Third direct-test batch covering the preflight state machine (v45), rule-id contract enforcement, SQL static analysis rules and orchestrator, run-loop mode dispatch, diagnosis prompt wiring, rows equivalence, registry/skill tiers, session manager, setup wizard, provider SSE parsing, and tool-call parsing. Together these tests lock the correctness of genieCLI's two execution paths (MCP + `--direct`) plus the shared static-analysis pipeline.

## Exports

# No tracked files found for module 'tests/[direct-3]'

## Invariants

- `PreflightRoute` enum has exactly 6 values; adding or removing a value breaks `test_should_have_preflight_route_enum_with_all_six_values` (test_preflight_state_machine_acceptance.py:401).
- `PreflightDecision` is frozen — post-construction mutation raises `FrozenInstanceError`; enforced by `test_should_have_preflight_decision_dataclass_frozen` (test_preflight_state_machine_acceptance.py:412).
- `rule_gate._STATIC_ACTIONS` keys and `pre_execution_diagnosis._RULE_KIND_MAP` keys must equal `ALL_RULE_IDS` exactly — no stale guesses, no gaps; enforced by `test_consumer_maps_cover_exactly_the_real_rule_ids` (test_rule_id_contract.py:46).
- `analyze()` must emit only ids in `ALL_RULE_IDS` across the test corpus; enforced by `test_producers_emit_only_known_rule_ids` (test_rule_id_contract.py:56).
- Both MCP and `--direct` paths must route identically for the same inputs; enforced by `test_should_produce_same_route_for_same_inputs_on_both_paths` (test_preflight_state_machine_acceptance.py:426).
- Write-SQL dispatch must skip `measure`/`explain`/loop and route to write-analysis; enforced by `test_direct_write_analysis_skips_measure_explain_and_loop` (test_run_loop_mode_dispatch.py:176).
- Wiring tests confirm diagnosis directions are injected into both path prompts; see `test_should_inject_same_diagnosis_header_on_both_paths` (test_pre_execution_diagnosis_wiring.py:147).
- `rows_equivalent` treats `NaN` as equal to `NaN` but not equal to `None`; enforced by `test_rows_equivalent_nan_both_equal` and `test_rows_equivalent_nan_not_equal_null` (test_rows_equivalent.py:96, 85).
- `SkillRegistry.clear()` resets CLI-discovery flag; enforced by `test_cli_discovery_flag_reset_on_clear` (test_registry.py:107).

## Change log

- 572f7ff30399bed1a1a3c230918ba037ae874272: initial card creation
