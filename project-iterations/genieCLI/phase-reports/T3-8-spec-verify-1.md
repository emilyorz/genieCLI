# Spec Verification Report - T3

**Verdict**: SPEC_COMPLIANT
**Context Packet**: CP-T3-8-spec-1 (inline Tkt from CURRENT.md T3 Step 6)
**Mode**: v3-strict (runtime-honesty deviation: hooks installed not live)

---

## Verify re-run

- **Command**: `cd /Users/leeabc/work/emilyorz/genieCLI && .venv/bin/python -m pytest -q`
- **Result**: PASS
- **Output excerpt**: `780 passed in 1.58s`

Ledger claims 780 passed. Re-run confirms exactly 780 passed, 0 failed, 0 regressions.

---

## Files actually changed

Derived from reading the five files named in the Tkt:

- `genie/chat.py` (flag parse lines 851-853, help text lines 291, 304, 310)
- `genie/skills/mcp_trino/research.py` (lines 1133, 1180-1199, 1262-1282, 1928-1941, 1757-1776)
- `genie/skills/mcp_trino/pre_execution_diagnosis.py` (lines 343-418 `format_directions_report`, lines 467 `__all__`)
- `genie/skills/trino_query/research.py` (lines 646-677 `_assemble_direct_directions`, 680-695 `_run_optimization_loop` sig, 717-732 diagnose-only block, 793-814 gate-trip block, 1196-1198 entry sig, 1268-1278 `_direct_explain_runner`, 1295 pass-through, 1305-1318 "diagnosed" handler)
- `tests/test_zero_cost_directed_report.py` (new file, 277 lines, 8 tests)

---

## Requirement mapping

