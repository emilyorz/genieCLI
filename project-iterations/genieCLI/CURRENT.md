# CURRENT — v27

## Basic Info

- **Project:** genieCLI
- **Iteration:** 27
- **Status:** active (PLAN — blocked on ack gate)
- **Owner:** Emily (planning + recording); Sam picks the order
- **Started:** 2026-04-20
- **Updated:** 2026-04-20T21:40+0800
- **Focus:** E2E mode disambiguation (stop misreading the daily smoke's `kept=0` as a product regression) + fix cron plumbing so E2E branches actually land as PRs.
- **Touched features:** [trino-research](features/trino-research.md) (Limits + Iteration touchpoint for v27 smoke semantics)

## Goal

- **One-line summary:** Stop the 4am E2E from generating a false product-value signal, and make sure its branch-push actually turns into a PR Sam can review.
- **Done when:**
  - `research-e2e.json` carries an explicit `e2e_mode` field whose value is documented in `features/trino-research.md` Limits.
  - A fresh 4am cron run writes the new field and emits `[INFO] smoke mode — kept=0 expected` instead of `[WARN] No iterations kept`.
  - `cron.log` on the next run shows zero `fatal: ... outside repository` and zero `HTTP 401` lines; a PR URL appears in the log.
  - v26's two P0/P1 promotes are verified in the v27 RETRO Promote Verification table with `worked` evidence.

## Carryover (from v26)

Max 3 items. From v26's promote decisions (2 promotes — under cap).

- ⭐ P0 S — **E2E mode disambiguation** (smoke vs product-value; decide + label + mute false alarm) — from: v26-#change-next-1 (change-next)
- ⭐ P1 S — **Cron plumbing fix** (`E2E-REPORT.md outside repository` git add fatal + `HTTP 401` gh auth) — from: v26-#change-next-2 (change-next)

## Promote Verification (mandatory first PLAN action)

Carryover items from v26 — outcomes will be filled when v27 Todo items complete and their VERIFY passes.

| From | Item | Outcome | Evidence |
|------|------|---------|----------|
| v26-#change-next-1 | E2E mode disambiguation | still-pending | will be filled at v27 RETRO — tied to T1 completion |
| v26-#change-next-2 | Cron plumbing fix | still-pending | will be filled at v27 RETRO — tied to T2 completion (or park if fallback picked) |

## Active Parks (carried from prior iterations)

5 parks entering v27 (v26 aged out 2, promoted 0 from parks, added 2 new).

- Display rounding hides sub-ms metrics (`cpu={:.0f}ms` makes 35us show as 0ms) — age 2/3 — trigger: a real Trino query (not SELECT 1) shows misleading 0 in the optimizer output AND a user complains. Production-sized queries have ms-scale CPU so unlikely to surface. — origin: v25-#change-next-2
- `debug-mcp-tools.py` permanent home (currently in `scripts/`, no `genie debug-mcp` entry point) — age 2/3 — trigger: third time someone (Sam, onboarding, future agent) asks "how do I check if MCP integration is working" — origin: v25-#change-next-3
- "Always probe before patching MCP-contract assumptions" process insight — age 2/3 — trigger: meta-retro at v30 reviews v25-v30 patterns, decides whether to formalize into AGENTS.md or self-model.md — origin: v25-#failed-1
- Ledger roll-over drag ("closing retro doesn't happen until Sam asks for next thing") — age 1/3 — trigger: a second iteration closes >1 day after its final Todo is accepted. If it reappears, formalize a "same-day retro or flag blocker" rule in AGENTS.md. — origin: v26-#failed-2
- Autoresearch product-value signal (separate from pipeline smoke) — age 1/3 — trigger: v27's E2E mode decision lands AND smoke mode is picked; this park then activates as "build a weekly product-value run against Sam's real PBB queries" in v28 or later. If v27 picks product mode instead, this park drops as subsumed. — origin: v26-#change-next-3

## Theme Tracker (cluster radar)

| Theme | Appearances | Status |
|-------|-------------|--------|
| UX polish (cards / banners / spinners / syntax highlights) | v22, v23, v24, v26 (banner fast-fail) | long-term — no per-instance lifecycle; surfaces again only if a UX regression or new polish request arrives |
| E2E signal hygiene (what the test measures vs what the output looks like) | v27 (new — triggered by v26 failed-1) | active — watch whether this becomes a pattern; if a second E2E ambiguity surfaces in v28-v29 re-classify as long-term |

## Hardthink — Alternatives considered

### For P0 (E2E mode disambiguation)

1. **Smoke only + mute false alarm** — keep `noop_build_prompt` + `qwen3.5:4b`; add `e2e_mode: "smoke"` to report JSON; downgrade `[WARN] No iterations kept` → `[INFO] smoke mode — kept=0 expected`. Cheap (~30 min). Preserves pipeline-health signal. Leaves "is the LLM's rewrite actually saving time" unanswered — that question migrates to the product-value park.
2. **Product-value only + solve qwen3.5:9b truncation** — wire real `build_prompt` + fix 9b's ~1800-char output truncation (try Ollama stream mode, or swap model: MiniMax-M2.7 remote / deepseek-coder / qwen-coder). Produces real `kept%` signal. Risk: the truncation may have no workaround on current hardware; if research fails, v27 ships nothing. Model swap also changes cron cost model (remote API budget).
3. **Dual-mode (daily smoke + weekly product-value)** — keep smoke as daily cron; add weekly product-value cron against remote model API. Two reports side-by-side, can't be confused. Most complete signal, highest work (new launchd entry + GH_TOKEN + API key management + first-run debugging).

**Recommendation: #1.** Reasons: (a) cron plumbing P1 already absorbs the other half of v27's budget; (b) product-value needs Sam's real PBB queries, not the synthetic `memory.*` catalog test — shipping product-value against fake data would mis-signal again; (c) park #5 gives a clean hand-off to v28 if Sam wants it promoted.

### For P1 (Cron plumbing fix)

1. **Fix both failures** — (a) copy or symlink the E2E report into the repo's working tree before `git add`, or use `git add -f` against an explicit path; (b) inject `GH_TOKEN` into the launchd `EnvironmentVariables` (read from `~/.config/gh/hosts.yml` or `op item get` at job start). ~1-2 hours. Preserves auto-PR flow. Has a clean fallback to #2 if token injection hits macOS sandbox.
2. **Branch-only mode** — remove the `git add E2E-REPORT.md` + `gh pr create` steps; cron just pushes the branch, Sam opens PRs manually if he wants to review. ~10 min. Gives up automation but stops the silent failures.
3. **Move to GitHub Actions** — E2E from local launchd → GHA runner. Auth becomes trivial. Blocker: local Trino + Ollama can't run on GHA (would need self-hosted runner or remote Trino access), so effectively 1+ days of infra work that delivers the same `kept` signal.

**Recommendation: #1.** Reasons: (a) both fixes are small and reversible; (b) #2 is a clean fallback inside v27 scope — if `GH_TOKEN` injection hits macOS Full Disk Access / keychain permissions, swap to #2 same-day and document; (c) #3 is off-budget (Trino / Ollama locality).

## Hardthink — Scope

### In

- `scripts/geniecli-research-e2e.py` — add `e2e_mode: "smoke"` to report JSON; change WARN → INFO for the smoke-mode expected case.
- `features/trino-research.md` — v27 Limits (explain smoke semantics: noop prompt + 4b model + why kept=0 is expected) + Iteration touchpoint.
- Cron/launchd wiring — fix `git add` path issue and `GH_TOKEN` injection (or swap to branch-only per P1 alternative #2 if injection fails).
- `project-iterations/genieCLI/CURRENT.md` (this file), `archive/v26.md` lock-in, `STATUS.md` roll-over.
- Optional (light-weight, 1 line): AGENTS.md "same-day retro" note — only if Sam agrees (Open question #3).

### Out

- `qwen3.5:9b` truncation investigation — deferred; lives behind the product-value park trigger.
- Product-value E2E implementation — deferred; lives behind park #5.
- Any code change inside `genie/` (core product) — v27 is tooling + docs + cron only.
- The four non-promoted parks (Display rounding, debug-mcp-tools home, Always-probe insight, Ledger roll-over drag) — aging pass only; no implementation work.
- Theme Tracker's UX-polish row — no v27 contribution planned.

## Hardthink — Open questions

1. **P0 mode choice** — Emily's recommendation is alternative #1 (smoke-only + mute). Does Sam agree, or does he want to pull product-value (alt #2 or #3) into v27 despite the model-swap risk and budget impact?
2. **P1 fallback trigger** — if `GH_TOKEN` injection hits a macOS sandbox block (similar to the 2026-04-19 crontab FDA incident), swap to alternative #2 (branch-only) same-day, or park the whole P1 and let v27 ship without cron plumbing fix?
3. **Ledger discipline scope** — v26 retro's "same-day retro" rule is a plausible AGENTS.md addition. Write it in v27 (cheap) or wait until the `ledger roll-over drag` park trigger fires a second time (which would force promote)?

## Todo

_(empty — Ack gate blocks DO. Populated after Sam's ack on the three Open questions.)_

| ID | Status | Pri | Task | Feature | Note |
|----|--------|-----|------|---------|------|

## Reports

_(empty — no Todo has entered DO.)_

## Blocked

- v27 DO entry — blocked on Sam ack for the three Open questions above. SKILL.md rule 15: Ack blocks DO when PLAN lists open questions OR touches a production path; both hold here.

## Retro

_(populated at end of v27)_

### Worked

_(tbd)_

### Failed

_(tbd)_

### Change next

_(tbd)_

### Duplicate check

_(tbd — grep against `archive/v1..v26` and this file before each Change-next item is finalized)_

### Park aging pass

_(tbd — 5 parks entering v27; none expected to auto-drop this round unless triggers fire)_

## Process gap

_(populated at end of v27)_

## Do differently next time

_(populated at end of v27)_

### Next-round Focus (preview)

_(populated at end of v27)_

## Roll-over Checklist

- [ ] Promote Verification table filled with `worked | regressed | still-pending` for both Carryover items
- [ ] All Failed/Change-next items tagged
- [ ] Promote count ≤ 3
- [ ] `features/trino-research.md` updated with v27 Limits + Iteration touchpoint
- [ ] Park aging applied (5 parks entering; ledger-drag park's trigger fires iff v27 also drags)
- [ ] Theme Tracker — E2E signal hygiene row reclassified if a second instance surfaces
- [ ] Move this file to `archive/v27.md`
- [ ] Create new `CURRENT.md` with Carryover from v27 promotes
- [ ] Update `STATUS.md`: Active Iteration pointer → v28, Next Iteration Focus, refreshed Parks, Feature Index
- [ ] Run `scripts/validate_ledger.py` manually
- [ ] No meta-retro due (next at v30; v27 is round 3 of the post-spec run)

## Archive / Handoff

- When archived, this file becomes `archive/v27.md` (read-only).
- STATUS.md is the single entrypoint going forward.
