# Spec Verification Report - T2

**Verdict**: SPEC_COMPLIANT
**Context Packet**: v29 T2 — wire directions into MCP prompt + memory metric
**Mode**: v2-sdd

## Verify re-run

- **Command/check**: `.venv/bin/python -m pytest tests/test_pre_execution_diagnosis.py tests/test_pre_execution_diagnosis_wiring.py` + full suite `pytest -q`
- **Result**: PASS
- **Output excerpt**:
  - `test_pre_execution_diagnosis.py`: 35 passed in 0.16s
  - `test_pre_execution_diagnosis_wiring.py`: 3 passed in 0.22s
  - Full suite: **772 passed in 1.43s** (0 skipped, 0 failures)

## Files actually changed

- `genie/skills/mcp_trino/pre_execution_diagnosis.py` — +33 lines (format helper + `__all__` export)
- `genie/skills/mcp_trino/research.py` — +77 lines (`_build_mcp_explain_runner`, diagnosis wiring, `MCP_METRICS` append)
- `genie/skills/trino_query/research.py` — +31 lines (diagnosis wiring, `METRICS` append)
- `project-iterations/genieCLI/CURRENT.md` — +76 lines (ledger update)
- `tests/test_pre_execution_diagnosis.py` — +50 lines (4 new format-helper tests)
- `tests/test_pre_execution_diagnosis_wiring.py` — new untracked file, 3 wiring tests

## Requirement mapping

| Requirement                                                                                              | Verdict | Evidence                                                                                                                                                                                                                            |
| -------------------------------------------------------------------------------------------------------- | ------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `format_directions_for_prompt(directions, *, limit=6) -> str` added pure to `pre_execution_diagnosis.py` | pass    | `pre_execution_diagnosis.py:311-335`                                                                                                                                                                                                |
| Returns `""` on empty input                                                                              | pass    | `pre_execution_diagnosis.py:323`; confirmed by `test_should_return_empty_string_when_no_directions` PASS                                                                                                                            |
| Numbered block headed "Pre-execution diagnosis"                                                          | pass    | `pre_execution_diagnosis.py:327-329`                                                                                                                                                                                                |
| Exported in `__all__`                                                                                    | pass    | `pre_execution_diagnosis.py:384-390` — `format_directions_for_prompt` listed                                                                                                                                                        |
| MCP path: `_build_mcp_explain_runner(client)` added                                                      | pass    | `mcp_trino/research.py:595-625`                                                                                                                                                                                                     |
| MCP path: `plan_cost` imported                                                                           | pass    | `mcp_trino/research.py:1143` — `plan_cost` in import from `.preflight`                                                                                                                                                              |
| MCP path: table metadata fetched pre-loop (qualified refs only)                                          | pass    | `mcp_trino/research.py:1245-1251` — `diag_refs` filters `c and s`; `_fetch_table_metadata` called before loop                                                                                                                       |
| MCP path: metadata reused post-loop (single fetch)                                                       | pass    | `mcp_trino/research.py:1472` — `metadata = pre_table_metadata if pre_table_metadata else _fetch_table_metadata(...)`                                                                                                                |
| MCP path: `pre_execution_diagnosis(...)` + `format_directions_for_prompt(...)` called                    | pass    | `mcp_trino/research.py:1259-1266`                                                                                                                                                                                                   |
| MCP path: block injected into sys_prompt AFTER skill guide BEFORE skill prompt body                      | pass    | `mcp_trino/research.py:1274-1288` — order: base rules → `skill_instructions` → `directions_block` → `skill_prompt`                                                                                                                  |
| MCP path: `"peak_memory_bytes"` appended to `MCP_METRICS`                                                | pass    | `mcp_trino/research.py:1536-1540` — `"peak_memory_bytes"` present in `MCP_METRICS` list                                                                                                                                             |
| `--direct` path: `pre_execution_diagnosis` + `plan_cost` imported                                        | pass    | `trino_query/research.py:772-776`                                                                                                                                                                                                   |
| `--direct` path: `explain_cost` computed via `explain_runner` (None-safe)                                | pass    | `trino_query/research.py:778-783` — `if explain_runner is not None` guard                                                                                                                                                           |
| `--direct` path: `table_metadata=None`                                                                   | pass    | `trino_query/research.py:789`                                                                                                                                                                                                       |
| `--direct` path: block injected before skill prompt body                                                 | pass    | `trino_query/research.py:798-810` — `directions_block + "\n\n"` prefixes `skill_prompt` when non-empty                                                                                                                              |
| `--direct` path: `"peak_memory_bytes"` appended to `METRICS`                                             | pass    | `trino_query/research.py:1149` — `METRICS` list includes `"peak_memory_bytes"`                                                                                                                                                      |
| 4 format-helper unit tests in `tests/test_pre_execution_diagnosis.py`                                    | pass    | Lines 469-511: `test_should_return_empty_string_when_no_directions`, `test_should_render_one_numbered_line_per_direction`, `test_should_cap_rendered_lines_at_limit`, `test_should_be_deterministic_for_same_directions` — all PASS |
| Dual-path wiring test `tests/test_pre_execution_diagnosis_wiring.py`, 3 tests                            | pass    | 3 tests collected and PASS: `--direct` path, MCP path, symmetry assertion                                                                                                                                                           |
| Wiring test patches `genie.session.manager.new_session`, sentinel exception                              | pass    | `test_pre_execution_diagnosis_wiring.py:40-41, 64-65, 109-114`                                                                                                                                                                      |
| Wiring test fakes baseline with `peak_memory_bytes` over 1 GiB threshold                                 | pass    | `test_pre_execution_diagnosis_wiring.py:29, 55, 99` — `_HIGH_PEAK = HIGH_PEAK_MEMORY_BYTES + 1`                                                                                                                                     |
| Wiring test asserts diagnosis header before skill prompt body on BOTH paths                              | pass    | `test_pre_execution_diagnosis_wiring.py:81-84, 130-132` — `index` ordering assert                                                                                                                                                   |
| Full suite green, zero regression                                                                        | pass    | 772 passed, T1 baseline was 755 passed + 10 skipped; net +17 passes from 38 new tests absorbed into the count                                                                                                                       |

## Extra work check

- None. Every changed line directly supports T2 requirements. `CURRENT.md` update is required ledger discipline per task-ledger-cycle SOP.

## Ledger updates

- `STATUS.md`: not applicable — T2 is in-progress/uncommitted; STATUS reflects ongoing iteration state
- `CURRENT.md`: pass — updated with T2 step details, Verify column evidence, and `peak_memory_bytes` references at multiple locations

## Usage compliance

- Not applicable — T2 is an internal optimizer wiring change with no direct user-visible behavior change beyond a `Pre-execution diagnosis` section appearing in the LLM's system prompt. No Usage Brief or Acceptance Criteria doc targets user-facing output. The Tkt's own acceptance bar (prompt injection, metrics list, test counts) is the operative spec and is fully met.

## Issues

- None

## Recommendation

pass to quality-verifier
