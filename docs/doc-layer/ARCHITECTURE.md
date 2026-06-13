---
last_synced: "dfeabc1a64fb3dcf297942cf39e4cf5ba55f334b"
---

## Overview

GenieCLI is a Trino SQL optimization and Oracle-to-Trino migration CLI that wraps
multiple LLM backends (OpenAI-compatible, Anthropic, Ollama, TGenie) behind a
uniform provider protocol and exposes Trino-specific capabilities through a
filesystem-discovered skill registry. Its primary feature is the `/trino-research`
pipeline: a multi-stage loop that statically analyzes a query, runs pre-flight safety
checks, applies LLM-proposed rewrites iteratively, and verifies result equivalence
before accepting a candidate.

The codebase is organized in five layers — entry (cli/chat/input), core primitives,
provider adapters, skill packages, and the autoresearch runtime — connected by
three stable interfaces: the `Provider` protocol, the `OutputSink` protocol, and
`SkillContext` (a thin DI container). Two parallel entry paths exist for the
`/trino-research` command: an MCP path (`genie/skills/mcp_trino`) that connects to
a running Trino MCP server, and a `--direct` path (`genie/skills/trino_query`) that
connects to Trino directly via the `trino.dbapi` driver.

## Components

- **genie/[direct]** — CLI entry (Typer), interactive REPL and tool-loop (`chat.py`), Tab-completion input, setup wizards; wires all layers without owning them. See [card](modules/genie-[direct].md).
- **genie/core** — Shared foundation: `Provider`/`OutputSink`/`SkillContext` protocols, `SkillRegistry`, `ContextManager`, SQL lint rules, config loader. See [card](modules/genie-core.md).
- **genie/output** — Two `OutputSink` implementations: `HumanSink` (Rich terminal) and `MachineSink` (newline-delimited JSON). See [card](modules/genie-output.md).
- **genie/providers** — Concrete LLM adapters: `OpenAIProvider`, `AnthropicProvider`, `TGenieProvider`, and shared SSE parser. See [card](modules/genie-providers.md).
- **genie/session** — Session CRUD: JSON file persistence, message construction, list/load/save. See [card](modules/genie-session.md).
- **genie/runtime** — Autoresearch iteration engine: `RunManager`, git checkpoint/restore, metric extraction, TSV journal. See [card](modules/genie-runtime.md).
- **genie/skills/[direct]** — Empty package marker enabling sub-package imports. See [card](modules/genie-skills-[direct].md).
- **genie/skills/mcp_trino** — MCP-backed Trino path: JSON-RPC client, preflight, static rule gate, pre-execution diagnosis, iterative rewrite loop, `trino_optimize` five-stage pipeline. See [card](modules/genie-skills-mcp-trino.md).
- **genie/skills/trino_query** — Direct Trino path: `trino.dbapi` connections, `QueryMetrics`, 10-rule sqlglot static analysis, `plan_signature` guard, `/trino-research` entry. See [card](modules/genie-skills-trino-query.md).
- **genie/skills/oracle2trino** — Six Oracle-to-Trino migration skills: transpile, lint, function/type lookup, SP analysis; backed by a bundled YAML function database. _Card deferred — bootstrap pilot closed before this module's card cleared review._
- **workflows** — Markdown-driven workflow definitions loaded by `WorkflowLoader`; `autoresearch.md` is the built-in autonomous iteration workflow. _Card deferred — bootstrap pilot closed before this module's card cleared review._
- **tests/fixtures** — Static JSON Trino EXPLAIN plan trees under `tests/fixtures/explain_plans/` used as deterministic test inputs. _Card deferred — bootstrap pilot closed before this module's card cleared review._
- **tests/[direct-1..4]** — Full test suite (unit + acceptance); no live cluster required except `test_trino_integration.py` which auto-skips. See cards: [1](modules/tests-[direct-1].md) [2](modules/tests-[direct-2].md) [3](modules/tests-[direct-3].md) [4](modules/tests-[direct-4].md).

## Data flow

### Interactive chat turn

