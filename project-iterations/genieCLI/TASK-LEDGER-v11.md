# TASK-LEDGER

## Basic Info

- Project: genieCLI v11 — DX + Subtraction + Report Depth
- Repo Folder: project-iterations/genieCLI/
- Iteration: 11
- Owner: Emily (Claude Code)
- Status: complete
- Last Updated: 2026-04-13T23:30+08:00
- Current Focus: Done — all tasks complete

## Goal

- One-line summary:
  Continue subtraction (remove shell/file/git ops), deepen report with table structure suggestions, add Chinese locale, improve DX with setup diagnostics.
- Done when:
  1. `shell_ops/`, `file_ops/`, `git_ops/` skills + tests removed;
  2. Report includes "Table Structure Suggestions" section via `information_schema` queries;
  3. `generate_report(locale="zh")` outputs 繁體中文 headers/summary/suggestions;
  4. `genie setup check` validates LLM + Trino + MCP connectivity;
  5. MCP client version fixed to 5.0.0;
  6. All tests pass (new + no regressions).

## Carryover

- v10 T9 (live verify on Sam's MCP server) still blocked — stays on Sam.
- Pre-existing trino linter/integration test skips (10 skipped, same baseline).

## Todo

| ID | Status | Pri | Task | Owner | Note |
|----|--------|-----|------|-------|------|
| T1 | done | P1 | Remove `shell_ops/` skill + `test_shell_ops.py` | Emily | ~104 LOC removed |
| T2 | done | P1 | Remove `file_ops/` skill + `test_file_ops.py` | Emily | ~116 LOC removed |
| T3 | done | P1 | Remove `git_ops/` skill + `test_git_ops.py` | Emily | ~129 LOC removed |
| T4 | done | P0 | Add `_extract_table_names(sql)` — parse table refs via sqlglot | Emily | Uses sqlglot.exp.Table |
| T5 | done | P0 | Add `_fetch_table_metadata(client, tables)` — query information_schema via MCP | Emily | Graceful fallback on error |
| T6 | done | P0 | Add `_generate_table_suggestions(metadata)` — partition/bucket/type/sort analysis | Emily | 5 rule categories |
| T7 | done | P0 | Add section 10 "Table Structure Suggestions" to report template | Emily | Graceful skip if no metadata |
| T8 | done | P1 | Add `locale` param to `generate_report()` — zh = 繁體中文 headers/summary/suggestions | Emily | 43 label keys, SQL/metrics stay EN |
| T9 | done | P1 | Add `genie setup check` — diagnose LLM + Trino + MCP connectivity | Emily | Wired as `genie setup check` |
| T10 | done | P2 | Fix MCP client version 4.1.0 → 5.0.0 | Emily | Both occurrences updated |
| T11 | done | P1 | Tests for table suggestions, Chinese report, setup check | Emily | 27 new tests |
| T12 | done | P0 | Full pytest run, confirm no regressions | Emily | 576 passed, 10 skipped, 0 failed |

## Verify

- Evidence checked: 2026-04-13
- Source of evidence: pytest (576 passed, 10 skipped, 0 fail) + multi-file verification agent
- Verification result: PASS
  - All new tests pass (27 new: 6 table extraction, 6 table suggestions, 6 Chinese locale, 3 report suggestions, 2 setup check, 4 existing report tests updated)
  - No remaining references to deleted skills
  - Forward reference in EnhancementReport safe via `from __future__ import annotations`
  - EN/ZH label dicts have identical 43 keys
  - setup_check wired correctly in cli.py

## Reports

### Ledger setup — 2026-04-13T23:00+08:00

- Created v11 ledger. Sam confirmed: Chinese scope = headers+summary+suggestions in 繁中, SQL/metrics English. information_schema = yes, query via MCP.

### Round 1 — 2026-04-13T23:30+08:00

- **PLAN**: 5 phases — subtraction, table suggestions, Chinese locale, DX, tests
- **DISPATCH**: All 12 tasks executed
- **VERIFY**: 576 passed, 10 skipped, 0 failed. Multi-file verification agent: all 6 checks PASS.
- **UPDATE**: Ledger marked complete

Files deleted:
- `genie/skills/shell_ops/` (directory, ~104 LOC)
- `genie/skills/file_ops/` (directory, ~116 LOC)
- `genie/skills/git_ops/` (directory, ~129 LOC)
- `tests/test_shell_ops.py`
- `tests/test_file_ops.py`
- `tests/test_git_ops.py`

Files modified:
- `genie/skills/mcp_trino/research.py` — table metadata types, extraction, suggestions, Chinese locale, report section 10
- `genie/skills/mcp_trino/client.py` — version 4.1.0 → 5.0.0
- `genie/setup_wizard.py` — added setup_check()
- `genie/cli.py` — wired setup_check into wizards dict
- `tests/test_mcp_research.py` — 27 new tests (table extraction, suggestions, Chinese locale)
- `tests/test_setup_wizard.py` — 2 new tests (setup_check)

Net LOC change: ~-350 removed (skills), ~+350 added (table suggestions + locale + setup check + tests) = roughly neutral in total LOC but much higher value density.
