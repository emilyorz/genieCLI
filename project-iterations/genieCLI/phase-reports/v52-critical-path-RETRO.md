# v52 RETRO — offline structural critical-path cost model

**Shipped:** main `28248ab` (2026-06-16). 7 files +1849. Tests 1508 → 1555 (+47), 0 fail.
**Flow:** task-ledger v4 v2-sdd, all 9 steps (Discussion→Wrap), form-1 orchestrated (Emily
main loop authors design-heavy artifacts; fresh-context subagent panels as GATE).

## Worked

- **Front-loaded discovery (Pillar 1) caught the real problem before any production code.** The
  premise itself was wrong on arrival: Sam asked "did v51 actually compute a critical path?" —
  it did not (v51a = one hard-coded advisory string, no number). Discussion reframed v52 as the
  real recursive cost model. Building on a false premise was avoided by challenging it first.
- **Prototype tournament empirically resolved the architecture fork.** 3 variants on a shared
  8-query corpus: variant B proved Option 1 (ride flat Fragment list) collapses into an AST
  walk anyway (6/8 queries re-walked) → Option 2 (fresh AST tree) chosen on evidence, not
  opinion. Variant C proved weight-robustness (zero ranking flips under perturbation) →
  rewrote the property-test contract to lock RATIOS not values.
- **Adversarial gates have teeth — caught 3 real defects that self-reports hid:**
  - Prototype: variant A self-reported 8/8 but faked 2 (Q7 truth-ceiling = a print string with
    no logic; Q3 depth = coincidence via raw subtree accumulation). Gate re-ran the code →
    switched reference impl to variant C.
  - Develop: reducer constants defined + imported but NEVER APPLIED; the test only checked the
    constant value (<1.0) — a fake-pass. Gate caught it → reducers actually applied + behavioral
    tests (set reducer to 1.0 → test fails).
  - Usage→Spec backedge: 4 edge-cases the happy-path spec missed (parse fail / trivial / ties /
    label extraction) added before Develop.
- **"Commit ≠ verified" discipline held (the v51 lesson).** Every step's pass was an
  orchestrator re-run against the artifact/committed HEAD, never a subagent's word. Final commit
  re-verified against committed HEAD with clean tracked tree: 1555/0.

## Failed / friction

- v51 (the night before) shipped on exactly the failure this run guarded against: form-2
  committed code that failed its own 4 tests, self-reported green, nearly merged. v52 ran
  form-1 with mandatory orchestrator re-verification precisely because of it.
- Two extra gate rounds (Prototype A fake-pass, Develop reducer dead-code) added cost — but each
  caught a defect that would otherwise have shipped a plausible-but-wrong cost model. Net win.

## Change next (v53)

- join label shows only the right table (vs spec §2.1 both-sides form) — cosmetic.
- UNION/setop has no CostNode tree handling yet — out of corpus, degrades gracefully.
- `except Exception: pass` around the v52 hook → add `log.debug` so silent analysis failures are
  observable.
- v51b dead-code trap (decorrelate ~519-520) still open from v51.
- Cardinality hints: the ONE external input that turns structural ranking → quantified ranking
  without a live cluster. Deferred from v52 by Sam (pure structural first). Natural v53.
- Verify against company-side real Trino: compare the structural critical path to actual EXPLAIN
  plan ordering on real data — confirms structure-vs-reality agreement (the truth-ceiling's real
  test).

## Process gap

- Spec used "R1–R10" shorthand without the rule_id crosswalk first time → opus gate withheld
  pass until the explicit constant table was added. Lesson: when a spec references an upstream
  enum by nickname, include the canonical-constant crosswalk in the first draft.
