---
covers:
  - "genie/__init__.py"
  - "genie/__main__.py"
  - "genie/chat.py"
  - "genie/cli.py"
  - "genie/input.py"
  - "genie/setup_wizard.py"
last_synced: "572f7ff30399bed1a1a3c230918ba037ae874272"
---

## Purpose

Top-level package files that form the CLI entry point, interactive chat REPL, terminal input handling, and first-run setup wizards. `cli.py` owns the Typer app and provider factory; `chat.py` owns the tool-loop REPL; `input.py` owns readline/paste/editor input; `setup_wizard.py` owns interactive configuration; `__main__.py` bridges `python -m genie` to `main()`; `__init__.py` is an empty package marker.

## Exports

# No tracked files found for module 'genie/[direct]'

## Invariants

- `__init__.py` is intentionally empty — the package exposes no public symbols at import time (`genie/__init__.py` line 1, single blank line).
- `__main__.py` delegates unconditionally to `genie.cli.main` — no logic lives here (`genie/__main__.py:1-4`).
- `cli.py` defines `__version__ = "5.0.0"` at module scope (`cli.py:31`); `chat.py` imports it as `from genie.cli import __version__` (`chat.py:445`) — version is the single source of truth in `cli.py`.
- `chat.py` does NOT import from `genie.cli` at module level — the `build_prompt` callable is passed in to avoid circular imports (`chat.py` module docstring, lines 1-13).
- Tool loop in `_send_with_tools` caps at `MAX_TOOL_LOOPS = 15` (`chat.py:34`) and detects identical-action repeats (≥5 occurrences in last 20) as a loop guard (`chat.py:141-142`).
- `_send_with_tools` terminates on `"tool": null` in AI reply or on missing `tool` key — both treated as "no more tools" (`chat.py:128-131`).
- `input.py` maintains a single `_ps` prompt_toolkit `PromptSession` singleton (`input.py:15`); `_read_paste_mode` creates an isolated session to prevent state leakage (`input.py:188-208`).
- `input.py` defines the canonical `SLASH_COMMANDS` list (`input.py:19-43`); adding a slash command to `chat.py` without updating this list silently omits it from Tab completion.
- `_make_provider` in `cli.py` dispatches on `cfg["interface"]`: `"openai"` → `OpenAIProvider`, `"anthropic"` → `AnthropicProvider`, anything else → `TGenieProvider` (`cli.py:51-65`).
- `_discover_skills` is idempotent via a module-level `_skills_discovered` flag (`cli.py:70`); the flag is reset by a `SkillRegistry.clear()` hook registered at import time (`cli.py:84`), so test teardown resets discovery correctly.
- `setup_wizard.py` writes LLM config to `~/.genie/config.toml`, Trino profiles to `~/.config/genie/trino.json`, and MCP config to `~/.config/genie/mcp.json` (`setup_wizard.py:13-15`); these paths are hardcoded constants, not derived from `genie.core.config`.
- `_read_editor_mode` falls back through `$EDITOR` → `vim` → `nano` and prints an error if none is found; it always deletes the temp file in a `finally` block (`input.py:211-242`).

## Change log

- 572f7ff30399bed1a1a3c230918ba037ae874272: initial doc-layer card created
