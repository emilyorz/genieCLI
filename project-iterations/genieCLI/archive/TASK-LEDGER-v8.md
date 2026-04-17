# TASK-LEDGER

## Basic Info

- Project: genieCLI skill-architecture migration
- Repo Folder: project-iterations/genieCLI/
- Naming note: this is a formal long-running workflow, not an "experiments" sandbox.
- Iteration: 8
- Owner: Emily (tmux emily-claude)
- Status: complete
- Last Updated: 2026-04-13T07:02+08:00
- Current Focus: adopt Anthropic-style SKILL.md skills in genieCLI and switch the bundled skills over without breaking existing tool behavior.

## Goal

- One-line summary:
  Migrate genieCLI from the current skill.toml + __init__.py pattern to an Anthropic-style skills architecture built around SKILL.md, and move the bundled skills onto the new format.
- Done when:
  1. SKILL.md-based discovery/loading works end-to-end in genieCLI;
  2. bundled skills are migrated or intentionally bridged with clear coverage;
  3. CLI/help/tests/docs reflect the new architecture;
  4. the relevant test suite passes and the handoff files reflect the final state.

## Carryover

- Current skill system already has a central registry, tiering, and legacy discovery in genie/core/registry.py + genie/cli.py.
- Anthropic reference repo is available at /tmp/anthropic-skills and https://github.com/anthropics/skills.
- Existing bundled skills currently live under genie/skills/* with skill.toml + __init__.py pairs.
- Preserve current tool names and user-facing behavior while switching the underlying skill format.

## Todo

| ID | Status | Pri | Task | Owner | Note |
|----|--------|-----|------|-------|------|
| T1 | done | P0 | Round 1: audit current genieCLI skill loading plus Anthropic skills repo, then write a concrete migration map and file inventory | Emily | MIGRATION-MAP.md written; 8 skills inventoried, 6 design decisions, file change list |
| T2 | done | P0 | Round 2: implement SKILL.md loading/discovery and any bridging needed for progressive disclosure / bundled resources | Emily | discover() accepts SKILL.md or skill.toml; parse_skill_md() added; 3 new tests; 557 pass |
| T3 | done | P0 | Round 3: migrate the bundled skills to the new format and move any shared resources into references/scripts/assets where appropriate | Emily | 8 SKILL.md created, 8 skill.toml deleted; 53 tools load via SKILL.md; 101 tests pass |
| T4 | done | P1 | Round 4: switch docs, help text, CLI listing, and regression tests to the new architecture; clean up stale legacy assumptions | Emily | architecture.md updated; registry docstring updated; no stale skill.toml refs in code |
| T5 | done | P0 | Round 5: verify with targeted tests and the full relevant suite, then update STATUS.md and the ledger handoff | Emily | 557 pass (same baseline); STATUS.md + ledger updated; migration complete |

## Verify

- Evidence checked: 2026-04-13
- Source of evidence: pytest full suite + manual discovery verification
- Verification result: PASS
  - 557/577 tests pass (20 failures are pre-existing trino linter/integration — same as before migration)
  - 53 tools discovered from 8 SKILL.md-based skill packages
  - 0 skill.toml files remaining
  - All SKILL.md files parse with valid YAML frontmatter
  - docs/wiki/architecture.md updated to reference SKILL.md

## Blocked

- None yet

## Reports

### Ledger setup — 2026-04-13T07:02+08:00

- Result: Created v8 ledger and locked scope to the Anthropic-style skill migration.
- Decision: accept

### Round 1 — 2026-04-13

- Result: Completed audit of both systems and wrote MIGRATION-MAP.md
- Evidence: MIGRATION-MAP.md in project-iterations/genieCLI/ — contains full inventory (8 skills, 62+ tools), 6 design decisions (D1-D6), file change inventory, risk register
- Key findings:
  - pyyaml is already a core dep — can use `yaml.safe_load()` for SKILL.md frontmatter
  - Only registry.py `discover()` needs code change (line 157-170); BaseSkill/Arg/SkillContext untouched
  - All 8 skills keep their __init__.py + register() unchanged; only the marker file changes
- Decision: accept — proceed to Round 2

### Round 2 — 2026-04-13

- Result: Implemented dual-marker discovery and SKILL.md frontmatter parser
- Changes:
  - `genie/core/registry.py`: discover() accepts SKILL.md or skill.toml; added parse_skill_md() utility
  - `tests/test_registry.py`: 3 new tests (parse valid, parse no-frontmatter, discover with SKILL.md)
- Evidence: 14/14 registry tests pass; 557/577 full suite pass (20 failures are pre-existing trino/linter issues)
- Decision: accept — proceed to Round 3

### Round 3 — 2026-04-13

- Result: All 8 bundled skills migrated from skill.toml to SKILL.md
- Changes:
  - Created SKILL.md for: file_ops, shell_ops, git_ops, browser, trino_query, trino_linter, oracle2trino, deepwiki
  - Deleted all 8 skill.toml files
  - Each SKILL.md has YAML frontmatter (name, description, version, group, tier, requires) + markdown body
- Evidence:
  - `find genie/skills -name "skill.toml"` → 0 results
  - `find genie/skills -name "SKILL.md"` → 8 results
  - Python discovery loads 53 tools from SKILL.md markers
  - 101/101 skill-related tests pass
- Decision: accept — proceed to Round 4

### Round 4 — 2026-04-13

- Result: Updated docs and cleaned up stale references
- Changes:
  - `docs/wiki/architecture.md`: Updated discovery description to reference SKILL.md
  - `genie/core/registry.py`: Updated discover() docstring
  - Retro section: marked legacy issues as resolved
- Evidence: 38/38 core tests pass; `grep skill.toml` in source code only finds backward-compat fallback in registry.py (intentional) and historical notes in workflow-notes/
- Decision: accept — proceed to Round 5

### Round 5 — 2026-04-13

- Result: Full verification passed; STATUS.md and ledger closed out
- Evidence:
  - Full test suite: 557 pass / 20 fail (pre-existing) / 1 error (pre-existing) — identical to pre-migration baseline
  - Discovery: 53 tools from 8 SKILL.md packages, 0 skill.toml remaining
  - Multi-file verification: re-read registry.py, 2 SKILL.md files, architecture.md — all consistent
  - STATUS.md updated with v8 summary
- Decision: accept — v8 complete

## Retro

- Worked: current repo already has a clean plugin registry, so the migration has a sane anchor instead of a full rewrite.
- Failed: (resolved) legacy skill.toml discovery was the active path — now replaced by SKILL.md-based discovery.
- Change next: (resolved) migrated in 5 rounds; old path kept only as fallback in discover().

## Next Step

- Next action: v8 complete. Pick next scope from STATUS.md or address pre-existing trino test failures.
- Next owner: Emily (tmux emily-claude)

## Archive / Handoff

- If this iteration is archived, create or update STATUS.md in the same fixed repo folder.
- STATUS.md should say: last iteration, carryover status, archived ledger path, retro follow-ups, and which iteration record(s) the agent should read next.
- Never move the workflow to a different folder mid-stream.
- Keep every iteration record; STATUS.md is the single entrypoint.
