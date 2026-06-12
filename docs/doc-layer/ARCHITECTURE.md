---
last_synced: "df1131522263a60bac2a7a0326499f43bc63c490"
---

## Overview

genieCLI is a terminal-based LLM assistant purpose-built for Trino SQL workflows. Its
primary feature is `/trino-research`: an iterative, evidence-driven SQL optimization
pipeline that combines static AST analysis, EXPLAIN-based cost reading, LLM-generated
rewrites, and correctness verification. The CLI also supports conversational LLM chat
with multi-provider backends, an autonomous `/autoresearch` iteration loop, and an
Oracle-to-Trino SQL migration toolkit.

The system is structured as a strict one-way dependency graph: a shared `core` layer
supplies protocols and utilities; concrete implementations (providers, skills, runtime)
import from `core` but not from each other; entry-point modules (`cli`, `chat`) sit at
the top and wire everything together at call time.

## Components

- **`genie/[direct]`** — Entry-point and REPL layer: Typer CLI (`cli.py`), interactive chat loop and `/command` dispatch (`chat.py`), terminal input with tab completion (`input.py`), first-run setup wizards (`setup_wizard.py`). See [module card](modules/genie-[direct].md).
- **`genie/core`** — Shared foundation: config loading, `Provider`/`OutputSink` protocols, `SkillRegistry`, `ContextManager`, SQL utilities (extraction, static lint rules, patterns), tool-call parsing, model profiles. See [module card](modules/genie-core.md).
- **`genie/output`** — Output abstraction: `HumanSink` (Rich terminal) and `MachineSink` (JSON/stderr) implementing the `OutputSink` protocol. See [module card](modules/genie-output.md).
- **`genie/providers`** — LLM backend adapters: `OpenAIProvider`, `AnthropicProvider`, `TGenieProvider`; shared SSE parser in `base.py`. See [module card](modules/genie-providers.md).
- **`genie/session`** — Conversation-history persistence: JSON-file CRUD over `sessions/*.json`; single authoritative source for session create/load/save/list. See [module card](modules/genie-session.md).
- **`genie/runtime`** — Autonomous iteration loop: git-based checkpoint/restore, shell metric extraction, TSV journal, `RunManager` state machine driving the `/autoresearch` command. See [module card](modules/genie-runtime.md).
- **`genie/skills/[direct]`** — Empty namespace marker (`genie/skills/__init__.py`); sub-package discovery is driven by `SkillRegistry.discover`, not imports here. See [module card](modules/genie-skills-[direct].md).
- **`genie/skills/mcp_trino`** — MCP-Trino integration and `/trino-research` pipeline: MCP client, preflight gating, EXPLAIN cost reader, pre-execution AST diagnosis, rule gate, LLM-driven decompose→optimize→recompose→verify pipeline, write-analysis advisory path, report generation. _Module card deferred — run #3 DEGRADED (fabricated API contracts), pending rewrite._
- **`genie/skills/trino_query`** — Direct Trino execution path: profile-based connection manager, `TrinoQuerySkill`/`TrinoExplainSkill`/`TrinoSchemaSkill`, `detection_scan` (zero-network static scanner), `plan_signature` comparator, `sql_static` 10-rule sqlglot engine, `research.py` (`--direct` optimization loop). See [module card](modules/genie-skills-trino-query.md).
- **`genie/skills/oracle2trino`** — Oracle-to-Trino migration: six registered skills wrapping sqlglot transpilation, YAML function/type DB, construct detection, and lint. See [module card](modules/genie-skills-oracle2trino.md).
- **`workflows`** — Markdown-driven workflow definitions: `WorkflowLoader` discovers and parses `*.md` files with YAML frontmatter; `autoresearch.md` encodes the autonomous iteration protocol for system-prompt injection. See [module card](modules/workflows.md).
- **`tests/[direct]`** — pytest suite covering all subsystems; `conftest.py` supplies `FakeProvider` and `NullSink` stubs. _Module card deferred — run #3 DEGRADED (67-file flat residual), pending rewrite._
- **`tests/fixtures`** — 18 static JSON Trino EXPLAIN plan fixtures under `tests/fixtures/explain_plans/`; used by plan-signature and diagnosis tests. _Module card deferred — run #3 DEGRADED, pending rewrite._

## Data flow

### Interactive chat (normal use)

```
User keystroke
  → input.py (_read_input / _read_paste_mode / _read_editor_mode)
  → chat.py (_chat_loop)
      → /command dispatch (built-in slash commands handled inline)
      → _do_send → _send_with_tools
          → genie/core/context_manager (token estimation, history pruning)
          → genie/session/manager (save session)
          → genie/providers/* (HTTP → LLM → Delta stream)
          → genie/output/* (render to terminal or JSON)
          → tool-call parse (genie/core/tool_call)
              → SkillRegistry.run_tool → BaseSkill.run
              → loop up to MAX_TOOL_LOOPS=15
```

### `/trino-research` (MCP path)

```
chat.py _chat_loop detects /trino-research
  → genie/skills/mcp_trino/research.run_trino_research_via_mcp
      → preflight.check_read_only + plan_cost + build_preflight_decision
          ├─ route=no_data  → write_analysis advisory path → report
          ├─ route=abort    → LongQueryAbort → no-data report
          └─ route=optimize →
              pre_execution_diagnosis (static AST + EXPLAIN cost + join facts + memory)
              → rule_gate.build_rule_gate_summary
              → run_mcp_enhancement (measure baseline → LLM rewrite iterations)
                  → trino_optimize: decompose → optimize → recompose → verify
                  → rows_equivalent correctness gate
              → generate_report → output
```

### `/trino-research` (--direct path)