| Requirement                                                                                                                            | Verdict | Evidence                                                                                                                                                                                                                                                                                             |
| -------------------------------------------------------------------------------------------------------------------------------------- | ------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `diagnose_only` flag parsed in `chat.py`                                                                                               | pass    | `chat.py:851-853` parses `--diagnose-only → kwargs["diagnose_only"]=True`; threaded to both wrappers via `**kwargs` at `:863` and `:882`                                                                                                                                                             |
| `--diagnose-only` help text and example in `chat.py`                                                                                   | pass    | `chat.py:291` usage line includes `[--diagnose-only]`; `:304` flag description; `:310` example                                                                                                                                                                                                       |
| MCP path: `--diagnose-only` short-circuits BEFORE any baseline                                                                         | pass    | `mcp_trino/research.py:1184` — `if diagnose_only:` block fires before `_measure_mcp` call at `:1208`; test `test_mcp_diagnose_only_emits_report_without_running_any_query` patches both `_measure_mcp` and `_fetch_explain_analyze` and asserts `assert_not_called()` on both                        |
| `--diagnose-only`: `peak_memory_bytes=None`                                                                                            | pass    | `mcp_trino/research.py:1188-1189` passes `peak_memory_bytes=None` to `_assemble_mcp_directions`                                                                                                                                                                                                      |
| `--direct` path: `--diagnose-only` short-circuits BEFORE any baseline                                                                  | pass    | `trino_query/research.py:721` — `if diagnose_only:` fires before `_measure` call at `:739`; test `test_direct_diagnose_only_returns_diagnosed_without_baseline` patches `_measure` and asserts `assert_not_called()`                                                                                 |
| `--direct` path: `--diagnose-only` returns `{"status":"diagnosed","report_markdown":...}`                                              | pass    | `trino_query/research.py:732` returns exactly that dict                                                                                                                                                                                                                                              |
| MCP gate-trip: baseline ran exactly once, real `peak_memory_bytes` feeds diagnosis                                                     | pass    | `mcp_trino/research.py:1208` runs `_measure_mcp` once before gate check; `:1272` feeds `peak_memory_bytes=getattr(baseline.metrics, "peak_memory_bytes", 0) or None`; test `test_mcp_gate_trip_emits_report_after_baseline_no_explain_analyze` confirms `_fetch_explain_analyze.assert_not_called()` |
| MCP gate-trip: no iteration, no EXPLAIN ANALYZE                                                                                        | pass    | Gate-trip block at `:1262-1282` raises `LongQueryAbort` immediately after building report; iteration loop never entered; `_fetch_explain_analyze` not called as confirmed by test                                                                                                                    |
| MCP `run_mcp_enhancement` RAISES `LongQueryAbort` with `report_markdown`                                                               | pass    | `:1196-1199` (diagnose-only) and `:1279-1282` (gate-trip) both `raise LongQueryAbort(..., report_markdown=report_md)`                                                                                                                                                                                |
| `run_trino_research_via_mcp` catches `LongQueryAbort` and writes `report/trino-research-diagnose-{ts}.md`                              | pass    | `mcp_trino/research.py:1928-1941` — `except LongQueryAbort as lqa:` checks `lqa.report_markdown`, writes to `report_dir / f"trino-research-diagnose-{...}.md"`                                                                                                                                       |
| `--direct`: `_run_optimization_loop` RETURNS `{"status":"diagnosed",...}`                                                              | pass    | `:732` (diagnose-only) and `:808-814` (gate-trip) both return that dict shape                                                                                                                                                                                                                        |
| `--direct`: `run_trino_research` writes `report/trino-research-diagnose-{ts}.md`                                                       | pass    | `trino_query/research.py:1305-1318` — `if result["status"] == "diagnosed":` branch writes the file                                                                                                                                                                                                   |
| `_assemble_direct_directions` added to `--direct` path (mirror MCP's `_assemble_mcp_directions`, `table_metadata=None`)                | pass    | `trino_query/research.py:646-677` — function exists, calls `pre_execution_diagnosis(original_sql, static_report=static_report, explain_cost=explain_cost, table_metadata=None, peak_memory_bytes=peak_memory_bytes)`                                                                                 |
| `_direct_explain_runner` added (wraps `_execute_sql` with `EXPLAIN (FORMAT JSON)`)                                                     | pass    | `trino_query/research.py:1268-1277` — closure wraps `_execute_sql(f"EXPLAIN (FORMAT JSON) {s}", capture_rows=True)`, extracts first cell str, None on error/empty                                                                                                                                    |
| `format_directions_report` in `pre_execution_diagnosis.py`, header `# Trino Query Pre-execution Diagnosis Report (zero-cost directed)` | pass    | `pre_execution_diagnosis.py:343-418` — function exists; `:360` emits that exact header as first element of `lines`                                                                                                                                                                                   |
| Report emitted at zero query cost on `--diagnose-only` (no `_measure`/`_measure_mcp`, no EXPLAIN ANALYZE)                              | pass    | Code inspection + mocked test confirms both                                                                                                                                                                                                                                                          |
| Gate-trip: no EXPLAIN ANALYZE called                                                                                                   | pass    | Code inspection: gate-trip block at `:1262-1282` (MCP) and `:793-814` (direct) raise/return immediately without calling `_fetch_explain_analyze`; wired test `test_mcp_gate_trip_emits_report_after_baseline_no_explain_analyze` asserts `explain_analyze.assert_not_called()`                       |
| Dual-path: MCP path RAISES, `--direct` path RETURNS                                                                                    | pass    | MCP path raises `LongQueryAbort` (both triggers); `--direct` returns dict (both triggers) — confirmed by tests                                                                                                                                                                                       |
| Both paths write same report filename pattern `trino-research-diagnose-{ts}.md`                                                        | pass    | `mcp_trino/research.py:1935` and `trino_query/research.py:1311` use identical pattern                                                                                                                                                                                                                |
| New test file `tests/test_zero_cost_directed_report.py` with 8 tests                                                                   | pass    | File exists at 277 lines; 8 test functions confirmed; all 8 pass                                                                                                                                                                                                                                     |
| Both paths emit same report header (symmetry)                                                                                          | pass    | `test_both_paths_emit_same_report_header_on_gate_trip` (line 247) asserts `mcp_report.startswith(_REPORT_HEADER)` and `direct_result["report_markdown"].startswith(_REPORT_HEADER)` where `_REPORT_HEADER = "# Trino Query Pre-execution Diagnosis Report (zero-cost directed)"`                     |

---

## Extra work check

One incidental in-scope item worth noting: `_assemble_direct_directions` at `trino_query/research.py:643-677` is also called by the diagnosis-prompt-injection path (the "Pre-execution diagnosis" block that was T2's work, now DRY'd to use this helper). This is not out-of-scope — T3 explicitly adds `_assemble_direct_directions` per the Tkt, and the DRY usage by the T2 injection path is a legitimate consequence of centralizing the assembler. No unrequested scope creep detected.

---

## Ledger updates

- `STATUS.md`: fail (not applicable for T3 specifically — STATUS.md still shows v28 state and references the v29 iteration as "active" but the T3 row is not individually tracked in STATUS.md; T3 row status is in CURRENT.md). The STATUS.md stale state is a pre-existing condition carried from T2 Wrap (T3 is not yet closed), so this is not a T3 violation.
- `CURRENT.md`: pass — T3 SDD walk through Step 7 is fully populated; Step 8 shows `_(dispatch pending)` which is expected since this is the spec-verifier run.
- `features/trino-research.md`: not applicable for T3 (Tkt explicitly assigns feature doc update to T4).

---

## Zero-cost guarantee verification

**`--diagnose-only` path (MCP):**

- Code path: `run_mcp_enhancement:1184` — diagnose-only block fires → calls `_assemble_mcp_directions` (cheap: EXPLAIN FORMAT JSON + catalog reads) → `format_directions_report` → raises `LongQueryAbort`. `_measure_mcp` is at line 1208, unreachable.
- Test: `test_mcp_diagnose_only_emits_report_without_running_any_query` — patches `_measure_mcp` and `_fetch_explain_analyze`, both `assert_not_called()`. PASS.

**`--diagnose-only` path (--direct):**

- Code path: `_run_optimization_loop:721` — diagnose-only block fires → calls `_assemble_direct_directions` → `format_directions_report` → returns. `_measure` is at line 739, unreachable.
- Test: `test_direct_diagnose_only_returns_diagnosed_without_baseline` — patches `_measure`, asserts `assert_not_called()`. PASS.

**Gate-trip (MCP):**

- Code path: baseline runs once at `:1208`; gate check at `:1261`; on `not gate.ok` the block at `:1262-1282` builds report from baseline's real peak and raises. `_fetch_explain_analyze` is at `:1303`, unreachable after raise.
- Test: `test_mcp_gate_trip_emits_report_after_baseline_no_explain_analyze` — patches `_fetch_explain_analyze`, asserts `assert_not_called()`, and asserts `exc.value.baseline_s > 60.0`. PASS.

**Gate-trip (--direct):**

- Code path: baseline runs once at `:739`; gate check at `:787`; on `not gate.ok` the block at `:793-814` builds report and returns `{"status":"diagnosed",...}`.
- Test: `test_direct_gate_trip_returns_diagnosed_with_report` — asserts `result["status"] == "diagnosed"` and `result["reason"] == "long_query_gate"`. PASS.

---

## Usage compliance

Based on Tkt T3 + T3 SDD Step 5 Usage Validate:

- Story: user who hit `[abort] baseline wall-time 98.4s` gets actionable directions instead of a dead end. The gate-trip path confirmed: baseline runs once, its `wall_time_ms=98_400.0` triggers the gate, report is emitted carrying `baseline_s > 60.0`. PASS.
- Story: cost-conscious user passes `--diagnose-only` for zero-cost pre-flight. Confirmed: no `_measure`/`_measure_mcp` fired. PASS.
- Acceptance: both paths write `report/trino-research-diagnose-{ts}.md`. Entry-point handlers confirmed at `mcp_trino/research.py:1935` and `trino_query/research.py:1311`. PASS.
- Acceptance: `peak_memory_bytes=None` on `--diagnose-only` (no run). Confirmed at `mcp_trino/research.py:1189` and `trino_query/research.py:725`. PASS.

---

## Issues

None blocking.

---

## Recommendation

pass to quality-verifier
