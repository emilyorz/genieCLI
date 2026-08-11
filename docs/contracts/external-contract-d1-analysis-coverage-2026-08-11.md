# External Contract — genieCLI D1 Analysis Coverage (~80% of Opus structural analysis)

Date: 2026-08-11  
Owner: Emily (scheduler) · Planner + review gate: **Fable 5**  
Worktree: `/tmp/genieCLI-d1-analysis-coverage-wt` @ `373f153`  
Ledger: `/tmp/tlv5-geniecli-d1-analysis-coverage-v1`  
Profile: standard4 / medium / foreground

## Goal (Sam)
What Opus 4.6 can **analyze**, genieCLI should analyze ~**80%** — even with a **weaker model**.

Fable locked definition (**D1 only**):
- **D1 Analysis coverage** = of structural opportunities in a frozen oracle, SCAN+PLAN surfaces ≥80% at **precision ≥60%** (or bounded recall@K)
- **NOT D2** verified auto-apply 80% (later)
- **NOT D3** SQL looks like Opus / speedup 80% (**FORBIDDEN**)

Archive: `memory/FABLE5-80PCT-ANALYSIS-GOAL-2026-08-11.md`

## North star this run
Make D1 **measurable and improvable**, then close highest-frequency scan/plan gaps so weak-model path has a credible climb toward 80% — **without** EXECUTE_ALL, without % speedup claims, without fake apply.

## In scope (this V5)
1. **Oracle fixture format + seed set** (start small but real; expandable to N≥30 later)
2. **Scorer**: match genieCLI SCAN (+ optional PLAN ids) vs oracle → recall, precision, per-category
3. **CI/tests** for scorer + at least one regression fixture
4. **Gap-driven P-hit / scan improvements** only where tests prove miss→hit (no drive-by)
5. **Report/CLI hook** (minimal): emit analysis coverage numbers when oracle present OR document how to run scorer
6. Honest mode split notes: offline vs MCP (detection is static; risk class may differ)

## Out of scope
- EXECUTE_ALL default
- Claiming 80% without oracle+scorer
- D2 full auto structural apply as default
- Speedup % product claims
- Matching Opus prose style
- Full N≥30 human-adjudicated Opus corpus in one night (seed + format + path is enough if Fable agrees)

## Acceptance (must be falsifiable)
- `pytest` green for new scorer + fixtures
- Scorer outputs recall + precision (not recall-only)
- At least one fixture where known structural sites are scored
- Document: how to add oracle cases; forbidden claims list
- No silent EXECUTE_ALL; dangerous stays advise/NEEDS_HUMAN

## Seats
- **spec.producer = planner = Fable 5**
- **ticket/develop = Emily/coder**
- **every review gate = Fable 5** (gate_reviewer / step_reviewer as profile allows — Sam: Fable supervises all review)
- Emily iterates on Fable must-fixes until SHIP or pending_decision

## Open Q for Fable planner
1. Seed oracle size tonight: 3 vs 5 vs 10 synthetic+labeled cases?
2. Scorer match key: pid-only vs pid+site-family?
3. Is PLAN ordering in D1 this run or detection-only first?
4. Which scan gaps to close first if timeboxed (P1/P9/P10 recall vs new detectors)?