```
chat.py → genie/skills/trino_query/research.run_trino_research
  → _run_optimization_loop (same shape: preflight → diagnosis → LLM iterations → verify)
  Uses trino_query/connection.py TrinoProfile instead of McpClient
```

### `/autoresearch`

```
chat.py → genie/runtime/autoresearch_cli._run_autoresearch
  → RunManager.start (git validation, baseline metric)
  → RunManager.step loop:
      checkpoint_create → LLM applies hypothesis → extract_metric
      → compare_metrics → keep or checkpoint_restore
      → journal.write_iteration
```

### Config resolution (every startup)

```
genie/core/config.load:
  DEFAULTS → ~/ai-agent-config.json → ~/.genie/config.toml → GENIE_* env → CLI overrides
```

## Invariants

- `genie/core` has no imports from `genie.skills`, `genie.runtime`, `genie.providers`,
  or `genie.session`. All other packages may import from `core`; `core` is leaf.
- All three `Provider` implementations set `tool_calls=False`; tool dispatch is text-
  parsing only, handled in `chat.py`, never delegated to the LLM API.
- `/trino-research` has two symmetric entry paths (MCP default via
  `run_trino_research_via_mcp`; `--direct` opt-in via `run_trino_research`). Any
  cross-cutting change — gate logic, no-data detection, dispatch routing — must be
  wired into both or it silently no-ops on the production path.
- `sql_extraction.queries_structurally_equivalent` is default-deny: unmodelled sqlglot
  AST keys with non-falsy values return `None` (caller reverts the rewrite). No early-
  `True` returns are permitted.
- `SkillRegistry` is a class-level singleton; tests must call `SkillRegistry.clear()`
  between runs to avoid cross-test pollution.
- `write_analysis` must remain import-safe at module load time (no MCP client, Trino
  driver, or config loader imported at top level) so `classify_write_operation` is safe
  to call from the preflight check before any cluster connection exists.
- `trino_optimize` pipeline functions (`decompose`, `optimize`, `recompose`, `verify`)
  accept all cluster/LLM dependencies as injected callables; no direct cluster or LLM
  imports in the module. This keeps the pipeline unit-testable with stubs.
- `mcp_trino/register()` is fail-soft: an unreachable MCP server logs a warning and
  returns; genieCLI continues without MCP tools.
- `RunManager.step()` expects file edits already applied to the working tree before it
  is called; it only commits, measures, and decides keep/revert.
- `detection_scan` and all `sql_static` functions are zero-network; they never require
  a live Trino cluster.
- Config load order is strictly: DEFAULTS → JSON legacy → TOML → env (`GENIE_*`) → CLI
  overrides. No bypass of this chain is permitted.

## Decisions

### D1 — One-way dependency boundary: `core` is leaf

`genie/core` imports nothing from `skills`, `runtime`, `providers`, or `session`.
All concrete implementations import from `core`. This keeps the shared layer testable
in isolation and prevents circular imports. Rejected: placing provider protocols in a
separate package; one-level core is simpler and sufficient.

### D2 — Tool dispatch via text parsing, not LLM API tool_calls

All three providers return `tool_calls=False`. Tool call payloads are embedded in LLM
text replies and parsed by `genie/core/tool_call.parse_tool_call`. Adopted because
TGenie (internal multipart API) does not support the OpenAI function-calling format;
unifying on text parsing avoids a provider-split dispatch path.

### D3 — Dual-path symmetry for `/trino-research` (MCP vs --direct)

The optimization pipeline exists in two parallel forms: MCP default (`mcp_trino/`) and
`--direct` opt-in (`trino_query/research.py`). The paths share the same preflight,
diagnosis, and rule-gate logic via shared imports. The split exists because some
environments have a local Trino JDBC connection but no running MCP server. Rejected:
single path with a connection-type flag — the two transports have different error
semantics and metadata APIs.

### D4 — Preflight read-only gate before any optimization

`preflight.check_read_only` blocks DML/DDL keywords before the optimization loop runs.
Write SQL is routed to `write_analysis` for an advisory-only non-execution path. This
prevents accidental execution of mutating queries during iteration. `write_analysis` is
kept import-safe (no cluster imports at load time) to allow early classification.

### D5 — `trino_optimize` pipeline uses injected callables, no direct imports

`decompose`, `optimize`, `recompose`, `verify` receive `ExplainRunner`, `QueryRunner`,
`LlmFn`, `CostReaderFn` as parameters. No direct MCP client or provider imports in the
module. Adopted to make the pipeline independently unit-testable with stub closures,
and to decouple the five-stage logic from transport concerns.

### D6 — `queries_structurally_equivalent` is default-deny

Any sqlglot AST key not in `_MODELLED_KEYS` with a non-falsy value returns `None`
(caller reverts). This is a safety gate: an unmodelled AST construct in the rewritten
SQL triggers revert rather than accepting a potentially semantics-altering change.
Rejected: default-allow with explicit deny list — the deny list cannot be exhaustive.

### D7 — Session storage as flat JSON files under `sessions/`

No database; each session is a self-contained JSON file. `SESSIONS_DIR` is resolved
relative to `manager.py` at import time. Chosen for simplicity, portability, and easy
inspection. Trade-off: concurrent writes to the same session file are not safe (no
locking). Acceptable for single-user interactive use.

### D8 — `SkillRegistry` as class-level singleton

Registry state is shared across all instances. Skills discovered in one code path are
visible to all callers in the same process. Test isolation requires `clear()` between
runs; the `register_clear_hook` mechanism lets external state (e.g., `cli.py`'s
discovery flag) reset alongside the registry. Rejected: instance-level registry — would
require threading the registry through every call site.
