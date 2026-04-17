# TASK-LEDGER

## Basic Info

- Project: genieCLI browser skill tuning
- Repo Folder: project-iterations/genieCLI/
- Naming note: this is a formal long-running workflow, not an "experiments" sandbox.
- Iteration: 9
- Owner: Emily (tmux emily-claude)
- Status: complete
- Last Updated: 2026-04-13T07:51+08:00
- Current Focus: make the browser skill easier and safer for Gemini Flash 2.5, because it currently guesses wrong too often.

## Goal

- One-line summary:
  Retune the browser skill so Gemini Flash 2.5 follows a clearer browser workflow, picks the right tools more reliably, and stops making avoidable navigation / element-selection mistakes.
- Done when:
  1. browser skill guidance is rewritten around a strict, model-friendly workflow;
  2. any tool grouping / tiering adjustments needed for Gemini Flash 2.5 are implemented;
  3. docs/tests reflect the new browser guidance;
  4. verification shows the revised browser skill is clearer and the handoff files are updated.

## Carryover

- Browser is the largest skill package and currently has 30 tools across navigation, reading, interaction, visual, and context groups.
- Gemini Flash 2.5 is mapped to the core skill tier, so it only sees the core browser subset by default.
- Current browser SKILL.md is very short; it does not yet spell out the step-by-step workflow enough for a weaker model.
- Likely failure mode: model jumps to the wrong tool or skips a necessary read/confirm step before interaction.
- Existing browser implementation lives in genie/skills/browser/tools.py and related helpers; keep tool names stable unless a change is clearly worth the blast radius.

## Todo

| ID | Status | Pri | Task | Owner | Note |
|----|--------|-----|------|-------|------|
| T1 | done | P0 | Round 1: audit the current browser skill, Gemini Flash 2.5 profile, and the likely failure modes; write a concrete tuning plan | Emily | Root cause: tier split gives Flash snapshot but not click_element/type_element |
| T2 | done | P0 | Round 2: rewrite browser SKILL.md into a stricter, model-friendly browser workflow with explicit ordering and guardrails | Emily | 4-step LOOK→PICK→ACT→VERIFY cycle; tool selection table; common patterns |
| T3 | done | P0 | Round 3: apply any browser tool grouping / tiering / wording changes needed to reduce Flash 2.5 confusion | Emily | Swapped core tier: click/type→extended, click_element/type_element→core; 38 tests pass |
| T4 | done | P1 | Round 4: add or adjust regression tests/docs for the browser skill so the new guidance stays stable | Emily | 3 new coherence tests in test_skill_tiers.py; architecture.md already correct |
| T5 | done | P0 | Round 5: verify the result on the relevant suite and update STATUS.md / ledger handoff | Emily | 560 pass (3 new); coherence check pass; STATUS.md + ledger updated |

## Verify

- Evidence checked: 2026-04-13
- Source of evidence: pytest full suite + manual coherence verification
- Verification result: PASS
  - 560/580 tests pass (20 pre-existing trino failures, same baseline)
  - 3 new browser coherence tests: snapshot+click_element+type_element are core; click+type are not core; exactly 10 core browser tools
  - Flash 2.5 core browser toolset: list_tabs, switch_tab, navigate, get_url, get_text, snapshot, click_element, type_element, scroll, screenshot
  - SKILL.md: 4-step mandatory workflow (LOOK→PICK→ACT→VERIFY), tool selection table, common patterns

## Blocked

- None yet

## Reports

### Ledger setup — 2026-04-13T07:51+08:00

- Result: Created v9 ledger and locked scope to the browser skill tuning problem.
- Decision: accept

### Round 2 — 2026-04-13

- Result: Rewrote browser SKILL.md from 24 lines to a full workflow guide
- Changes: 4-step mandatory cycle (LOOK→PICK→ACT→VERIFY); tool selection table showing ID-based over CSS-selector; common patterns for navigate+interact and form-fill
- Decision: accept

### Round 3 — 2026-04-13

