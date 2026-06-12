---
last_synced: "572f7ff30399bed1a1a3c230918ba037ae874272"
---

## Overview

genieCLI is a terminal AI assistant specialised in Trino query optimisation and Oracle-to-Trino SQL migration. It wraps multiple LLM backends behind a single `Provider` protocol, exposes Trino-domain capabilities through a plugin `SkillRegistry`, and drives two distinct optimisation loops: an MCP-backed research pipeline (`/trino-research`) and a git-checkpoint autoresearch engine (`/autoresearch`). The interactive REPL and a JSON-output machine mode share the same internals through a switchable `OutputSink`.

The codebase is split into five horizontal layers (core, providers, output, session, runtime) and three domain-skill packages (mcp_trino, trino_query, oracle2trino), all discovered dynamically at startup. Workflow definitions are loaded from Markdown files. The design keeps control flow deterministic in Python code; LLMs are bounded to two roles — rewrite producer and correctness judge — with row-equivalence verification as the objective exit gate.

## Components

- **genie/[direct]** — CLI entry point (`cli.py`/`chat.py`/`input.py`/`setup_wizard.py`): Typer app, provider factory, 15-turn tool loop REPL, readline input, first-run wizard. → [card](modules/genie-[direct].md)
- **genie/core** — Shared foundation: config merge chain, `Provider`/`OutputSink` protocols, `SkillRegistry`/`BaseSkill` plugin contract, `ContextManager` token-budget pruning, SQL lint rules, tool-call parsing, model profiles. → [card](modules/genie-core.md)
- **genie/providers** — LLM backend drivers: `OpenAIProvider` (OpenAI-compatible + Ollama), `AnthropicProvider`, `TGenieProvider`; shared SSE parser. → [card](modules/genie-providers.md)
- **genie/output** — Output sinks: `HumanSink` (Rich terminal) and `MachineSink` (JSON/stderr); duck-typed via `OutputSink` protocol. → [card](modules/genie-output.md)
- **genie/session** — Conversation persistence: session CRUD, JSON files under `sessions/`, message-dict helpers, title slugging. → [card](modules/genie-session.md)
- **genie/runtime** — Autoresearch engine: git-checkpoint iteration loop (`RunManager`), metric extraction, TSV journaling, CLI entry (`autoresearch_cli.py`). → [card](modules/genie-runtime.md)
- **genie/skills/mcp_trino** — `/trino-research` MCP pipeline: JSON-RPC transport, preflight safety gate, static rule classification, pre-execution diagnosis, iterative AI-rewrite loop, five-stage `trino_optimize`, write-statement analysis, dynamic skill registration. → _card deferred — run #4 DEGRADED (budget/invariant), pending fix._
- **genie/skills/trino_query** — Direct Trino skill group: `trino_query`/`trino_explain`/`trino_schema`/`trino_optimize` tools, connection profile management, 10-rule AST static analysis engine (`sql_static/`), detection scan, plan-signature fingerprinting. → _card deferred — run #4 DEGRADED (budget/invariant), pending fix._
- **genie/skills/oracle2trino** — Oracle-to-Trino migration: sqlglot transpilation, Oracle function/type lookup (YAML reference DB), Trino limitations reference, stored-procedure analysis, Trino SQL linter. → [card](modules/genie-skills-oracle2trino.md)
- **genie/skills/[direct]** — Empty `__init__.py` namespace marker making `genie.skills.*` importable. → _card deferred — run #4 DEGRADED (budget/invariant), pending fix._
- **workflows** — Markdown-driven workflow definitions; `WorkflowLoader` discovers, validates requirements, and injects prompts; `autoresearch.md` is the only bundled definition. → _card deferred — run #4 DEGRADED (budget/invariant), pending fix._
- **tests/fixtures** — Static JSON EXPLAIN-plan fixtures covering scan/filter/CTE/aggregate/join plan-node shapes; no live Trino required. → _card deferred — run #4 DEGRADED (budget/invariant), pending fix._
- **tests/[direct-1..4]** — ~80-file pytest suite covering all layers; pure-unit unless `test_trino_integration.py` (skipped when cluster unavailable at `localhost:8085`). → cards: [1](modules/tests-[direct-1].md) [2](modules/tests-[direct-2].md) [3](modules/tests-[direct-3].md) [4](modules/tests-[direct-4].md)

