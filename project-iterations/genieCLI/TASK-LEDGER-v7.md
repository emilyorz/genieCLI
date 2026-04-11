# TASK-LEDGER

## Basic Info

- Project: genieCLI harness and UX iteration
- Repo Folder: project-iterations/genieCLI/
- Naming note: this is a formal long-running workflow, not an "experiments" sandbox.
- Iteration: 7
- Owner: Main Agent
- Status: complete
- Last Updated: 2026-04-12T01:00+08:00
- Current Focus: Conversation branching — /branch shipped, 575 tests green.

## Goal

- One-line summary:
  Add a `/branch` command (or equivalent) so users can fork conversation history at any prior exchange, enabling multi-step recovery without clobbering the redo stack.
- Done when:
  1. `/branch` (or chosen command) is implemented end-to-end;
  2. targeted tests pass with observable evidence;
  3. STATUS.md and this ledger reflect the final state.

## Carryover

- v6 retro follow-ups considered:
  - Conversation branching — carried from v5 and v6; the natural next step after /undo + /redo.
  - Any other UX or harness improvement surfaced by usage — deprioritised; branching is the clear winner.

## Scope Decision

- Locked: `/branch <exchange-index>` — fork history at any prior real user exchange
- Rationale: /undo + /redo give single-step recovery. Branching lets users jump to any earlier state without iterating through /undo. Carried through two retros without landing; now shipped.
- Deferred: no other UX changes this iteration.

## Todo

| ID  | Status | Pri | Task                                                                                                                                   | Owner      | Note                                                                                                      |
| --- | ------ | --- | -------------------------------------------------------------------------------------------------------------------------------------- | ---------- | --------------------------------------------------------------------------------------------------------- |
| T1  | done   | P0  | Lock scope, create v7 ledger, update STATUS.md                                                                                         | Main Agent | Done                                                                                                      |
| T2  | done   | P1  | Inspect current history/undo/redo code paths in genie/chat.py and genie/session/manager.py; design the branch data model               | Main Agent | Design: real-user-index slice + redo clear; /history updated with #N labels                               |
| T3  | done   | P1  | Implement `/branch` end-to-end (history slice, redo clear, autocomplete hint, /help entry)                                             | Main Agent | genie/chat.py: /branch handler + /history #N labels; genie/input.py: SLASH_COMMANDS + SLASH_COMMAND_HINTS |
| T4  | done   | P1  | Add tests for branching behaviour (happy path, edge: index 0, beyond range, last exchange, redo interaction, tool-result invisibility) | Main Agent | 16 new tests in tests/test_branch_command.py; +2 assertions in test_new_commands.py                       |
| T5  | done   | P0  | Verify with full pytest suite; update ledger and STATUS.md for handoff                                                                 | Main Agent | 575 passed, 0 failed                                                                                      |

## Verify

- Evidence checked: yes
- Source of evidence: `.venv/bin/pytest -q` full suite run
- Evidence package:
  - Test output: `575 passed in 1.43s`
  - Diff summary: 4 files changed — genie/chat.py (/branch handler, /history #N numbering, /help entry), genie/input.py (/branch in SLASH_COMMANDS + SLASH_COMMAND_HINTS), tests/test_branch_command.py (16 new branch tests), tests/test_new_commands.py (/branch in completeness check)
  - Artifact paths: genie/chat.py, genie/input.py, tests/test_branch_command.py
- Verification result: pass — all 575 tests green, no regressions (+15 net new tests vs v6 baseline)

## Blocked

- None

## Reports

### Ledger setup — 2026-04-12T00:00+08:00

- Result: Created v7 ledger; scope locked to conversation branching.
- Decision: accept

### /branch implementation — 2026-04-12T01:00+08:00

- Result: /branch shipped end-to-end. Key design decisions:
  - "Real user exchange" defined as: `role == "user"` and text does not start with `[Tool result:` — this excludes internal tool round-trips from the exchange counter, so users never have to count invisible pipeline messages
  - Cut point is `real_user_indices[n]` — the start of the (n+1)th real user message — so all tool calls and results belonging to exchange n survive the branch
  - Redo stack is cleared on branch (user is on a new path; stale redos are misleading); but NOT cleared on no-op branch (already-at-last-exchange case)
  - `/history` updated: real user messages now render as `[#N You]` so users know which index to pass to `/branch`
  - One initial test had a wrong assertion about tool-result survival; caught immediately by pytest and corrected before any commit
- Decision: accept

## Transition Log

- 2026-04-12T00:00+08:00 — PLAN — v7 initialized, scope locked.
- 2026-04-12T01:00+08:00 — COMPLETE — /branch shipped, 575 tests green.

## Retro

- Worked: clean index model (only real user turns count); redo-clear-on-branch avoids confusing state; `/history` #N labels make the feature self-discoverable
- Failed: one test assertion about tool-result survival was wrong (expected tool result to vanish, but it belongs to the branched-at exchange) — caught by pytest before commit
- Change next: if branching becomes heavy use, could consider named branches or a branch stack; for now the current model is complete enough

## Next Step

- Next action: commit this iteration; start v8 ledger when ready for the next scope.
- Next owner: Main Agent

## Archive / Handoff

- If this iteration is archived, create or update STATUS.md in the same fixed repo folder.
- STATUS.md should say: last iteration, carryover status, archived ledger path, retro follow-ups, and which iteration record(s) the agent should read next.
- Never move the workflow to a different folder mid-stream.
- Keep every iteration record; STATUS.md is the single entrypoint.
