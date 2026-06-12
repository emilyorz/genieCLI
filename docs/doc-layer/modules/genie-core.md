---
covers:
  - "genie/core/*.py"
last_synced: "df1131522263a60bac2a7a0326499f43bc63c490"
---

## Purpose

`genie/core` is the shared foundation layer for the genieCLI harness. It owns
configuration loading, the provider protocol + data types, skill registry +
plugin discovery, shared context/output-sink protocols, conversation-context
management (token estimation, pruning), SQL text utilities (extraction,
patterns, static lint rules), tool-call parsing, and model capability profiles.
All other packages (`genie.chat`, `genie.skills.*`, `genie.runtime.*`) import
from here; nothing in `core` imports from those packages.

## Exports

### `config.py`
- `load(overrides: dict | None) -> dict` — merge chain: DEFAULTS → legacy
  `~/ai-agent-config.json` → `~/.genie/config.toml` → `GENIE_*` env vars →
  CLI overrides. Returns a flat config dict.
- `save(cfg: dict) -> None` — persist to legacy JSON path.

### `provider.py`
- `Provider` (Protocol) — `name`, `complete(req) -> Iterator[Delta]`,
  `complete_text(req) -> str`, `capabilities() -> ProviderCapabilities`.
- `CompletionRequest` — messages, model, tools, files, reasoning.
- `Delta` — `text`, `finish_reason`.
- `ProviderCapabilities` — streaming, vision, tool_calls flags.

### `registry.py`
- `BaseSkill` — base class with `run`, `validate`, `spec`, `tools`,
  `run_tool`, `contribute_commands`; tier in `"core" | "extended" | "full"`.
- `SkillRegistry` (class-level singleton) — `register`, `get`, `all(tier)`,
  `all_tools`, `run_tool`, `discover(paths)`, `discover_legacy(module)`,
  `clear`, `register_clear_hook`, `register_instructions`,
  `get_instructions`, `all_instructions`.
- `parse_skill_md(path) -> dict` — YAML frontmatter from SKILL.md.
- `parse_skill_md_body(path) -> str` — narrative body after frontmatter.

### `context.py`
- `OutputSink` (Protocol) — `progress`, `result`, `stream`, `error`,
  `table`, `confirm`, `markdown`, `print`, `tool_call`, `tool_result`.
- `SkillContext` — dataclass: `provider`, `output`, `config`, `session`.

### `context_manager.py`
- `ContextManager(model_name)` — tracks token usage; methods:
  `estimate_history_tokens`, `should_prune`, `prune_history`,
  `truncate_tool_result`, `context_status`.

### `model_profiles.py`
- `ModelProfile` — frozen dataclass: name, context_window, max_output,
  skill_tier, supports_vision, supports_tool_calls, chars_per_token.
- `get_profile(model_name) -> ModelProfile` — exact match → pattern
  fallback → conservative default.
- `estimate_tokens(text, model_name) -> int` — rough char/token estimate.

### `tool_call.py`
- `parse_tool_call(text) -> dict | None` — extracts JSON tool call from AI
  response; tolerates markdown fences and trailing commas.
- `normalize_result(result) -> str` — coerce tool result to str.
- `extract_memory(text) -> str` — pull `memory` field from a tool call.

### `sql_extraction.py`
- `extract_sql_from_reply(reply) -> Optional[str]` — extract SQL from AI
  reply via fenced blocks.
- `extract_ctas_inner_select(sql) -> Optional[str]` — strip `CREATE TABLE
  … AS` wrapper, return inner query for read-only optimization.
- `rewrap_ctas_inner_select(original_ctas_sql, new_inner_sql) -> Optional[str]`
  — re-wrap optimized inner query back into original CTAS shell.
- `query_output_columns(sql) -> Optional[tuple]` — static projection names
  (returns None for `SELECT *` or parse failure).
- `queries_structurally_equivalent(sql1, sql2) -> Optional[bool]` — 9-fix
  AST oracle; default-deny sentinel for unmodelled constructs.

### `sql_patterns.py`
- `ORACLE_CONSTRUCTS: list[dict]` — catalog of Oracle→Trino incompatibilities
  with regex patterns, severity, suggestions, and lint rule IDs.
- `get_construct_meta(construct) -> dict | None`
- `get_construct_pattern(construct) -> str | None`
- `compute_confidence(unsupported) -> float`

### `sql_utils.py`
- `strip_comments_and_strings(sql) -> str` — replace comment/string content
  with spaces, preserving newlines and character positions.

### `lint_analyzer.py`
- `LintResult` — findings list + score string + summary.
- `analyze(sql) -> LintResult` — run all lint rules via `lint_rules.ALL_RULES`.

### `lint_rules.py`
- `Finding` — dataclass: severity, line, rule, message, suggestion.
- Per-rule checkers: `check_nvl`, `check_decode`, `check_plus_join`,
  `check_rownum`, `check_sysdate`, `check_select_star`,
  `check_implicit_cross_join`, `check_leading_wildcard_like`,
  `check_count_distinct`, `check_correlated_subquery`,
  `check_missing_partition_filter`.

### `arg.py`
- `Arg` — dataclass: name, type, description, required, default, choices.

## Invariants

- `SkillRegistry` state is class-level (module singleton). `clear()` resets
  all registered skills plus any hooked external state. Tests must call
  `clear()` between runs to avoid cross-test pollution.
- `provider.py` defines only Protocols and dataclasses — no HTTP logic.
  Concrete implementations live in `genie/providers/`.
- `sql_extraction.queries_structurally_equivalent` is default-deny: any
  sqlglot key not in `_MODELLED_KEYS` with a non-falsy value returns `None`
  (gate reverts the rewrite). Never add early-`True` returns.
- `context_manager.py` prune thresholds: `_SYSTEM_RESERVE_RATIO=0.15`,
  `_OUTPUT_RESERVE_RATIO=0.15`, `_PRUNE_TRIGGER_RATIO=0.70`,
  `_TOOL_RESULT_MAX_CHARS=3000`. Changing these affects all providers.
- `config.load` resolution order is strictly: DEFAULTS → JSON legacy → TOML
  → env (`GENIE_*`) → CLI overrides. Callers must not bypass this chain.
- `genie/core` must not import from `genie.skills`, `genie.runtime`,
  `genie.providers`, or `genie.session`. One-way dependency boundary.

## Change log

- df1131522263a60bac2a7a0326499f43bc63c490: initial doc-layer card authored
