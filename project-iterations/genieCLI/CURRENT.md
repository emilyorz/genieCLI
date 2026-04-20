# CURRENT — v27

## Basic Info

- **Project:** genieCLI
- **Iteration:** 27
- **Status:** active (DO — PLAN v2 ack'd by Sam via Telegram msg 811)
- **Owner:** Emily (planning + execution); Sam picks direction
- **Started:** 2026-04-20
- **Updated:** 2026-04-20T21:55+0800
- **Focus:** `/trino-research` UX overhaul — terminal slim + iteration-centric report + ./report/ subdir. Removes three pain points Sam identified as "整個有點混亂".
- **Touched features:** [trino-research](features/trino-research.md)

## Goal

- **One-line summary:** Make `/trino-research` output scannable — no terminal dump of giant SQL, no cwd pollution, no triple-SQL duplication in the report.
- **Done when:**
  - Terminal after a run shows summary table + iteration history + `Report saved: ./report/<file>.md` — no full Optimized SQL printed to stdout.
  - Report has **one** Best SQL block (not three), with per-iteration sections that carry the full hypothesis (no 60-char truncation) + a mini diff scoped to that iteration's change + verdict + metric.
  - Report is saved under `./report/<name>.md` (auto-created), not directly in cwd.
  - `features/trino-research.md` reflects the new UX in Design log + Iteration touchpoint.
  - Sam sign-off on the new report/terminal layout (T5 dropped per msg 813 — no real-run verification in v27; will exercise in first natural use after merge).

## Carryover (from v26)

Max 3 items. v26 promoted 2 items; both **deferred** at v27 PLAN ack stage per Sam's Telegram msg 807 ("三個都還好") and msg 809 (picked D — UX — instead).

- ⭐ P0 S — E2E mode disambiguation — **deferred to park** (see Active Parks)
- ⭐ P1 S — Cron plumbing fix — **deferred to park** (see Active Parks)

## Promote Verification (mandatory first PLAN action)

Walk every Carryover item from v26. Outcomes filled at v27 PLAN ack stage (user-directed deferral, not waiting for Todo verification).

| From | Item | Outcome | Evidence |
|------|------|---------|----------|
| v26-#change-next-1 | E2E mode disambiguation | **deferred-by-user-ack** | Telegram msg 807 ("這三個都還好") + msg 809 (Sam picked D instead); moved to park with age 0/3 and revival trigger. Not lost — re-evaluatable at v28 retro or earlier if trigger fires. |
| v26-#change-next-2 | Cron plumbing fix | **deferred-by-user-ack** | Same Telegram msgs; moved to park with age 0/3 and trigger "Sam actually tries to view an E2E PR and can't find it". |

## Active Parks (carried from prior iterations + deferred v26 promotes)

7 parks entering v27 DO (5 carried + 2 deferred from v26 promotes).

- Display rounding hides sub-ms metrics — age 2/3 — trigger: real Trino query (not SELECT 1) shows misleading 0 AND user complains — origin: v25-#change-next-2
- `debug-mcp-tools.py` permanent home — age 2/3 — trigger: third time someone asks "how do I check if MCP integration is working" — origin: v25-#change-next-3
- "Always probe before patching MCP-contract assumptions" — age 2/3 — trigger: meta-retro at v30 — origin: v25-#failed-1
- Ledger roll-over drag — age 1/3 — trigger: a second iteration closes >1 day after final Todo accepted — origin: v26-#failed-2
- Autoresearch product-value signal — age 1/3 — trigger: v27's E2E mode decision lands AND smoke mode picked — origin: v26-#change-next-3
- **E2E smoke mode labelling (new)** — age 0/3 — trigger: next time `kept=0/2` in an E2E report causes a human (Sam, Emily, Elena, or onboarding) to suspect a product regression when it isn't — origin: v26-#change-next-1 (deferred at v27 ack)
- **Cron plumbing: `E2E-REPORT.md outside repo` + `HTTP 401` (new)** — age 0/3 — trigger: Sam actually tries to open an auto-generated E2E PR and finds the branch exists but no PR — origin: v26-#change-next-2 (deferred at v27 ack)

## Theme Tracker (cluster radar)

| Theme | Appearances | Status |
|-------|-------------|--------|
| UX polish (cards / banners / spinners / syntax highlights / output layout) | v22, v23, v24, v26 (banner fast-fail), v27 (trino-research output) | long-term — no per-instance lifecycle; v27 is a substantial new entry |
| E2E signal hygiene (what the test measures vs what the output looks like) | v27 parks (deferred) | dormant — activates when a park trigger fires |

## Hardthink — Alternatives considered

### For T2 (Report iteration-centric rewrite)

1. **Full replacement** — delete Original SQL + Optimized SQL blocks entirely; the report becomes Summary card → per-iteration sections → final Best SQL (once). Diff lives inside each iteration section, scoped to just that round's change. Most aggressive; addresses "三份看三次" completely.
2. **Original as appendix** — keep Original SQL but move it to end of report under a `## Appendix` heading; Optimized SQL gone; per-iteration sections as in #1. Keeps reproducibility for someone who opens the report standalone but pushes it off the first screen.
3. **Minimal patch** — keep current layout but un-truncate hypothesis (drop `[:60]`) and add a verdict column. Cheapest, doesn't address Sam's "整個混亂" complaint.

**Recommendation: #1.** Sam explicitly named "三份看三次" as a pain. #2 adds structural complexity (appendix navigation) without solving it. #3 is rearranging deck chairs. If a consumer of the report needs Original SQL, they can read the first iteration's diff (delta from Original) or rerun with `--show-original` in a future iteration.

### For T3 (report path)

1. **`./report/`** — Sam's suggestion (msg 811 literally said "report folder"). Per-project, co-located with the SQL being researched. Clean under one project; noisy if Sam runs /trino-research from home or a temp dir.
2. **`~/.genie/reports/`** — global cache. Clean across projects, but decouples report from the project context (finding "the report for yesterday's PBB query" requires grep across all projects).
3. **`$XDG_STATE_HOME/genie/reports/`** — XDG-compliant version of #2.

**Recommendation: #1** (Sam explicitly picked it). If cwd-polution becomes annoying later, add `--report-dir` flag; don't pre-engineer.

### For T1 (terminal slim)

1. **Pure redirect** — terminal shows "Report saved: ./report/xxx.md", nothing else after the run (summary too). Saves the most scroll.
2. **Summary-only** — terminal keeps summary table + iteration one-liners (current lines 570-582 behavior), drops the full SQL print. Sam still sees the outcome at a glance without navigating to the file.
3. **Current + pagination** — pipe long SQL through `less` if tty, skip if not. Complexity, fragile.

**Recommendation: #2.** Sam's pain is the "70 lines of Optimized SQL洗版", not the summary/iteration lines — those are compact and useful. #1 is overcorrection (forces Sam to open the file even for quick runs). #3 overengineered.

## Hardthink — Scope

### In

- `genie/skills/trino_query/research.py` — three edits:
  - `run_trino_research` line 584-590: remove full Optimized SQL print; keep "Optimized SQL saved" pointer
  - `_generate_report` lines 390-462: rewrite to iteration-centric layout; remove Original/Optimized duplicates; un-truncate hypothesis; add per-iteration mini diff
  - Report save path lines 595-599: change `Path.cwd() / report_name` → `Path.cwd() / "report" / report_name` with `mkdir(parents=True, exist_ok=True)`
- `features/trino-research.md` — v27 Design log + Iteration touchpoint + Current capability bump
- `project-iterations/genieCLI/CURRENT.md` + `STATUS.md` — this PLAN v2 + Todo progression

### Out (explicitly deferred)

- Semantic diff (AST-level change summary) — larger body of work; can park if Sam wants it in v28
- Side-by-side / word-level diff rendering — current unified diff kept; no new dep
- `--report-dir` flag — add when someone asks; not pre-engineered
- Machine sink / JSON output format — untouched
- Interactive (`genie chat`) vs non-interactive (`--sql-file`) divergence — both behave identically after T1
- All seven Active Parks (aging-only, no implementation this round)

## Hardthink — Open questions

**None — proceeding.**

Rationale: Sam acked A/B/C via Telegram msgs 809 + 811 and explicitly dropped T5 in msg 813 ("T5 不用 , 先走 T1~T4"). Folder name fixed to `./report/` per his word. Diff style stays unified (no new dep). Interactive/non-interactive unified behavior (no question).

## Todo

| ID | Status | Pri | Task | Feature | Note |
|----|--------|-----|------|---------|------|
| T1 | pending | P0 | Terminal slim: drop full-Optimized-SQL print in `run_trino_research` (research.py:584-590); keep summary + iteration lines | trino-research | Keep "Report saved:" pointer; interactive/non-interactive identical behavior |
| T2 | pending | P0 | `_generate_report` iteration-centric rewrite: remove Original/Optimized full dumps; per-iteration section with full hypothesis + mini diff + verdict; one Best SQL block at end | trino-research | Biggest code change of the iteration; keep stdlib `difflib` — no new dep |
| T3 | pending | P0 | Report path → `./report/<name>.md` with `mkdir(parents=True, exist_ok=True)` (research.py:595-599) | trino-research | Sam's word: cwd底下開 report folder |
| T4 | pending | P1 | `features/trino-research.md` — Design log entry (UX trio), Iteration touchpoint (v27), Current capability snapshot update | trino-research | Doc-track VERIFY enforcement (SKILL.md rule 10) |

## Reports

_(populated as Todo items complete)_

## Blocked

- None — T5 dropped per Sam Telegram msg 813 ("T5 不用 , 先走 T1~T4"). v27 closes on T1-T4.

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

_(tbd — 7 parks entering v27; trigger checks at retro time)_

## Process gap

_(populated at end of v27)_

## Do differently next time

_(populated at end of v27)_

### Next-round Focus (preview)

_(populated at end of v27)_

## Roll-over Checklist

- [ ] Promote Verification table filled (v26 deferrals recorded; v27 Todo items verified as they complete)
- [ ] All Failed/Change-next items tagged
- [ ] Promote count ≤ 3
- [ ] `features/trino-research.md` updated with v27 Design log + Limits + Iteration touchpoint (T4 enforces this)
- [ ] Park aging applied (7 parks entering; aging triggers checked)
- [ ] Theme Tracker — UX polish row bumped with v27 entry
- [ ] Move this file to `archive/v27.md`
- [ ] Create new `CURRENT.md` with Carryover from v27 promotes
- [ ] Update `STATUS.md`: Active Iteration pointer → v28, Next Iteration Focus, refreshed Parks, Feature Index
- [ ] Run `scripts/validate_ledger.py` manually
- [ ] No meta-retro due (next at v30; v27 is round 3 of the post-spec run)

## Archive / Handoff

- When archived, this file becomes `archive/v27.md` (read-only).
- STATUS.md is the single entrypoint going forward.
