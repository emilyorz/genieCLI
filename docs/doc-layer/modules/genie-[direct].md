---
covers:
  - "genie/__init__.py"
  - "genie/__main__.py"
  - "genie/chat.py"
  - "genie/cli.py"
  - "genie/input.py"
  - "genie/setup_wizard.py"
last_synced: "dfeabc1a64fb3dcf297942cf39e4cf5ba55f334b"
---

## Purpose

This module is the user-facing entry layer of GenieCLI. It owns the Typer CLI
(`cli.py`), the interactive REPL and tool-execution pipeline (`chat.py`), terminal
input with Tab-completion (`input.py`), and the interactive setup wizards for LLM,
Trino, and MCP connections (`setup_wizard.py`). `__main__.py` is the `python -m
genie` entry point delegating to `cli.main`. `__init__.py` is an empty package
marker. The layer wires together providers, skills, sessions, and output sinks
without owning any of those subsystems directly.

## Exports

> See exports file: /Users/leeabc/work/emilyorz/genieCLI/docs/doc-layer/exports/genie--direct-.md

<!-- No tracked symbols in exports file — no annotations added. -->

## Invariants

- `__main__.py` delegates to `cli.main` with no logic of its own — `genie/__main__.py:1` — `from genie.cli import main`
- `chat.py` must NOT import from `genie.cli` to avoid circular imports; `build_prompt` is injected as a callable — `genie/chat.py:10` — `Does NOT import from genie.cli — receives build_prompt as a callable to`
- Tool loop is bounded to `MAX_TOOL_LOOPS = 15` iterations per turn — `genie/chat.py:34` — `MAX_TOOL_LOOPS = 15`
- Loop-detection fires when the same `(tool_name, sorted_args)` key appears 5 or more times in the last 20 actions — `genie/chat.py:141` — `if recent_actions.count(action_key) >= 5:`
- `_skills_discovered` is a module-level flag; `SkillRegistry.clear()` must call `_reset_discovery_flag` via a registered hook or re-discovery is silently skipped — `genie/cli.py:84` — `SkillRegistry.register_clear_hook(_reset_discovery_flag)`
- `cli.callback` selects `MachineSink` when stdout is NOT a TTY or `--json` is passed; `HumanSink` is used only when stdout IS a TTY and `--json` is absent — `genie/cli.py:183` — `output: HumanSink | MachineSink = MachineSink() if (json_output or not is_tty) else HumanSink()`
- `input.py` maintains a single `_ps` PromptSession singleton reused across calls; paste mode uses a separate isolated session to prevent state leakage — `genie/input.py:15` — `_ps = None`
- `setup_wizard._write_toml` merges new keys into any existing `~/.genie/config.toml` rather than overwriting it, preserving keys not touched by the wizard — `genie/setup_wizard.py:57` — `existing.update(data)`
- `verify` is a backwards-compatible alias for `doctor` with no independent logic — `genie/cli.py:432` — `"""Alias for \`genie doctor\` (kept for backwards compatibility)."""`
- `_cmd_config` excludes `authToken`, `openaiApiKey`, and `cookies` entirely from machine output; in human output only `authToken`, `openaiApiKey`, and `customHeader` are masked to first-4/last-4 (`cookies` is not present in `secret_keys` for human output) — `genie/cli.py:269` — `safe = {k: v for k, v in cfg.items() if k not in ("authToken", "openaiApiKey", "cookies")}`

## Change log

- dfeabc1a64fb3dcf297942cf39e4cf5ba55f334b: initial card created for genie/[direct] entry layer
