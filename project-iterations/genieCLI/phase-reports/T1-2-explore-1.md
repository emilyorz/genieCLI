# Explore Report — genieCLI v29 / T1 pre_execution_diagnosis

**Status**: DONE
**Angle**: codebase-reality
**Tool calls**: ~14

---

## Q1 — Module location

### Cross-package import map (current state)

`genie/skills/trino_query/research.py` already imports FROM `mcp_trino`:

- `from genie.skills.mcp_trino.preflight import plan_cost` — line 365
- `from genie.skills.mcp_trino.preflight import detect_no_data_reason` — line 672
- `from genie.skills.mcp_trino.preflight import check_long_query_gate, ...` — line 728

`genie/skills/mcp_trino/research.py` imports FROM `trino_query`:

- `from genie.skills.trino_query.sql_static import analyze as static_analyze` — line 1090
- `from genie.skills.trino_query.research import _run_no_data_path` — line 1131

**Existing precedent**: `genie/skills/mcp_trino/preflight.py` is already the shared module — both `research.py` files import from it via lazy imports inside functions (no top-level cycle).

### Verdict

Place `pre_execution_diagnosis.py` alongside `preflight.py`:

```
genie/skills/mcp_trino/pre_execution_diagnosis.py
```

Both research.py files already use the lazy-import-inside-function pattern to import from `mcp_trino.preflight`. The new module follows the same pattern:

- `genie/skills/mcp_trino/research.py` — top-level or lazy: `from .pre_execution_diagnosis import pre_execution_diagnosis`
- `genie/skills/trino_query/research.py` — lazy inside function: `from genie.skills.mcp_trino.pre_execution_diagnosis import pre_execution_diagnosis`

No cycle risk: `pre_execution_diagnosis.py` must NOT import from either `research.py`. It only consumes `StaticAnalysisReport` (from `trino_query.sql_static`) and pure data types — both are leaf packages with no back-imports.

**Alternative rejected**: placing in `trino_query/` would invert the existing flow (mcp_trino is the consumer of trino_query shared code, not vice versa — but preflight.py is already a counter-example). `mcp_trino/` is correct because the module needs `TableMetadata` and `TableSuggestion` which are defined in `mcp_trino/research.py` lines 103–129, making co-location mandatory to avoid a third cross-package import.

---

## Q2 — Existing diagnostic data shapes

### 2a. StaticAnalysisReport (`genie/skills/trino_query/sql_static/__init__.py`)

```python
@dataclass
class Finding:
    severity: str       # "high" | "medium" | "low"   (line 19)
    rule_id: str        # e.g. "cartesian-join"         (line 20)
    message: str                                        # (line 21)
    suggestion: str                                     # (line 22)
    line: int = 1                                       # (line 23)

@dataclass
class StaticAnalysisReport:
    findings: list[Finding]                             # (line 28)
    parse_error: str | None = None                      # (line 29)
```

Severity vocabulary: `"high"`, `"medium"`, `"low"` — confirmed at lines 33–36.

Eight rules loaded: r1_cartesian_join through r8_redundant_cast (lines 63–82). All rules return `list[Finding]`.

### 2b. plan_cost / estimate_from_explain (`genie/skills/mcp_trino/preflight.py`)

```python
def plan_cost(sql, explain_runner) -> tuple[Optional[int], Optional[int], Optional[object]]:
    # returns (rows_est, bytes_est, raw_plan_json)           (line 124-158)
```

`estimate_from_explain` walks the EXPLAIN JSON tree recursively via `node.get("estimates")` (list), `node.get("children", [])`. Fields consumed from each estimate node:

- `outputRowCount` → `rows_est` (line 111)
- `outputSizeInBytes` → `bytes_est` (line 112)

No other fields read. The `raw_plan_json` (third return) is the full parsed dict — available for structural walking downstream.

### 2c. Table metadata (`genie/skills/mcp_trino/research.py`)

Fetch method: `_fetch_table_metadata(client, tables)` at line 157. Executes TWO SQL queries via `_execute_via_mcp`:

1. `SELECT column_name, data_type, is_nullable, ordinal_position FROM {cat}.information_schema.columns WHERE table_schema=... AND table_name=...` — yields `ColumnInfo` objects (lines 176–193)
2. `SELECT property_name, property_value FROM system.metadata.table_properties WHERE catalog_name=... AND schema_name=... AND table_name=...` — yields `properties: dict[str, str]` (lines 197–214)

```python
@dataclass
class ColumnInfo:
    column_name: str          # (line 105)
    data_type: str            # (line 106)
    is_nullable: str          # (line 107)
    ordinal_position: int     # (line 108)

@dataclass
class TableMetadata:
    catalog: str
    schema: str
    table_name: str
    columns: list[ColumnInfo]
    properties: dict[str, str]   # keys: "sorted_by", "sort_order", partition keys, etc.
```

Properties used in `_generate_table_suggestions` (line 223): `meta.properties.get("sorted_by", ...)`, `meta.properties.get("sort_order", ...)`, and implicitly partitioning (checked as empty columns list).

No row counts, file counts, or file sizes in `TableMetadata` — only column schema + table properties.

---

## Q3 — Trino peak-memory field

### MCP path metric source

`_execute_via_mcp` at line 541 parses the MCP tool response dict `data["metrics"]`:

