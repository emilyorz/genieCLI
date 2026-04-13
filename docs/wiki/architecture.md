# GenieCLI Architecture Wiki

> Generated: 2026-04-13 | Version: 5.0.0

## Overview

GenieCLI is an AI-powered Trino query tuning CLI. It supports multiple LLM backends (TGenie, OpenAI-compatible, Anthropic) and provides Trino-focused skill/tool capabilities via a plugin registry. Designed for interactive SQL optimization, Oracle-to-Trino migration, and autonomous research loops.

## Architecture Layers

```
┌─────────────────────────────────────────────────┐
│                    CLI Layer                      │
│  cli.py (Typer) — entry point, routing, config   │
│  setup_wizard.py — interactive config wizard     │
├─────────────────────────────────────────────────┤
│                  Chat Engine                      │
│  chat.py — REPL, tool loop, session management   │
├──────────────┬──────────────┬───────────────────┤
│   Providers  │   Skills     │    Runtime         │
│  tgenie.py   │  trino_query/│  autoresearch/     │
│  openai.py   │  mcp_trino/  │  checkpoint.py     │
│  anthropic.py│  oracle2trino│  run_manager.py     │
│              │  trino_linter│  eval_loop.py       │
│              │  file_ops/   │                     │
│              │  git_ops/    │                     │
│              │  shell_ops/  │                     │
├──────────────┴──────────────┴───────────────────┤
│                  Core (Engine)                    │
│  registry.py    — SkillRegistry + BaseSkill       │
│  provider.py    — Provider Protocol               │
│  context.py     — SkillContext (DI container)     │
│  config.py      — Config merge chain              │
│  sql_patterns.py— Shared Oracle construct catalog │
│  sql_utils.py   — SQL text utilities              │
│  model_profiles.py — Model capabilities           │
│  context_manager.py — Token tracking + pruning    │
├─────────────────────────────────────────────────┤
│                Output Layer                       │
│  human.py  — Rich terminal (HumanSink)            │
│  machine.py— JSON output (MachineSink)            │
├─────────────────────────────────────────────────┤
│               Session Layer                       │
│  manager.py — Session CRUD, JSON persistence      │
└─────────────────────────────────────────────────┘
```

## Key Components

### 1. Entry Point (`cli.py`)

- **Typer CLI** with `callback()` as the default command handler
- `setup` subcommand for interactive config wizard (LLM / Trino / MCP)
- Resolves config via merge chain: DEFAULTS → JSON → TOML → env → CLI flags
- Discovers skills from `genie/skills/` subdirectories (dynamic, filesystem-based)
- Builds system prompt with tool definitions filtered by model capability tier

### 2. Chat Engine (`chat.py`)

- **`_chat_loop`**: Interactive REPL with slash commands (`/trino`, `/trino-research`, `/autoresearch`, etc.)
- **`_send_with_tools`**: Tool execution loop (max 15 iterations)
  - Sends conversation history to LLM
  - Parses JSON tool calls from response
  - Dispatches via `SkillRegistry.run_tool()`
  - Detects action loops (same action repeated 5+ times)
  - **Context management**: Prunes history when approaching model limits
- **`_do_send`**: Single-turn orchestration (append user message → tool loop → save)

### 3. Provider System (`core/provider.py`)

Protocol-based abstraction with three implementations:

| Provider  | File           | Features                                                                  |
| --------- | -------------- | ------------------------------------------------------------------------- |
| TGenie    | `tgenie.py`    | Internal gateway, multipart/form-data, SSE streaming, auto token refresh  |
| OpenAI    | `openai.py`    | OpenAI/Groq/Ollama/LM Studio, SSE streaming, Ollama native mode          |
| Anthropic | `anthropic.py` | Anthropic wire format, system prompt extraction, vision support           |

### 4. Skill Registry (`core/registry.py`)

- **Singleton pattern** with class-level `_skills` dict
- **Discovery**: Scans directories for `SKILL.md` + `__init__.py` pairs
- **Tier system**: Skills have `tier` attribute (`core`/`extended`/`full`) for model-aware loading
- **Dispatch**: `run_tool(name, args, ctx)` with validation + error handling

### 5. Skills (~20 tools across 7 packages)

| Package          | Tools   | Description                          |
| ---------------- | ------- | ------------------------------------ |
| **trino_query**  | 4       | Query execution + EXPLAIN + optimize |
| **mcp_trino**    | dynamic | MCP client + autoresearch via MCP    |
| **oracle2trino** | 5       | Oracle → Trino SQL transpilation     |
| **trino_linter** | 1       | SQL static analysis (11 rules)       |
| **file_ops**     | 4       | File read/write/patch/list           |
| **git_ops**      | 5       | Git status/diff/log/checkpoint       |
| **shell_ops**    | 1       | Whitelisted shell commands           |

### 6. Shared SQL Catalog (`core/sql_patterns.py`)

Oracle construct catalog shared between `oracle2trino` and `trino_linter`:
- 20 Oracle constructs (SQL residuals + PL/SQL blocks)
- Pattern-based detection (regex)
- Confidence scoring for conversion quality
- Eliminates cross-skill imports

### 7. Autoresearch Runtime (`runtime/`)

Autonomous iteration engine for query optimization:
- `run_manager.py` — Iteration state machine (propose → verify → commit/revert)
- `checkpoint.py` — Git-based state checkpointing
- `journal.py` — TSV iteration journaling with metrics
- `metric.py` — Metric extraction + trend comparison

## Data Flow

```
User Input
    │
    ▼
_chat_loop (REPL)
    │
    ├── /trino → Trino connection manager
    ├── /trino-research → SQL optimization loop
    ├── /autoresearch → General iteration loop
    │
    ├── Regular input → _do_send()
    │       │
    │       ▼
    │   _send_with_tools() ◄──────┐
    │       │                      │
    │       ▼                      │
    │   Provider.complete_text()   │
    │       │                      │
    │       ├── No tool call → out │
    │       ├── Tool call found    │
    │       │       │              │
    │       │   SkillRegistry ─────┘
    │       │   .run_tool()
    │       ▼
    │   Save session + display
    ▼
 Next input
```

## Configuration

### Setup Wizard

```bash
genie setup          # LLM backend
genie setup trino    # Trino connection
genie setup mcp      # MCP Trino server
```

### Config Merge Chain (highest → lowest)

1. CLI flags
2. Environment variables (`GENIE_*`)
3. TOML (`~/.genie/config.toml`)
4. JSON legacy (`~/ai-agent-config.json`)
5. Defaults

## Changes in v5.0.0

1. **Removed** browser/ (30 tools) and deepwiki/ (3 tools) — not related to Trino tuning
2. **Added** `genie setup` interactive wizard for LLM/Trino/MCP configuration
3. **Moved** shared Oracle patterns to `core/sql_patterns.py` (clean core/skills boundary)
4. **Removed** websocket-client dependency (was only used by browser)
5. **Refocused** README and CLI branding on Trino query tuning