```
user input (input.py / prompt_toolkit)
  → cli.callback: select provider + output sink + skills
  → chat._do_send: append user message to session
  → provider.complete() streaming Delta chunks → HumanSink.stream()
  → parse_tool_call() detects tool invocation in response
  → SkillRegistry.run_tool() dispatches to skill.run_tool()
  → tool result appended to session history
  → repeat up to MAX_TOOL_LOOPS=15 per turn
  → session saved to sessions/*.json
```

### /trino-research MCP path

```
chat._parse_trino_research_args
  → build_preflight_decision() → PreflightRoute (6 possible routes)
      STANDARD_LOOP / PLAN_COST_LOOP / NO_DATA / LONG_QUERY_ABORT
      / DIAGNOSE_ONLY / REAL_FAILURE
  → [NO_DATA] → run_write_analysis_only (DML advisory) or static report
  → [STANDARD/PLAN_COST] → pre_execution_diagnosis:
      static rule scan (sql_static 10 rules)
      + join-fact analysis (EXPLAIN LOGICAL plan JSON)
      + memory pressure (per-node limit)
      + partition metadata (table stats via MCP)
      → ranked OptimizationDirection list
  → run_mcp_enhancement: iterative AI rewrite loop
      for each candidate SQL:
        McpClient.call_tool("run_query") → RunMetrics
        rows_equivalent() or plan_signature structural check
        keep candidate if improved, else revert
  → EnhancementReport written to ./report/
```

### /trino-research --direct path

```
run_trino_research (trino_query/research.py)
  → TrinoProfile.connect() → trino.dbapi connection
  → baseline execution → QueryMetrics
  → zero-row baseline → detect_no_data_reason() → no-data path
  → scan_sql (detection_scan) → DetectionFindings
  → analyze (sql_static) → StaticAnalysisReport
  → iterative LLM rewrite loop with structural_equivalent guard
  → result written to ./report/
```

### Autoresearch runtime loop

```
RunManager.start(): record baseline metric via extract_metric()
RunManager.step():
  → checkpoint_create() (git commit snapshot)
  → AI proposes hypothesis + file changes (via autoresearch_cli)
  → extract_metric() runs verify command
  → compare_metrics() → improved / same / worse
  → improved: advance current_best; otherwise checkpoint_restore()
  → JournalWriter.write_iteration() appends TSV row
  → repeat until max_iterations or non-"running" status
```

## Invariants

- `Provider`, `OutputSink`, and `SkillContext` are the only contracts crossing layer boundaries; concrete types must not leak upward — genie/core/context.py, genie/core/provider.py.
- All providers declare `tool_calls=False`; tool dispatch is handled in `chat.py` by parsing model text output, not native function-calling — genie/providers/anthropic.py:37, genie/providers/openai.py:50.
- `SkillRegistry` is a singleton via class-level dict; `clear()` must reset the discovery flag via the registered hook or re-discovery is silently skipped — genie/core/registry.py:88, genie/cli.py:84.
- Config resolution order is fixed and cannot be overridden at runtime: CLI flags > `GENIE_*` env vars > config.toml > config.json > DEFAULTS — genie/core/config.py:3.
- `ContextManager` prunes history at 70 % of available tokens, always preserving the first (system) message and the last 4 messages — genie/core/context_manager.py:18,70.
- Tool results are hard-truncated to 3 000 characters before context accounting — genie/core/context_manager.py:19.
- The tool loop in `chat.py` is bounded to `MAX_TOOL_LOOPS = 15` per turn; loop-detection fires when the same `(tool_name, sorted_args)` key appears ≥5 times in the last 20 actions — genie/chat.py:34,141.
- `check_read_only` in preflight blocks DML/DDL before any execution; only SELECT/WITH/EXPLAIN/SHOW/DESCRIBE/DESC are permitted — genie/skills/mcp_trino/preflight.py:17.
- `rule_gate` classifies static findings into BLOCK/REWRITE/ADVISE/PASS but never rewrites SQL — genie/skills/mcp_trino/rule_gate.py:3.
- `trino_optimize` public functions never raise; all degrade to typed unavailable/unverified outcomes — genie/skills/mcp_trino/trino_optimize.py:7.
- `checkpoint_restore` always resets to `original_head`, not the checkpoint commit — genie/runtime/checkpoint.py:110.
- Guard failure triggers restore before recording the result — genie/runtime/run_manager.py:181.
- Static rule consumer maps (`_STATIC_ACTIONS`, `_RULE_KIND_MAP`) must key on exactly `ALL_RULE_IDS`; any drift is a test failure — tests/test_rule_id_contract.py:46.
- `PreflightRoute` must have exactly six values: DIAGNOSE_ONLY, NO_DATA, REAL_FAILURE, LONG_QUERY_ABORT, PLAN_COST_LOOP, STANDARD_LOOP — genie/skills/mcp_trino/preflight.py (enforced by tests/test_preflight_state_machine_acceptance.py:400).
- Sessions are stored as JSON files under `sessions/` at repo root; `SESSIONS_DIR` is repo-relative and cannot be overridden at runtime — genie/session/manager.py:10.
- MCP skills are only registered when `config.enabled` is True — genie/skills/mcp_trino/__init__.py:117.

