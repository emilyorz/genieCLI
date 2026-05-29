# Spec Candidate — T1 pre_execution_diagnosis

**Todo:** T1 — shared `pre_execution_diagnosis` module
**Phase:** DO / Step 4 (Spec Candidate)
**Author:** Emily (orchestrator+executor)
**Date:** 2026-05-27

---

## 1. Goal

A pure, deterministic function that turns the three already-computed-but-discarded diagnostics (static AST findings + EXPLAIN plan cost + table metadata) plus an optional post-run memory signal into a ranked list of concrete `OptimizationDirection` objects. NO query execution. This is the contract T2 (MCP wiring), T3 (long-query zero-cost report), and T4 (`--direct` parity) all consume.

## 2. Public contract

```python
@dataclass(frozen=True)
class OptimizationDirection:
    kind: str            # stable machine id
    severity: str        # "high" | "medium" | "low"
    rationale: str       # human WHY (fed to LLM prompt)
    evidence: str        # provenance string
    target_metric: str   # RunMetrics field it aims to move

def pre_execution_diagnosis(
    sql: str, *,
    static_report,                 # StaticAnalysisReport | None
    explain_cost,                  # (rows_est, bytes_est, raw_plan_json) | None
    table_metadata=None,           # list[TableMetadata] | None
    peak_memory_bytes=None,        # int | None
) -> list[OptimizationDirection]: ...
```

`kind` vocabulary (stable, machine-comparable in tests):

- `reduce-scan` — large input scan; target `physical_input_bytes`
- `memory-pressure` — large build side / high peak memory; target `peak_memory_bytes`
- `fix-cartesian-join`, `add-join-condition`, etc. — derived from static `rule_id`; target `wall_time_ms`/`cpu_time_ms`
- `leverage-partitioning`, `leverage-sort` — metadata-driven; target `physical_input_bytes`

## 3. Four contributors (each pure, independently unit-tested)

| #   | Source       | Input                                     | Emits                                                                                                             |
| --- | ------------ | ----------------------------------------- | ----------------------------------------------------------------------------------------------------------------- |
| 1   | static       | `StaticAnalysisReport.findings`           | one direction per Finding; severity passthrough; `evidence=static:{rule_id}@L{line}`                              |
| 2   | explain-cost | `(rows_est, bytes_est, raw_plan_json)`    | `reduce-scan` if bytes/rows over threshold; recursive walk → max non-leaf `outputSizeInBytes` → `memory-pressure` |
| 3   | metadata     | `list[TableMetadata]`                     | `leverage-partitioning`/`leverage-sort` when props present                                                        |
| 4   | memory       | `peak_memory_bytes` + build-side from (2) | guarantees ≥1 memory-targeted class                                                                               |

## 4. Invariants

1. **Pure** — no execution, no I/O, no network. Deterministic.
2. **Deterministic ranking** — stable sort `(severity_rank, source_rank, kind)`. `severity_rank{high:0,medium:1,low:2}`, `source_rank{static:0,explain:1,memory:2,metadata:3}`.
3. **Total over partial inputs** — every arg may be `None`/empty → contributes nothing, never raises. All-absent → `[]`.
4. **Leaf module** — no top-level import of any `research.py`. `TableMetadata` consumed by duck-typing (`getattr`) or `TYPE_CHECKING`.

## 5. Failure modes

| Condition                                                | Behavior                             |
| -------------------------------------------------------- | ------------------------------------ |
| `static_report is None` or `.parse_error` set            | static contributor → []              |
| `explain_cost is None` / `raw_plan_json` malformed/empty | explain walk finds nothing, no raise |
| unqualified SQL → `table_metadata` empty/None            | metadata contributor → []            |
| all inputs absent                                        | return `[]` (NOT exception)          |

## 6. Thresholds (module-level, named, documented)

- `LARGE_SCAN_BYTES` — bytes_est above which `reduce-scan` emits (initial: 1 GiB; tune in Dev).
- `HIGH_PEAK_MEMORY_BYTES` — peak/ build-side above which `memory-pressure` emits (initial: 1 GiB).
- Rationale: no live cluster this session → thresholds validated against synthetic plan dicts; documented as tunable, single source of truth at module top.

## 7. Test plan (Step 7 Verify hooks)

- ranking order deterministic across repeated calls + shuffled contributor inputs
- memory direction emitted when plan signals high peak (synthetic `raw_plan_json` with large non-leaf `outputSizeInBytes`)
- memory direction emitted when `peak_memory_bytes` over threshold
- empty/parse-fail inputs → `[]`, no raise (parametrized over each arg None + all-None)
- static Finding → direction mapping (severity passthrough + evidence string shape)
- metadata partition/sort props → leverage-\* directions; absent props → none

## 8. Open risk carried to Dev

`table_metadata` is fetched POST-loop on MCP path (research.py:1387) — T2's job to move it pre-loop. T1 only needs to consume it gracefully when absent. Memory proxy via `outputSizeInBytes` is unvalidated against real plans this session; flagged for live re-check when Sam runs from cluster.
