# GenieCLI Architecture Wiki

> Generated: 2026-04-09 | Version: 4.1.0

## Overview

GenieCLI is a plugin-based AI agent CLI that supports multiple LLM backends (TGenie, OpenAI, Anthropic, Ollama) and provides extensible skill/tool capabilities via a registry pattern. It's designed for interactive chat with tool use, file-based queries, and autonomous research loops.

## Architecture Layers

```
┌─────────────────────────────────────────────────┐
│                    CLI Layer                      │
│  cli.py (Typer) — entry point, routing, config   │
├─────────────────────────────────────────────────┤
│                  Chat Engine                      │
│  chat.py — REPL, tool loop, session management   │
├──────────────┬──────────────┬───────────────────┤
│   Providers  │   Skills     │    Runtime         │
│  tgenie.py   │  browser/    │  autoresearch/     │
│  openai.py   │  file_ops/   │  checkpoint.py     │
│  anthropic.py│  git_ops/    │  eval_loop.py      │
│              │  shell_ops/  │  run_manager.py     │
│              │  oracle2trino│                     │
│              │  trino_*/    │                     │
│              │  deepwiki/   │                     │
├──────────────┴──────────────┴───────────────────┤
│                  Core Layer                       │
│  registry.py — SkillRegistry + BaseSkill          │
│  provider.py — Provider Protocol + CompletionReq  │
│  context.py  — SkillContext (DI container)        │
│  config.py   — Config merge chain                 │
│  model_profiles.py — Model capabilities           │
│  context_manager.py — Token tracking + pruning    │
│  arg.py      — Arg descriptor                     │
│  tool_call.py— JSON tool call parsing             │
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
- Resolves config via merge chain: DEFAULTS → JSON → TOML → env → CLI flags
- Discovers skills from `genie/skills/` subdirectories
- Builds system prompt with tool definitions filtered by model capability tier
- Routes to: interactive chat, file input, stdin pipe, or subcommands

### 2. Chat Engine (`chat.py`)

- **`_chat_loop`**: Interactive REPL with slash commands (`/new`, `/clear`, `/context`, `/skills`, etc.)
- **`_send_with_tools`**: Tool execution loop (max 15 iterations)
  - Sends conversation history to LLM
  - Parses JSON tool calls from response
  - Dispatches via `SkillRegistry.run_tool()`
  - Handles screenshots with vision model support
  - Detects action loops (same action repeated 5+ times)
  - **Context management**: Prunes history when approaching model limits
- **`_do_send`**: Single-turn orchestration (append user message → tool loop → save)

### 3. Provider System (`core/provider.py`)

Protocol-based abstraction with three implementations:

| Provider | File | Features |
|----------|------|----------|
| TGenie | `tgenie.py` | Internal gateway, multipart/form-data, SSE streaming, auto token refresh |
| OpenAI | `openai.py` | OpenAI/Groq/Ollama/LM Studio, SSE streaming, Ollama native mode detection |
| Anthropic | `anthropic.py` | Anthropic wire format, system prompt extraction, vision support |

### 4. Skill Registry (`core/registry.py`)

- **Singleton pattern** with class-level `_skills` dict
- **Discovery**: Scans directories for `skill.toml` + `__init__.py` pairs
- **Tier system**: Skills have `tier` attribute (`core`/`extended`/`full`) for model-aware loading
- **Dispatch**: `run_tool(name, args, ctx)` with validation + error handling
- **Clear hooks**: External state (e.g., discovery flags) stays in sync

### 5. Skills (46 tools across 8 packages)

| Package | Tools | Tier Mix | Description |
|---------|-------|----------|-------------|
| **browser** | 30 | 10 core, 14 extended, 6 full | Chrome CDP automation |
| **file_ops** | 4 | core | File read/write/patch/list |
| **git_ops** | 5 | core | Git status/diff/commit/log/branch |
| **shell_ops** | 1 | core | Whitelisted shell commands |
| **oracle2trino** | 5 | extended | SQL transpilation tools |
| **trino_linter** | 1 | extended | SQL analysis |
| **trino_query** | 2 | extended | Query execution + optimization |
| **deepwiki** | 3 | extended | Wiki documentation generation |

### 6. Model Profiles (`core/model_profiles.py`)

Maps model names to capability profiles:
- **Context window size** — determines when to prune
- **Skill tier** — controls which tools are loaded
- **Vision/tool call support** — capability flags
- Pattern-based fallback for unknown models

### 7. Context Manager (`core/context_manager.py`)

Prevents context overflow on resource-constrained models:
- **Token estimation** from character count (model-specific ratio)
- **Pruning trigger** at 70% of available context
- **Pruning strategy**: Keep system prompt + last 4 messages, summarize middle
- **Tool result truncation** at 3000 chars
- **Diagnostics** via `/context` command

### 8. Config System (`core/config.py`)

Merge chain (highest → lowest priority):
1. CLI flags
2. Environment variables (`GENIE_*`)
3. TOML (`~/.genie/config.toml`)
4. JSON legacy (`~/ai-agent-config.json`)
5. Defaults

### 9. Autoresearch Runtime (`runtime/`)

Autonomous iteration engine:
- `autoresearch_cli.py` — CLI interface for `/autoresearch` command
- `run_manager.py` — Run lifecycle management
- `eval_loop.py` — Evaluation loop with metric tracking
- `checkpoint.py` — State checkpointing
- `journal.py` — Iteration journaling

## Data Flow

```
User Input
    │
    ▼