## Decisions

**D1 — Provider as duck-typed Protocol, not ABC (current)**
Using `@runtime_checkable Protocol` means providers are duck-typed. This keeps the
core package free of inheritance chains and allows third-party adapters without
subclassing. Alternative (ABC) was rejected because it would force all providers to
import a common base; the duck-typed approach is sufficient given the small, stable
interface (complete/complete_text/capabilities). Source: genie/core/provider.py:30.

**D2 — Tool dispatch via text parsing, not native function-calling**
All providers declare `tool_calls=False`; tool invocations are parsed from model
text output by `parse_tool_call()`. This was chosen to support providers (TGenie,
Ollama) that do not implement the OpenAI function-calling wire protocol. The tradeoff
is brittleness if model output format drifts, but it provides uniform behaviour
across all four adapters. Source: genie/chat.py:123, genie/core/tool_call.py.

**D3 — Two parallel entry paths for /trino-research (MCP vs --direct)**
The MCP path (`mcp_trino`) connects to a Trino MCP server; the `--direct` path
(`trino_query`) uses `trino.dbapi` directly. They share static analysis rule IDs
and preflight logic but differ in execution mechanics. Keeping them parallel allows
each to be developed and tested independently. The dual-path rule-id equivalence
test (`test_dual_path_rule_id_equivalence.py`) enforces that both paths emit the
same non-explain direction tuples for a given SQL.

**D4 — Git-based checkpoint/restore for autoresearch iterations**
`RunManager` snapshots working tree state via git commits and hard-resets to
`original_head` on guard failure. This gives a reliable, auditable revert mechanism
without a custom snapshot format. The tradeoff is that autoresearch only works inside
a git repo (`RunManager.start` fails immediately when not in a repo). Source:
genie/runtime/checkpoint.py, genie/runtime/run_manager.py:85.

**D5 — Preflight decision expressed as a frozen, six-route enum**
`PreflightDecision` is a frozen dataclass carrying a `PreflightRoute` enum (exactly
six values). Freezing prevents accidental mutation across the call chain; the fixed
enum prevents ad-hoc route addition that would bypass existing test coverage.
Enforced by `test_preflight_state_machine_acceptance.py`. Source:
genie/skills/mcp_trino/preflight.py:310,325.

**D6 — Static analysis never raises; rules that fail are silently skipped**
`analyze()` in `sql_static/__init__.py` catches per-rule exceptions and logs at
DEBUG level, returning a partial `StaticAnalysisReport`. This prevents a single
malformed SQL pattern from killing the optimization pipeline. The tradeoff is that
a silent rule skip can mask a rule bug; the rule-id contract test closes this gap
by requiring all rule IDs to appear in the consumer maps. Source:
genie/skills/trino_query/sql_static/__init__.py:131.
