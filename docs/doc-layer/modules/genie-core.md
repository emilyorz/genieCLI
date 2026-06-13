---
covers:
  - "genie/core/*.py"
last_synced: "dfeabc1a64fb3dcf297942cf39e4cf5ba55f334b"
---

## Purpose

`genie/core` is the shared foundation for the entire genieCLI harness. It owns five
distinct concerns: (1) LLM provider abstraction (`provider.py`, `context.py`);
(2) skill plugin system — base class, registry, and filesystem discovery
(`registry.py`, `arg.py`); (3) context-window budget management and history pruning
(`context_manager.py`, `model_profiles.py`); (4) Trino SQL static analysis — lint
rules, pattern catalog, and extraction utilities (`lint_rules.py`, `lint_analyzer.py`,
`sql_patterns.py`, `sql_extraction.py`, `sql_utils.py`); and (5) configuration loading
with a four-layer merge chain (`config.py`). Nothing outside this package may bypass
these primitives — all skills, CLI commands, and provider adapters depend on the
abstractions defined here.

## Exports

> See exports file: /Users/leeabc/work/emilyorz/genieCLI/docs/doc-layer/exports/genie-core.md

- SkillRegistry: Singleton class-level dict; all registration is class-method based.
- BaseSkill: Superclass every skill must extend; enforces validate/run contract.
- Provider: `@runtime_checkable` Protocol — duck-typed, not subclassed.
- OutputSink: `@runtime_checkable` Protocol for all UI output channels.
- SkillContext: Thin dataclass coupling provider, output sink, config, and session.
- ContextManager: Prunes history at 70 % of context window; keeps last 4 messages.
- analyze: Entry point for SQL lint; returns a `LintResult` with score and findings.
- load: Config merge — CLI flags > env (`GENIE_*`) > config.toml > config.json > DEFAULTS.

## Invariants

- `SkillRegistry` is a singleton via class-level dict — `clear()` must be called between tests or state leaks across test runs — registry.py:88 — `_skills: dict[str, BaseSkill] = {}`
- `Provider` is a `@runtime_checkable Protocol`, not an ABC — implementors are duck-typed — provider.py:30 — `@runtime_checkable`
- Config resolution order is fixed: CLI > env > TOML > JSON > DEFAULTS — config.py:4 — `CLI flags (injected by caller)  >  env vars  >  config.toml  >  config.json  >  DEFAULTS`
- `ContextManager` starts pruning at 70 % of available history tokens — context_manager.py:18 — `_PRUNE_TRIGGER_RATIO = 0.70   # start pruning at 70% of context window`
- Tool results are hard-truncated at 3 000 characters per entry before further processing — context_manager.py:19 — `_TOOL_RESULT_MAX_CHARS = 3000  # truncate individual tool results`
- `prune_history` always preserves the first message (system) and the last 4 messages — context_manager.py:71 — `# 2. Always keep the last 4 messages (recent context)`
- `BaseSkill.tier` must be one of `"core"`, `"extended"`, or `"full"`; unknown values are treated as `"core"` by `SkillRegistry.all()` — registry.py:26 — `tier: str = "core"  # "core" | "extended" | "full"`
- Oracle residual lint rules resolve patterns from the shared catalog; a missing catalog entry raises `LookupError` (not a silent pass) — lint_rules.py:44 — `raise LookupError(`
- `discover()` requires both `__init__.py` AND either `SKILL.md` or `skill.toml` to be present; a directory with only `__init__.py` is silently skipped — registry.py:190 — `if not init_file.exists():`
- `ModelProfile` is frozen — all capability data is immutable after construction — model_profiles.py:16 — `@dataclass(frozen=True)`

## Change log

- dfeabc1a64fb3dcf297942cf39e4cf5ba55f334b: initial card created
