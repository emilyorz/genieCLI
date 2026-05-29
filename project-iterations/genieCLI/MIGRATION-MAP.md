# Skill Architecture Migration Map

> Generated: 2026-04-13 | Round 1 deliverable for TASK-LEDGER-v8

## 1. Current Architecture (skill.toml + **init**.py)

### Discovery Flow

```
cli.py _discover_skills()
  → SkillRegistry.discover([Path("genie/skills")])
    → scan subdirs for skill.toml + __init__.py
      → importlib.import_module("genie.skills.{name}")
        → mod.register(SkillRegistry)
```

### File Layout (per skill)

```
genie/skills/{name}/
├── __init__.py    # BaseSkill subclasses + register() function
└── skill.toml     # Metadata: name, version, description, group, requires
```

### Metadata in skill.toml

| Field           | Example                           | Used By                 |
| --------------- | --------------------------------- | ----------------------- |
| name            | "file_ops"                        | Display / grouping      |
| version         | "1.0.0"                           | Informational only      |
| description     | "File read/write/list operations" | CLI help                |
| group           | "file"                            | CLI grouping            |
| requires.python | ["sqlglot"]                       | Not enforced at runtime |
| requires.system | ["Chrome with..."]                | Not enforced at runtime |

### Bundled Skills Inventory (8 skills, 62+ tools)

| Skill        | Tools                                                                                                | Tier     | Group        | Dependencies             |
| ------------ | ---------------------------------------------------------------------------------------------------- | -------- | ------------ | ------------------------ |
| file_ops     | read_file, write_file, list_files, file_patch                                                        | core     | file         | none                     |
| shell_ops    | command_run                                                                                          | core     | shell        | none                     |
| git_ops      | git_status, git_diff, git_log, git_checkpoint_create, git_checkpoint_restore                         | core     | git          | none                     |
| browser      | 30 tools (browser\_\*)                                                                               | core     | browser      | websocket-client         |
| trino_query  | trino_query, trino_explain, trino_schema, trino_optimize                                             | core     | trino_query  | trino                    |
| trino_linter | trino_linter                                                                                         | core     | trino_linter | sqlglot                  |
| oracle2trino | transpile_sql, lookup_oracle_function, lookup_oracle_type, list_trino_limitations, analyze_oracle_sp | core     | oracle2trino | sqlglot, pyyaml          |
| deepwiki     | deepwiki_generate, deepwiki_export, deepwiki_status                                                  | extended | analysis     | none (requests optional) |

### Key Runtime Contracts

- `BaseSkill` subclass with `name`, `description`, `group`, `tier`, `args`, `run()`
- `Arg(name, type, description, required, default, choices)` dataclass
- `SkillRegistry` singleton: register/get/all/run_tool/discover
- Tier filtering: core < extended < full
- `SkillContext(provider, output, config, session)` passed to run_tool

## 2. Target Architecture (Anthropic-style SKILL.md)

### Anthropic Pattern

```
skills/{name}/
├── SKILL.md        # YAML frontmatter + narrative instructions
├── scripts/        # Executable code (optional)
├── references/     # Deep docs loaded on demand (optional)
├── examples/       # Templates (optional)
└── assets/         # Static files (optional)
```

### SKILL.md Frontmatter

```yaml
---
name: skill-name
description: >-
  When to use. What it does. Trigger conditions.
  What NOT to trigger on.
license: optional
compatibility: optional
---
```

### Key Difference

Anthropic skills are _instructional documents_ — Claude reads them and follows narrative instructions. genieCLI skills are _programmatic tools_ — Python classes with `run()` methods that execute deterministic logic. **We must bridge this gap.**

## 3. Migration Design Decisions

### D1: SKILL.md replaces skill.toml as the metadata source

- Frontmatter carries: name, description, version, group, tier, requires
- Body carries: rich documentation, usage examples, trigger guidance
- **skill.toml is deleted** after migration (not kept as dual source)

### D2: **init**.py stays as the execution layer

- BaseSkill subclasses, Arg schemas, run() methods — unchanged
- register() function — unchanged
- The code that _does work_ doesn't need to change format

### D3: Discovery switches from "skill.toml required" to "SKILL.md required"

