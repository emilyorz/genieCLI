# TASK-LEDGER

## Basic Info

- Project: genieCLI model switching & optimization
- Iteration: 2
- Owner: Main Agent
- Status: archived
- Last Updated: 2026-04-10T08:30+08:00
- Current Focus: T1 done, dispatching T2

## Goal

- One-line summary: Let users switch models mid-conversation and list available models
- Done when: `/model <name>` switches the active model, `/model list` shows available options, provider is recreated on switch, existing session is preserved

## Carryover

(none — v1 was a different sprint)

## Todo

| ID | Status | Pri | Task | Owner | Note |
|----|--------|-----|------|-------|------|
| T1 | done | P0 | Upgrade /model to support runtime switching | sub-agent | 3 modes work, Ollama listing verified |
| T2 | done | P0 | Add Ollama model listing via /api/tags | sub-agent | Delivered inside T1's _list_models() |
| T3 | done | P1 | Add tests for /model switching and model listing | sub-agent | 9 tests, all pass |
| T4 | done | P2 | Add /model to auto-complete with subcommand hints | sub-agent | /model l → list subcmd complete |
| T5 | done | P1 | Validate model name on switch (Ollama: check /api/tags) | sub-agent | _validate_model() verified |
| T6 | done | P2 | Show active model marker in /model list output | sub-agent | ● marker on current model |

## Blocked

(none)

## Reports
### T1 — 2026-04-10T08:30+08:00
- Result: /model upgraded to 3 modes (show/list/switch). _list_models() queries Ollama /api/tags. Fixed config key bug (baseUrl → openaiBaseUrl). Model switch reassigns local var, propagates to all subsequent _do_send calls.
- Decision: accept
- Verification: `_list_models()` returns 3 Ollama models. /model handler grep confirmed at line 625.

### T5+T6 — 2026-04-10T08:50+08:00
- Result: Refactored into 4 functions (_is_ollama, _get_ollama_models, _list_models, _validate_model). Validation blocks invalid model names on Ollama, passes through for non-Ollama. List shows ● marker on active model.
- Decision: accept
- Verification: _validate_model returns (False, error) for nonexistent models, (True, "") for valid ones. _list_models shows ● on current model. Non-Ollama providers skip validation.

### T3+T4 — 2026-04-10T09:10+08:00
- Result: 9 model switching tests (is_ollama, validate, list with marker). /model subcommand auto-complete (/model l → list). Fixed phantom command sync issue.
- Decision: accept
- Verification: 16/16 new tests pass. 408/408 existing tests pass. 0 regressions.

## Retro
- Worked:
  - State machine kept rounds clean — no step skipped
  - T2 was absorbed into T1 naturally (listing was part of the same function)
  - Validation + marker added real UX value with minimal code
  - Sub-agent dispatch worked well for T1 and T3
- Failed:
  - T1 sub-agent used wrong config key (baseUrl instead of openaiBaseUrl) — caught in VERIFY
  - /model list as SLASH_COMMANDS entry broke sync test — caught by existing test
- Change next:
  - Cross-provider switching (Ollama → OpenAI) deferred — requires provider recreation, separate iteration
  - Consider adding /provider command for interface switching

## Next Step

- Next action: ARCHIVE this ledger
- Next owner: main agent