```python
# genie/skills/mcp_trino/research.py lines 557–564
m = data["metrics"]
metrics.cpu_time_ms      = float(m.get("cpu_time_ms",        m.get("cpuTimeMillis", 0)))
metrics.wall_time_ms     = float(m.get("wall_time_ms",        m.get("wallTimeMillis", 0)))
metrics.peak_memory_bytes = int(m.get("peak_memory_bytes",   m.get("peakMemoryBytes", 0)))
metrics.physical_input_bytes = int(m.get("physical_input_bytes", m.get("physicalInputBytes", 0)))
metrics.processed_rows   = int(m.get("processed_rows",       m.get("processedRows", 0)))
metrics.total_splits     = int(m.get("total_splits",         m.get("totalSplits", 0)))
```

**`peak_memory_bytes` IS already parsed** (line 561), stored in `RunMetrics.peak_memory_bytes` (dataclass field at line 42).

### The gap

`MCP_METRICS` list at line 1462 does NOT include `peak_memory_bytes`:

```python
MCP_METRICS = [
    "query_time_ms", "cpu_time_ms", "wall_time_ms",
    "physical_input_bytes", "processed_rows", "total_splits",
]
```

`peak_memory_bytes` is parsed into `RunMetrics` but never surfaced as a selectable optimization metric. Adding `"peak_memory_bytes"` to `MCP_METRICS` is the only change needed to expose it.

### --direct path

`genie/skills/trino_query/__init__.py` line 127, `_extract_metrics(stats)`:

```python
peak_memory_bytes=stats.get("peakMemoryBytes", 0),     # line 136
spilled_bytes=stats.get("spilledBytes", 0),            # line 137
```

Field name in Trino cursor stats dict: `peakMemoryBytes` (camelCase). The --direct `QueryMetrics` dataclass includes both `peak_memory_bytes` AND `spilled_bytes` (line 37) — `spilled_bytes` is a bonus signal not present in the MCP path at all.

### Test fixtures

No `tests/fixtures/explain_plans/*.json` files exist in the repo (directory does not exist). Cannot confirm memory/estimate fields from fixtures.

The EXPLAIN JSON shape used by `estimate_from_explain` only extracts `outputRowCount` and `outputSizeInBytes` — no memory fields. However, `outputSizeInBytes` on a large join node (hash-join build side) IS a valid proxy for memory pressure: a large `outputSizeInBytes` on a non-leaf node indicates a big intermediate build side.

---

## Q4 — MCP query-tool resolution

`_resolve_query_tool` at line 511:

**Pass 1** (lines 517–522): exact name match against `("query", "trino_query", "execute", "execute_query", "run_query")`.

A tool named `mcp_trino_query` (the pattern from `McpTrinoSkill.__init__` at `mcp_trino/__init__.py` line 29: `self.name = f"mcp_{tool_def['name']}"`) would NOT match pass 1 — the MCP-registered tool name is the raw server tool name (e.g. `query`), not the genieCLI-prefixed name. The `_resolve_query_tool` calls `client.list_tools()` which returns the raw MCP server tool names, so `"query"` or `"trino_query"` from the server WILL match pass 1.

**Pass 2** (lines 523–527): scan all tools for any with a SQL-shaped param (`"sql"`, `"query"`, or `"statement"` in `inputSchema.properties`). This is the fallback for non-standard tool names.

**Verdict**: a tool named `mcp_trino_query` at the genieCLI skill layer is irrelevant here — `_resolve_query_tool` operates on raw MCP server tool names. Pass 1 covers `query` and `trino_query` (the two most common mcp-trino server conventions). Pass 2 reliably covers anything else with a SQL param. T2/T3 do not need to worry about tool-name mismatch.

---

## Feasibility & biggest risk

**Verdict**: feasible.

All three inputs exist and are already computed before the optimization loop starts in both paths:

- `static_report` — computed at `mcp_trino/research.py:1092` and `trino_query/research.py:677`, both before the baseline
- `plan_cost` output — already called in `trino_query/research.py:375` (long-query path); available for pre-diagnosis
- `table_metadata` — currently fetched POST-loop (line 1387) on MCP path; must be moved earlier or computed lazily

**Biggest risk**: `table_metadata` fetch currently happens AFTER the optimization loop (line 1387), not before it. Moving it pre-loop adds 1-2 MCP round trips (two SQL queries per qualified table) to the hot path before any optimization begins. For tables without fully-qualified names, it silently skips (line 1395) — so `table_metadata` will be `None` or empty for unqualified SQL, making any `OptimizationDirection` that depends on it conditional. The diagnosis module must handle absent metadata gracefully.

Secondary risk: `plan_cost` is currently only called in the --direct long-query path (`trino_query/research.py:375`), not in the standard has-data path. The MCP path calls `run_preflight` (which uses `estimate_from_explain`) but does not expose `raw_plan_json` upstream. Pre-diagnosis will need an explicit `plan_cost` call added to both entry points.

---

## Recommended candidate

```
genie/skills/mcp_trino/pre_execution_diagnosis.py
```

Signature:

```python
from genie.skills.trino_query.sql_static import StaticAnalysisReport
from genie.skills.mcp_trino.research import TableMetadata

def pre_execution_diagnosis(
    sql: str,
    *,
    static_report: StaticAnalysisReport | None,
    explain_cost: tuple[int | None, int | None, object | None],  # (rows_est, bytes_est, plan_json)
    table_metadata: list[TableMetadata] | None,
    peak_memory_bytes: int | None = None,
) -> list[OptimizationDirection]: ...
```

Both callers use:

```python
# mcp_trino/research.py
from .pre_execution_diagnosis import pre_execution_diagnosis

# trino_query/research.py  (lazy, inside function)
from genie.skills.mcp_trino.pre_execution_diagnosis import pre_execution_diagnosis
```