- `SkillRegistry.discover()` looks for `SKILL.md` instead of `skill.toml`
- `__init__.py` still required (it's the Python execution layer)
- Backward compat: during Round 2 only, accept either file as marker

### D4: Extended frontmatter schema for genieCLI

```yaml
---
name: file-ops
description: >-
  File read/write/list operations. Use for any local filesystem interaction.
version: 1.0.0
group: file
tier: core
requires:
  python: []
  system: []
---
```

### D5: Directory layout adds optional subdirs

```
genie/skills/{name}/
├── SKILL.md           # NEW — metadata + docs (replaces skill.toml)
├── __init__.py        # KEPT — BaseSkill classes + register()
├── references/        # NEW optional — deep docs
├── scripts/           # NEW optional — helper scripts
└── (other .py files)  # KEPT — existing helper modules
```

### D6: Tool names and CLI behavior are preserved

- All 62+ tool names stay exactly the same
- `genie tools` output unchanged (reads from BaseSkill.spec())
- `--skill-dir` flag unchanged
- Tier filtering unchanged

## 4. File Change Inventory

### Must Modify

| File                             | Change                                                                                                        |
| -------------------------------- | ------------------------------------------------------------------------------------------------------------- |
| `genie/core/registry.py`         | `discover()` → look for SKILL.md instead of skill.toml; add `_parse_skill_md()` helper to extract frontmatter |
| `genie/skills/*/skill.toml` (x8) | DELETE after SKILL.md created                                                                                 |
| `genie/skills/*/ (x8)`           | ADD SKILL.md with migrated metadata + enriched docs                                                           |
| `tests/test_registry.py`         | Update discovery tests to use SKILL.md marker                                                                 |

### May Modify

| File                         | Change                                    |
| ---------------------------- | ----------------------------------------- |
| `genie/cli.py`               | Only if help text references "skill.toml" |
| `tests/test_skill_tiers.py`  | Only if tier tests reference discovery    |
| `tests/test_cli_coverage.py` | Only if CLI tests reference skill.toml    |

### No Change Needed

| File                              | Reason                      |
| --------------------------------- | --------------------------- |
| `genie/core/arg.py`               | Arg dataclass unchanged     |
| `genie/core/context.py`           | SkillContext unchanged      |
| `genie/skills/*/__init__.py` (x8) | BaseSkill classes unchanged |
| All tool-specific tests           | Tool behavior unchanged     |

## 5. Migration Sequence

| Round         | What                                                                            | Verify                                                                      |
| ------------- | ------------------------------------------------------------------------------- | --------------------------------------------------------------------------- |
| **R1** (this) | Audit + migration map                                                           | This document exists and is reviewed                                        |
| **R2**        | Implement SKILL.md loader in registry.py; accept both markers during transition | `discover()` loads skills via SKILL.md; existing tests pass                 |
| **R3**        | Create SKILL.md for all 8 skills; delete skill.toml files                       | All 8 skills have SKILL.md; no skill.toml remains; all tools still register |
| **R4**        | Update docs, help text, CLI refs, tests                                         | No mention of skill.toml in code/docs; tests reference SKILL.md             |
| **R5**        | Full test suite; update STATUS.md + ledger                                      | All tests green; ledger closed with evidence                                |

## 6. Risks & Assumptions

| #   | Risk/Assumption                                                                            | Mitigation                                                                               |
| --- | ------------------------------------------------------------------------------------------ | ---------------------------------------------------------------------------------------- |
| A1  | SKILL.md frontmatter parsing needs a YAML parser — project may not have pyyaml as core dep | Check; if not, use a minimal frontmatter parser (regex-based) for the 4-5 fields we need |
| A2  | Some skills have extra .py files beyond **init**.py (browser has many)                     | These stay untouched — only the marker file changes                                      |
| A3  | External skill dirs (--skill-dir) must also work with SKILL.md                             | discover() already iterates any path; just need to check for SKILL.md there too          |
| A4  | Legacy discovery path (discover_legacy) is orthogonal                                      | No change needed — it imports by module name, not by marker file                         |
| R1  | If we break discovery, all tools disappear silently                                        | Round 2 keeps dual-marker support; Round 3 cuts over only after verification             |
| R2  | skill.toml `requires` field is not enforced — should SKILL.md enforce it?                  | No. Keep it informational. Don't add new runtime enforcement in a format migration.      |
