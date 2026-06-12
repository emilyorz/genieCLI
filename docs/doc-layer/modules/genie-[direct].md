---
covers:
  - "genie/__init__.py"
  - "genie/__main__.py"
  - "genie/chat.py"
  - "genie/cli.py"
  - "genie/input.py"
  - "genie/setup_wizard.py"
last_synced: "df1131522263a60bac2a7a0326499f43bc63c490"
---

## Purpose

Entry-point and interactive shell layer for GenieCLI. `cli.py` owns the
Typer app, provider factory, skill discovery, and system-prompt assembly.
`chat.py` owns the interactive REPL and the tool-execution loop. `input.py`
owns terminal input (readline, paste, editor modes). `setup_wizard.py`
provides interactive wizards for LLM, Trino, and MCP configuration.
`__main__.py` is the `python -m genie` entry point; `__init__.py` is empty.

## Exports

**cli.py**
- `app` — Typer application instance; used by `main()` and tests
- `main() -> None` — package entry point; calls `app()`
- `__version__: str` — current version string (`"5.0.0"`)
- `_make_provider(cfg, debug) -> Provider` — factory: dispatches to
  `OpenAIProvider`, `AnthropicProvider`, or `TGenieProvider` based on
  `cfg["interface"]`
- `_build_system_prompt(cfg, use_skills, model) -> str` — assembles the
  system prompt; injects tool-call JSON schema and SKILL.md instructions
  when `use_skills=True`; filters skills by model capability tier
- `_discover_skills(skill_dirs, legacy) -> None` — one-shot discovery;
  guarded by `_skills_discovered` flag; reset via SkillRegistry clear hook
- `callback(...)` — Typer root callback; routes to interactive chat,
  file/stdin non-interactive, or sub-commands (sessions/config/tools/doctor/setup)
- `setup(target) -> None` — `genie setup [llm|trino|mcp|check]` command
- `doctor() -> None` — preflight: Python version, PATH, trino driver,
  sqlglot, LLM provider, Trino connection, MCP reachability
- `verify() -> None` — alias for `doctor()` (backwards compat)

**chat.py**
- `_chat_loop(provider, cfg, model, reasoning, use_skills, output, build_prompt) -> None`
  — interactive REPL; handles all `/command` dispatch and delegates plain
  text to `_do_send`
- `_do_send(provider, session, model, reasoning, user_input, output, ctx) -> None`
  — single user-turn: appends message, calls `_send_with_tools`, saves session
- `_send_with_tools(provider, session, model, reasoning, output, ctx) -> str | None`
  — tool loop up to `MAX_TOOL_LOOPS=15`; handles screenshot results and
  loop-detection (same tool+args repeated ≥5 times in last 20 actions)
- `_parse_trino_research_args(args) -> tuple[dict, bool]` — parses
  `/trino-research` flags; returns `(kwargs, force_direct)`
- `_try_run_trino_write_analysis_from_file(...) -> bool` — short-circuits
  `/trino-research --file` when the SQL is a write operation
- `_render_banner(output, version) -> None` — ASCII banner, TTY only
- `_list_models`, `_validate_model` — Ollama model listing and validation

**input.py**
- `_read_input(prompt_str) -> str` — readline input via shared
  `PromptSession` singleton; returns `"/exit"` on EOF
- `_read_paste_mode() -> str` — isolated multiline session; Ctrl-D sends
- `_read_editor_mode() -> str` — opens `$EDITOR` (fallback vim/nano) with
  a temp `.sql` file; returns file content on save
- `SLASH_COMMANDS: list[str]` — canonical list of all `/commands` for Tab
  completion
- `_build_completer()` — `prompt_toolkit` Completer for slash commands,
  subcommand hints, and registered skill names

**setup_wizard.py**
- `setup_llm() -> None` — interactive wizard for Ollama/OpenAI/Groq/TGenie/
  Anthropic; writes `~/.genie/config.toml`
- `setup_trino() -> None` — interactive Trino profile wizard; writes
  `~/.config/genie/trino.json`
- `setup_mcp() -> None` — MCP URL/timeout wizard; writes
  `~/.config/genie/mcp.json`
- `setup_check() -> None` — connectivity check for LLM, Trino, and MCP

## Invariants

- `cli.py` must not import from `chat.py` at module level; the import
  `from genie.chat import _chat_loop` happens inside the callback body to
  avoid a circular dependency (`chat.py` imports from `genie.cli` only for
  `__version__`).
- `_skills_discovered` is a module-level flag; `SkillRegistry.clear()` must
  call `_reset_discovery_flag()` (registered as a clear hook) or subsequent
  test runs will skip rediscovery.
- `_send_with_tools` loop limit is `MAX_TOOL_LOOPS=15`; callers must not
  assume the loop always completes — it returns `None` on max-loops exhaustion.
- `/trino-research` in `_chat_loop` reads `current_reasoning` (mutable in
  the REPL via `/reasoning`), not the outer `reasoning` closure variable.
- `_read_input` is backed by a module-level singleton `_ps`; calling it
  before the PromptSession is warmed up creates it lazily — safe for tests
  that never call it.
- `setup_wizard.py` uses `input()` directly (not `_read_input`); it is
  intended for first-run interactive use only, not the REPL.
- `__init__.py` is intentionally empty; do not add imports — it is the
  package marker only.

## Change log

- df1131522263a60bac2a7a0326499f43bc63c490: initial doc-layer card created