## Data flow

### Interactive REPL (general chat + slash commands)

```
User input (terminal / paste / editor)
    │
    ▼  genie/input.py  _read_input / _read_paste_mode
    │
    ▼  genie/chat.py  _chat_loop
    │
    ├── /trino-research ──► research.run_trino_research_via_mcp  (MCP path)
    │                   └── research.run_trino_research           (--direct path)
    │
    ├── /autoresearch   ──► runtime/autoresearch_cli._run_autoresearch
    │
    └── plain text      ──► chat._do_send
                                │
                                ▼  genie/session  new_msg → append history
                                │
                                ▼  chat._send_with_tools  (max 15 turns)
                                │       │
                                │       ▼  Provider.complete_text
                                │       │
                                │       ├── no tool call → stream to OutputSink
                                │       │
                                │       └── tool call → SkillRegistry.run_tool
                                │               │
                                │               ▼  BaseSkill.run_tool (skill pkg)
                                │               │
                                │               └──► result injected as next turn
                                │
                                ▼  session.save_session
```

### `/trino-research` MCP optimisation loop

```
SQL input
    │
    ▼  preflight.check_read_only  (blocks DML)
    │
    ▼  preflight.build_preflight_decision  (long-query gate, no-data detect)
    │
    ├── NoDataDetected ──► _no_data_path / write_analysis (advisory only)
    │
    └── data path
            │
            ▼  pre_execution_diagnosis  (static + EXPLAIN COST + join + memory)
            │   └─ consumes sql_static rules (10 AST rules, shared rule_ids)
            │
            ▼  rule_gate.build_rule_gate_summary  (BLOCK/REWRITE/ADVISE/PASS)
            │
            ▼  trino_optimize / _run_mcp_plan_cost_loop
            │       │
            │       ├── decompose (LLM fragments query)
            │       ├── optimize  (LLM rewrites each fragment)
            │       ├── recompose (assemble + scan safety gate)
            │       └── verify    (rows_equivalent correctness gate)
            │
            ▼  generate_report  →  OutputSink
```

### Autoresearch iteration loop

```
RunManager.start  (baseline metric + git checkpoint)
    │
    loop
    │
    ▼  LLM proposes hypothesis (via autoresearch_cli)
    │
    ▼  RunManager.step
    │       │
    │       ├── checkpoint_create (git commit)
    │       ├── extract_metric (verify command)
    │       ├── compare_metrics
    │       └── improved? keep : checkpoint_restore (git reset --hard)
    │
    ▼  JournalWriter.write_iteration  (TSV append)
    │
    └── should_continue? → loop : summary
```

## Invariants

- **Config merge order** (lowest → highest): DEFAULTS → JSON legacy → TOML → env vars → CLI overrides; `None` overrides are skipped. (`genie/core/config.py:39-61`)
- **Provider protocol**: all three providers report `tool_calls=False`; tool routing is handled in `chat.py`, not inside providers. (`genie/providers/*/py capabilities()`)
- **SkillRegistry is process-global**: `_skills`, `_clear_hooks`, `_group_instructions` are class-level. A `SkillRegistry.clear()` fires registered hooks; tests must register a reset hook or call `clear()` in teardown. (`registry.py:88-132`)
- **OutputSink contract**: `MachineSink.confirm()` always returns `True`; `MachineSink.progress()` and `tool_result()` are no-ops; `error()` writes to stderr, not stdout. (`output/machine.py`)
- **Session directory**: always `<repo-root>/sessions/`; no config override. (`session/manager.py:10`)
- **`/trino-research` dual entry points**: `run_trino_research_via_mcp` (MCP) and `run_trino_research` (--direct) are sibling entry points; any cross-cutting change (preflight, diagnosis, dispatch) must be wired into both or one path silently no-ops. (`mcp_trino/research.py:2616`, `trino_query/research.py:1474`)
- **DML guard**: `check_read_only()` blocks INSERT/UPDATE/DELETE/MERGE/CREATE/DROP/ALTER/TRUNCATE/RENAME/GRANT/REVOKE/CALL/COMMIT/ROLLBACK before any execution path. (`preflight.py:47`)
- **Correctness gate**: `rows_equivalent()` is the only acceptance criterion for query rewrites; structural similarity alone is not sufficient. (`mcp_trino/research.py:1066`)
- **`RunManager` requires a git repo**: `start()` returns `status="failed"` immediately if `git_is_repo()` is False — it never raises. (`runtime/run_manager.py:84-85`)
- **ContextManager pruning**: triggers at 70% of context window, reserving 15% each for system prompt and output. (`core/context_manager.py:16-18`)
- **Rule-ID contract**: every rule ID in `ALL_RULE_IDS` must appear in both `rule_gate._STATIC_ACTIONS` and `pre_execution_diagnosis._RULE_KIND_MAP`; gaps cause test failure. (`test_rule_id_contract.py:46`)
- **Tool loop guard**: `_send_with_tools` caps at 15 turns and detects identical-action repeats (≥5 in last 20) as a loop guard. (`chat.py:34,141-142`)
- **`lint_analyzer` relative import**: uses a package-relative import `from .lint_rules import ALL_RULES, Finding`; no `sys.path` manipulation required. (`core/lint_analyzer.py:7`)

