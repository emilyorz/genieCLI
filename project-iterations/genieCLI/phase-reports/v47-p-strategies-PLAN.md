# v47 PLAN — codify the P1–P8 rewrite-strategy menu (P5 gap closed)

> Sam-acked 2026-06-13. Closes the recurring "tGenie P1–P8 docs (P5 gap)" backlog
> deferred since v37 (v39/v40/v41 all re-deferred it).

## Problem

The optimize step rewrites flagged fragments with an **unconstrained LLM call**
(`trino_optimize.py` ~730: prompt = findings' suggestions + "return rewritten SQL").
The manager's eight rewrite strategies (P1–P8) existed only as a mental model — never
codified, with **P5 undefined**. So the optimizer freestyles instead of applying named,
safety-vetted rewrites, and there is no shared vocabulary between detection (R1–R10),
the optimizer prompt, and the docs.

## Decision

Codify P1–P8 as an explicit, action-tier-tagged **fix menu** (how to rewrite a monster),
distinct from detection (R1–R10 + cost; unchanged). P5 = **predicate/partition pushdown**
(Sam-confirmed). Each strategy carries a safety tier that drives how its rewrite is admitted:

| #  | strategy                      | tier      | optimize action            |
|----|-------------------------------|-----------|----------------------------|
| P1 | function-pushup               | SAFE      | rewrite                    |
| P2 | exists-to-left-join           | TRAP      | rewrite + must verify      |
| P3 | like-to-contains              | DANGEROUS | advise only                |
| P4 | listagg-to-slice              | DANGEROUS | advise only                |
| P5 | predicate-partition-pushdown  | TRAP      | rewrite + must verify      |
| P6 | lambda-rewrite                | DANGEROUS | advise only                |
| P7 | skinny-join                   | SAFE      | rewrite                    |
| P8 | broadcast-hint                | SAFE      | rewrite                    |

## Tasks

1. **T1 — `p_strategies.py` reference + contract test.** Eight `PStrategy{id,name,trigger,
   recipe,tier,safety_note}` + tier→action map + `render_menu()`. `test_p_strategies.py`
   completeness gate (P1–P8 present, no gap/dupe, tiers valid, DANGEROUS⇒advise,
   TRAP⇒verify). _Verify: pytest green; menu renders 8 with safety flags._ **(done)**
2. **T2 — wire the menu into the optimize prompt.** Feed `render_menu()` into the
   `trino_optimize.py` rewrite prompt so the LLM applies a NAMED strategy; DANGEROUS
   strategies never enter the auto-apply path (advise only); TRAP rewrites stay gated by
   the existing column/row-level semantic checks. Check `--direct` path parity.
   _Verify: prompt contains the menu; dangerous-tier rewrite is not auto-applied; suite green._
3. **T3 — SKILL.md ×2 docs.** Document P1–P8 in `mcp_trino/SKILL.md` + `trino_query/SKILL.md`
   so the LLM and humans share the vocabulary. _Verify: both list P1–P8; consistent with T1._
4. **T4 — feature doc + close-out.** `features/trino-research.md` design note + STATUS v47 entry.

## Boundaries

- Do NOT touch R1–R10 detection or the 4-action gate wiring.
- Do NOT weaken the v40/v41 column + row-level semantic gates; P-strategy tier *feeds* them.
- Safety-first: DANGEROUS strategies are advisory by default, never auto-applied.
- Dual-path: wire wherever an LLM rewrite prompt exists (trino_optimize + any --direct rewrite).