_chat_loop (REPL)
    │
    ├── Slash command? → handle locally
    │
    ├── Regular input → _do_send()
    │       │
    │       ▼
    │   Append to session history
    │       │
    │       ▼
    │   _send_with_tools() ◄──────────┐
    │       │                          │
    │       ├── Context prune check    │
    │       │                          │
    │       ▼                          │
    │   Provider.complete_text()       │
    │       │                          │
    │       ▼                          │
    │   Parse response                 │
    │       │                          │
    │       ├── No tool call → return  │
    │       │                          │
    │       ├── Tool call found        │
    │       │       │                  │
    │       │       ▼                  │
    │       │   SkillRegistry.run_tool │
    │       │       │                  │
    │       │       ▼                  │
    │       │   Truncate result        │
    │       │       │                  │
    │       │       ▼                  │
    │       │   Append to history ─────┘
    │       │
    │       ▼
    │   Save session
    │       │
    │       ▼
    │   Display response
    │
    ▼
 Next input
```

## File Map

| Path | Purpose | Lines |
|------|---------|-------|
| `genie/__main__.py` | Entry point | 4 |
| `genie/cli.py` | CLI routing + config | ~300 |
| `genie/chat.py` | Chat REPL + tool loop | ~650 |
| `genie/core/registry.py` | Skill registry | ~195 |
| `genie/core/provider.py` | Provider protocol | ~42 |
| `genie/core/context.py` | SkillContext DI | ~30 |
| `genie/core/config.py` | Config loading | ~125 |
| `genie/core/model_profiles.py` | Model capabilities | ~90 |
| `genie/core/context_manager.py` | Context management | ~150 |
| `genie/core/arg.py` | Arg descriptor | ~16 |
| `genie/core/tool_call.py` | Tool call parsing | ~30 |
| `genie/skills/browser/` | Browser automation | ~1100 |
| `genie/skills/file_ops/` | File operations | ~200 |
| `genie/skills/git_ops/` | Git operations | ~250 |
| `genie/skills/shell_ops/` | Shell commands | ~105 |
| `genie/skills/deepwiki/` | Wiki generation | ~200 |
| `genie/output/human.py` | Rich terminal output | ~100 |
| `genie/output/machine.py` | JSON output | ~50 |
| `genie/session/manager.py` | Session persistence | ~50 |

## Configuration Reference

| Key | Default | Description |
|-----|---------|-------------|
| `interface` | `tgenie` | Provider: tgenie/openai/anthropic |
| `defaultModel` | `gemini-2.5-flash` | Default model name |
| `systemPrompt` | "You are a helpful AI assistant." | Base system prompt |
| `openaiApiKey` | "" | API key for OpenAI-compatible providers |
| `openaiBaseUrl` | `https://api.openai.com/v1` | API base URL |

## Recent Changes (v4.1.0)

1. **Skills default to ON** — `--skills` flag now defaults to `True`
2. **Skill tier system** — `core`/`extended`/`full` tiers filter tools by model capability
3. **Model profiles** — Context window, tier, and capability mapping per model
4. **Context management** — Token estimation, auto-pruning, tool result truncation
5. **DeepWiki skill** — Wiki documentation generation integration
6. **Browser tier classification** — 10 core, 14 extended, 6 full tools
