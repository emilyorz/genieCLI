# TASK-LEDGER

## Basic Info

- Project: genieCLI v18 — Anthropic-style SKILL.md body injection
- Repo Folder: project-iterations/genieCLI/
- Iteration: 18 (completes the v8 half-done Anthropic migration)
- Owner: Emily (Claude Code)
- Status: done
- Updated: 2026-04-16T13:00+0800
- Focus: Make SKILL.md body AI-visible so skill maintainers can tune
  behavior by editing markdown, not Python.

## Context (what was broken)

v8 (2026-04-13) migrated skill format from `skill.toml` → `SKILL.md`,
but `parse_skill_md()` only extracted YAML frontmatter. The markdown
body was discarded — never reached the AI. Tuning optimization
prompts required editing Python code, defeating the whole point of
Anthropic-style skills.

Confirmed in this session: user wrote detailed Trino best-practice
notes in `mcp_trino/SKILL.md` body; AI never saw them.

## Goal

- One-line summary:
  SKILL.md body becomes the single source of truth for AI-visible
  skill instructions. Editing markdown is enough to tune behavior.
- Done when:
  1. `parse_skill_md_body()` extracts body text; ✅
  2. `SkillRegistry` stores per-group instructions; ✅
  3. Chat mode `_build_system_prompt` injects active group instructions; ✅
  4. `/trino-research` sys_prompt pulls `mcp_trino` body verbatim; ✅
  5. `mcp_trino/SKILL.md` expanded with Trino optimization guide; ✅
  6. Hardcoded Trino rules removed from `research.py` (moved to markdown); ✅
  7. Tests: body parsing, registry storage, empty-body handling; ✅
  8. 591 tests pass; ✅

## Changes

| File                                 | Change                                                                                                                                                                                                                                                                 |
| ------------------------------------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `genie/core/registry.py`             | Add `parse_skill_md_body()`; extend `SkillRegistry` with `_group_instructions`, `register_instructions()`, `get_instructions()`, `all_instructions()`; `_load_skill_package` auto-attaches SKILL.md body to registry keyed by dir name; `clear()` resets instructions. |
| `genie/cli.py`                       | `_build_system_prompt` collects active group instructions and appends a `## SKILL INSTRUCTIONS` section to the system prompt.                                                                                                                                          |
| `genie/skills/mcp_trino/research.py` | `run_mcp_enhancement` reads `SkillRegistry.get_instructions("mcp_trino")` and injects it as a `## Trino Optimization Guide` section. Hardcoded best-practices list removed.                                                                                            |
| `genie/skills/mcp_trino/SKILL.md`    | Expanded body: optimization priorities (6 ordered principles), anti-pattern rewrite table (9 rewrites), metric-specific wins, per-iteration response format.                                                                                                           |
| `tests/test_registry.py`             | 4 new tests covering body extraction and registry-level instructions.                                                                                                                                                                                                  |

## Verification

- 591 tests pass (up from 587; 4 new tests added)
- Runtime smoke test: `SkillRegistry.get_instructions("mcp_trino")`
  returns the 3555-char body with "Optimization Priorities" and the
  NVL→COALESCE anti-pattern table

## How maintainers tune from now on

1. Edit `genie/skills/<group>/SKILL.md` (markdown body below `---` fence)
2. The body is loaded at startup by `_load_skill_package()` and attached
   to the registry under the directory name (= group name).
3. For chat mode: active groups' instructions are inlined into the
   system prompt under `## SKILL INSTRUCTIONS`.
4. For `/trino-research`: the `mcp_trino` body is inlined into the
   optimization session's sys_prompt under `## Trino Optimization Guide`.
5. No Python changes needed.

## Retro

- **Worked:** Scoped change — 2 small utility additions (parse body,
  register_instructions) plus 2 injection sites. Everything else is
  content in SKILL.md. Good separation.
- **Failed:** Should have caught this gap during v8 migration. The
  "Anthropic-style" label was applied to a half-done change.
- **Change next:**
  - Review and enrich other SKILL.md bodies (trino_query,
    oracle2trino, trino_linter) with domain knowledge so the chat-mode
    system prompt becomes actually useful
  - Consider token budget: if all groups' bodies are inlined, the
    system prompt could grow large for chat mode. Currently the tier
    filter limits which groups are active; may need per-group length
    caps or on-demand loading later.
  - v15 R3 (tests + docs for /trino-research MCP requirement) still
    pending — can close out after Sam confirms tuning via SKILL.md
    edits works end-to-end.