- Result: Swapped tier assignments to make Flash 2.5 core toolset coherent
- Changes in tools.py:
  - `browser_click`: core → extended (CSS selector is the advanced path)
  - `browser_type`: core → extended (CSS selector is the advanced path)
  - `browser_click_element`: extended → core (ID-based, pairs with snapshot)
  - `browser_type_element`: extended → core (ID-based, pairs with snapshot)
  - Updated descriptions to nudge toward preferred tools
- Net effect: same 10 core tools, but now snapshot→click_element→type_element pipeline works end-to-end
- Decision: accept

### Round 4 — 2026-04-13

- Result: Added 3 regression tests for browser core tier coherence
- Changes in test_skill_tiers.py:
  - test_snapshot_workflow_tools_are_core: verifies snapshot + click_element + type_element are core
  - test_raw_css_tools_are_not_core: verifies click + type are NOT core
  - test_core_browser_count: verifies exactly 10 core browser tools
- Decision: accept

### Round 5 — 2026-04-13

- Result: Full verification passed; STATUS.md and ledger closed out
- Evidence: 560 pass / 20 fail (pre-existing) / 1 error (pre-existing); coherence check pass
- Decision: accept — v9 complete

### Round 1 — 2026-04-13

- Result: Found the root cause of Flash 2.5 browser failures.
- Root cause analysis:
  1. **Incoherent tier split** — `browser_snapshot` is core (Flash sees it), but `browser_click_element` and `browser_type_element` are extended (Flash can't use them). Flash gets numbered element IDs from snapshot but has no tool to act on them. It tries raw CSS selectors via `browser_click`/`browser_type`, which are a different paradigm → wrong-element guesses.
  2. **No workflow guidance** — SKILL.md is 24 lines. Flash has zero instruction on operation order. It jumps straight to `browser_click` without inspecting the page first.
  3. **Duplicate interaction paths** — Flash sees `browser_click` (CSS selector) and `browser_snapshot` (numbered IDs) but not the bridge (`browser_click_element`). Two competing paradigms, no guidance on which to use.
  4. **Description overlap** — `browser_get_text` and `browser_snapshot` both read page content. Flash picks the wrong one.
- Tuning plan:
  - **R2**: Rewrite SKILL.md with explicit 4-step workflow (snapshot → pick ID → click_element/type_element → verify)
  - **R3**: Promote `browser_click_element` and `browser_type_element` to tier=core so Flash can actually use them. Demote raw `browser_click`/`browser_type` to extended (advanced users). Consider demoting `browser_get_text` to extended (snapshot is the primary read tool).
  - **R4**: Add test that verifies core tier gives Flash a coherent toolset
  - **R5**: Verify + close
- Flash 2.5 core tools (current, 10 tools): list_tabs, switch_tab, navigate, get_url, get_text, click, scroll, type, screenshot, snapshot
- Flash 2.5 core tools (proposed, 10 tools): list_tabs, switch_tab, navigate, get_url, snapshot, click_element, type_element, scroll, screenshot, get_text (demoted) → replaced by snapshot as primary
- Decision: accept — proceed to Round 2

## Retro

- Worked: the browser package already has explicit tool names, so the fix should mostly be about guidance and surfacing, not renaming everything.
- Failed: the current browser skill is too terse for a weaker model like Gemini Flash 2.5, so it leaves too much room for bad guesses.
- Change next: make the browser workflow explicit and boring; boring is good here, because browsers punish improvisation.

## Next Step

- Next action: v9 complete. Pick next scope from STATUS.md, or do a live E2E test of Flash 2.5 + browser to validate the workflow in practice.
- Next owner: Emily (tmux emily-claude)

## Archive / Handoff

- If this iteration is archived, create or update STATUS.md in the same fixed repo folder.
- STATUS.md should say: last iteration, carryover status, archived ledger path, retro follow-ups, and which iteration record(s) the agent should read next.
- Never move the workflow to a different folder mid-stream.
- Keep every iteration record; STATUS.md is the single entrypoint.