## Decisions

**D1 — Protocol-based provider abstraction (not inheritance)**
`Provider` and `OutputSink` are `@runtime_checkable` Protocols. This allows new backends to be dropped in without touching `chat.py` or `cli.py`. Rejected: subclass hierarchy would require registering each backend in a factory; protocols keep the factory minimal (`_make_provider` in `cli.py`).

**D2 — `SkillRegistry` as a class-level singleton, not a passed-in container**
Skills are discovered once at startup and shared across all call sites. Dependency-injection via constructor would require threading the registry through ~10 function signatures. Downside: test teardown must call `clear()` explicitly; the registry registers a reset hook to handle this.

**D3 — Dual entry points for `/trino-research` (MCP vs --direct)**
MCP path uses a live MCP server for query execution; `--direct` path uses the `trino` Python driver directly. Both paths share the same preflight, diagnosis, and report logic but keep independent I/O wiring. This separation isolates the MCP dependency while keeping the optimisation logic identical. Any cross-cutting change must be wired into both paths.

**D4 — `rows_equivalent()` as the hard correctness gate, not plan-structure comparison**
Query rewrites are only accepted when result rows match, not when plan signatures match. Plan structure can change without correctness loss (e.g., join reordering); row equivalence is the minimal sufficient check. `trino_optimize.verify()` delegates to `rows_equivalent()` for this reason.

**D5 — Git-checkpoint-based autoresearch (not in-memory state)**
`RunManager` commits each hypothesis before running the verify command and hard-resets on failure. This makes the iteration log auditable, restartable after crash, and independent of Python process lifetime. Rejected: in-memory undo stack would be lost on process exit and couldn't be inspected after the fact.

**D6 — Static SQL analysis split: `genie/core/lint_*` (Oracle residuals) vs `sql_static/` (Trino anti-patterns)**
Oracle-residual lint rules live in `genie/core` and are shared with `oracle2trino`. Trino-specific anti-pattern rules live in `genie/skills/trino_query/sql_static/`. The two sets do not share rule IDs and are not merged at the analysis layer, so each can evolve independently. `LintTrinoSQL` in `oracle2trino` delegates to `genie.core.lint_analyzer` to avoid duplicating rule logic.

**D7 — `OutputSink` duck-typed at startup, not per-call**
The sink (human vs machine) is chosen once in `cli.py` and passed as a constructor argument throughout. Per-call sink selection would require all callers to check `--json` on every output call. Downside: the sink cannot be switched mid-session.

**D8 — Workflow definitions in Markdown, not Python**
`autoresearch.md` ships the agent prompt as a Markdown body with YAML frontmatter for metadata. `WorkflowLoader` discovers, validates, and injects prompts without code changes. New workflows can be added without modifying Python source. Rejected: hard-coding prompts in Python strings would require a code release per prompt change.
