# v37 PLAN (DRAFT) — Trino Optimization Profile for Task-Ledger V4 form-2

> Status: APPROVED by Sam 2026-06-04. Run A (3 deterministic primitives) executing via form-2.
> Next: promote into `CURRENT.md` (v37 entry) + add `profiles/trino-optimize.json`
> to the task-ledger-cycle skill, then run via the form-2 dynamic-workflow driver.

## Origin

Real case (2026-06-04, Sam's manager — a Trino expert): a report query that "could not finish in 4h"
was brought down to ~1 min. His debugging method, generalized:

1. A whole query is un-actionable. First **decompose** it (by AI) into smaller queries.
2. **Optimize each fragment** independently (mirrors how Trino itself splits a plan into stages across workers).
3. **Recompose** the optimized fragments back along the _original_ query structure → Final Query.

Key insight from Sam: the real value of decompose is **hunting the monster** — locate exactly where the
query is slow. Once the hotspot is named, optimization becomes a _targeted strike_, not blind trial.
genieCLI already does optimization, but feeds the AI the whole query at once → cannot optimize finely.

## Use-case gate

1. **Concrete scenario:** Sam runs the optimizer on a slow Trino _report_ query and wants it
   decomposed, the cost monsters located, structurally optimized, and recomposed — with a hard
   guarantee the report output does not change.
2. **Existing-solution gap:** `/trino-research` optimizes the whole query in one pass (coarse).
   Task-Ledger V4 form-2 has the step/verify/backedge machinery, but its profiles are _for
   development_ (feature / bugfix), not _for optimization_.
3. **Cost of doing vs not:** Without this, the optimizer keeps blind-trialing a whole query and may
   propose rewrites that change report output. With it, deterministic monster-hunting guides a
   targeted, equivalence-verified optimization.

## Core design decision

**Do NOT modify `tlv4.py` (engine) or the form-2 driver control loop.** Both are profile-generic and
already support feature(9-step) / bugfix(4-step). The optimizer is **a third profile**
(`trino-optimize`) + ONE small driver extension (sweep-by-monster, see Open Questions). The V4
verification skeleton — producer → N-judge adversarial panel → deterministic gate →
advance / fail / escalate / backedge — is reused verbatim.

## Step sequence

Maps directly onto the manager's method. `entry: baseline`.

| #   | Step         | Manager's method                    | V4 mode                                 | Gate criteria                                                                                                                                                                                                                                                                                                                                                       |
| --- | ------------ | ----------------------------------- | --------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 1   | `baseline`   | measure                             | single producer                         | Run original query EXPLAIN (+ live run if Trino reachable). Record plan cost, plan_signature, row count, wall time. This is the keep/discard anchor for everything downstream.                                                                                                                                                                                      |
| 2   | `decompose`  | split + **hunt monsters**           | **sweep** (multi-angle)                 | Produce fragment dependency graph **+ ranked monster list + anti-pattern attribution**. Sweep angles: by-cardinality-blowup / by-antipattern / by-explain-cost. Synth merges. **Then a human gate: Sam confirms / overrides / adds a missed monster before optimize runs.** Hard-fail if a monster is missed or mis-attributed — worse than a structural mis-split. |
| 3   | `optimize`   | optimize each fragment              | **tournament**, fan-out = monster count | Only the flagged monsters are touched; clean fragments are left untouched. Each monster runs a strategy tournament: Lambda / skinny-join / broadcast (tGenie P6/P7/P8), selector picks size-aware. Review criteria = tGenie 8-principle checklist + the equivalence tier rules below.                                                                               |
| 4   | `recompose`  | reassemble along original structure | single producer + **strictest gate**    | Stitch optimized monsters back into the original structure. **Highest risk step.** Gate MUST pass row-equivalence vs original query AND cost comparison vs baseline.                                                                                                                                                                                                |
| 5   | `verify`     | global acceptance                   | single producer + panel                 | Run the full final query: (a) row-equivalent to original? (b) how much faster? Equivalence is a **gate, not a metric** — fail → backedge.                                                                                                                                                                                                                           |
| 6   | `wrap_retro` | —                                   | single producer                         | Standard V4 retro for cross-iteration learning.                                                                                                                                                                                                                                                                                                                     |

**Backward edges:** `[["verify","optimize"], ["verify","recompose"], ["recompose","decompose"]]`
(verify can send work back to either optimize or recompose; recompose can demand a re-decompose if
the split itself was wrong).

## Hard constraint: report output MUST NOT change

These queries produce reports → output equivalence is a P0 gate, not a nice-to-have.

- **row-equivalence is the pass precondition.** cost reduction is secondary. A faster-but-different
  result is a FAIL regardless of speed.
- **Scope discipline:** only structural monsters get touched. Micro-optimizations are excluded by
  rule — small time savings, same output-changing risk → bad risk/reward.

### tGenie 8-principle equivalence tiers (the optimize gate's admission rule)

| Tier                              | Principles                                                                  | Why                                                                                                                                                                                                                                                                    |
| --------------------------------- | --------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Safe (guaranteed-equivalent)**  | P1 function pushup, P7 skinny join, P8 broadcast hint                       | Pure relocation / execution-strategy change; values unchanged                                                                                                                                                                                                          |
| **Trap (must verify)**            | P2 correlated EXISTS → LEFT JOIN enrich                                     | EXISTS is a semi-join (does NOT multiply rows); LEFT JOIN on a 1-to-many key **fans out** → base row duplicates → downstream `MAX/SUM/LISTAGG` change. The manager's case worked only because `C.REF_ID`/`D.KEY_ID` happened to be unique to B — **cannot be assumed** |
| **Dangerous (may change output)** | P3 `LIKE '%v%'`→`contains(split)`, P4 `LISTAGG`→`slice(...,1,N)`, P6 Lambda | P3 = substring vs exact-token match semantics; P4 adds an element cap that **truncates**; P6 is a heavy rewrite                                                                                                                                                        |

**Admission rule:** Safe-tier rewrites are always allowed. Trap-tier and Dangerous-tier rewrites are
admitted ONLY if they pass live row-equivalence verification → **when Trino is unreachable they are
disabled**, and optimize is locked to Safe-tier structural rewrites only.

## Environment adaptation

`TRINO_MCP` is configurable (Sam's test cluster / MCP is available).

- **Reachable:** monster ranking uses real EXPLAIN cost; verify runs live row-equivalence + timing;
  Trap/Dangerous tiers are unlocked (each gated by row-equiv).
- **Unreachable:** monster ranking falls back to AI structural reasoning + rule attribution (cost is
  estimated, not measured); optimize auto-locks to Safe-tier only.

Reuse genieCLI's existing L3 row-equivalence + plan_signature primitives for the equivalence check.

## Draft profile (`profiles/trino-optimize.json`)

```json
{
  "name": "trino-optimize",
  "entry": "baseline",
  "steps": [
    "baseline",
    "decompose",
    "optimize",
    "recompose",
    "verify",
    "wrap_retro"
  ],
  "backward_edges": [
    ["verify", "optimize"],
    ["verify", "recompose"],
    ["recompose", "decompose"]
  ],
  "routing": {
    "baseline": { "producer": "sonnet", "reviewer": "sonnet" },
    "decompose": {
      "producer": "opus",
      "reviewer": "opus",
      "sweep": 3,
      "synth": "opus"
    },
    "optimize": {
      "producer": "sonnet",
      "reviewer": "opus",
      "tournament": 3,
      "select": "opus",
      "panel": 3
    },
    "recompose": {
      "producer": "opus",
      "reviewer": "opus",
      "panel": 3,
      "gate": "opus"
    },
    "verify": { "producer": "sonnet", "reviewer": "opus", "panel": 3 },
    "wrap_retro": { "producer": "sonnet", "reviewer": "sonnet" }
  }
}
```

## Risks

1. **recompose can be globally suboptimal** even if every fragment is locally optimal — join order /
   broadcast are cross-fragment decisions. Mitigation: recompose gate compares total cost vs baseline,
   not just per-fragment.
2. **Reviewer score calibration skews lenient** (known from V4 form-2 validation) — independent
   verification of the final query stays mandatory, not optional.
3. **P2 fanout trap** (above) is the most likely silent output-changing bug. The optimize review
   criteria must explicitly check enrich-CTE join key uniqueness.

## Resolved decisions (Sam, 2026-06-04)

1. **Driver extension — sweep-by-monster: APPROVED.** The optimize step's fan-out N comes from the
   previous step's output (decompose's monster list). This is the ONLY control-loop change vs
   feature/bugfix.
2. **Fragment boundary: APPROVED.** "fragment = an independently-EXPLAIN-able sub-plan", CTEs as
   natural cut points; correlated subqueries become enrich-CTE candidates (P2).
3. **Row-equivalence = full result-set compare** (Sam's pick), with non-deterministic columns
   excluded (e.g. `CURRENT_TIMESTAMP AS rectime`). **Requires a reachable Trino cluster.** No-Trino
   fallback: genieCLI does NOT claim equivalence — it emits the optimized SQL and hands verification
   back to the user. (This binds equivalence-claims to the live cluster: no cluster → no claim.)

## Layer clarification (do not conflate)

- **v37 itself is a _development_ iteration**: it BUILDS the `trino-optimize` profile (write the
  profile JSON, extend the driver, build the Trino-MCP integration, row-equiv, tests). It is run with
  genieCLI's normal task-ledger development discipline.
- **The `trino-optimize` profile is v37's _product_**: once built, IT is what later optimizes a real
  report query via form-2. Building the optimizer ≠ running the optimizer.

## v37 development row breakdown

| Row | Work                                                                                        | Verify                                                                       |
| --- | ------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------- |
| T1  | `profiles/trino-optimize.json` + profile loader recognizes it                               | `tlv4 begin` starts a state.json on the `trino-optimize` profile             |
| T2  | Driver `sweep-by-monster` extension (optimize fan-out N from prior step output)             | mock decompose → 3 monsters → optimize spawns 3 parallel agents              |
| T3  | Trino-MCP integration: baseline cost read + EXPLAIN (env-configurable, graceful no-cluster) | against test cluster: reads real plan cost; unreachable → clean fallback     |
| T4  | Row-equivalence: full-set compare + non-deterministic column exclusion                      | known equivalent/non-equivalent query pairs judged correctly                 |
| T5  | `decompose` monster-hunt prompt + human-confirm gate                                        | on the 4h golden query: surfaces the correlated-EXISTS monster + ranks it #1 |
| T6  | `optimize` tGenie 3-tier admission gate                                                     | Safe-tier passes; Trap/Dangerous blocked when Trino unreachable              |
| T7  | `recompose` + `verify` step tasks/criteria (row-equiv P0 gate + cost compare)               | end-to-end on golden case: before → equivalent, faster output                |
| T8  | Golden-case end-to-end run + feature docs                                                   | manager's 4h→1min query reaches an independently-verified equivalent result  |

First real run (T8) uses the manager's 4h→1min query as the **golden case**: before is known, after
is known → validates the pipeline reaches an equivalent, faster result on its own.

## Consolidated design (2026-06-04, Sam + Emily — SUPERSEDES the tGenie tier table above)

The form-2 Run A escalated at `discussion` (4/4 panel fails) because the tGenie P1–P8 tier table
above is **ungrounded against the real genieCLI rule system** — verified in code. This section is the
corrected design and is the authority the next discussion run must follow.

### Two disjoint vocabularies (the root mistake)

- **The manager's P1–P8** (function pushup, EXISTS→LEFT JOIN, LIKE→contains, LISTAGG→slice, Lambda,
  skinny join, broadcast hint) = an **optimization-rewrite mental model**. There is no P1–P8 anywhere
  in the code, and P5 was never defined.
- **genieCLI's real system** = 9 static lint rules (`genie/skills/trino_query/sql_static/rule_ids.py`)
  → a 4-action safety gate (`genie/skills/mcp_trino/rule_gate.py`): **BLOCK** (semantics at risk, do
  not auto-rewrite: cartesian-join, null-unsafe-equals), **REWRITE** (safe candidate, verify
  equivalence: redundant-distinct, order-by-in-subquery, predicate-pushdown, cast-chain,
  join-first-filter-late), **ADVISE** (hint only: select-star, subquery-pushable + 6 directions).

**Decision = Option C.** The tier classifier is built on the **real 4-action gate** (already coded,
already tested — it IS the deterministic Safe/Trap/Dangerous notion). The manager's P1–P8 become
**Run B rewrite strategies**, each tagged with the action-tier its rewrite lands in. P5 is defined
in Run B, not now — it does not block Run A.

### Detection = 9 rules + cost (NOT 9 alone); 8 principles = the fix menu (NOT a 2nd scan)

- Monster detection has two inputs: the **9-rule static scan** (anti-patterns) AND **cost/EXPLAIN**
  (a fragment can pass all 9 rules yet be a cost monster). Both feed the monster ranking.
- The 8 principles are _how to fix_ a named monster (the rewrite menu, applied in Run B's optimize
  step) — they are not a second detection pass.

### Detection runs at THREE stages (Sam's state-machine insight)

A monster can be born at any stage — **especially recompose** (locally-optimal fragments can combine
into a global monster: e.g. two independently broadcast-optimized fragments feeding one join → double
broadcast → memory blowup; each fragment is "Safe" alone). So the detection function is invoked at:

1. **before decompose** — whole query → monster map (guides the split)
2. **after decompose** — each fragment → per-fragment safety tier (gates optimize)
3. **after recompose** — reassembled whole query → cross-fragment monster check **+ row-equivalence
   vs the original (P0 gate)**

One shared detection function, three call sites, stage-specific interpretation of its output. This is
the state machine: states share the tool, differ in transitions.

### Pure vs impure (the FP design constraint)

- **Pure — run freely at every stage:** the 9-rule sqlglot scan + tier classification. Functions of an
  SQL string; no side effects; cheap. The same function must accept **a whole query OR a single
  fragment** (this is a hard Run A interface requirement).
- **Impure / expensive — gate only at real decision points (esp. after recompose):** cost/EXPLAIN read
  and row-equivalence — both hit the live Trino cluster. Do NOT re-hit the cluster at every
  intermediate state.

### Build order = FP-first, then wire (this is exactly the Run A / Run B split)

Build the functions first (Run A), then connect them with the state machine (Run B). Run A's revised
function list:

| #   | Run A function                                                                                                      | Pure?  | Notes                                                                                              |
| --- | ------------------------------------------------------------------------------------------------------------------- | ------ | -------------------------------------------------------------------------------------------------- |
| 1   | **detection scan** — sqlglot 9-rule + per-finding action-tier via `rule_gate.py`; accepts whole query OR a fragment | pure   | the shared 3-stage tool; this REPLACES the old "tier classifier" framing                           |
| 2   | **cost reader** — baseline plan cost + EXPLAIN via Trino-MCP; graceful no-cluster                                   | impure | env-configurable; the other half of monster detection                                              |
| 3   | **row-equivalence comparator** — full-set compare + non-deterministic-column exclusion                              | impure | **extend the existing `genie/skills/mcp_trino/research.py:_results_equivalent()`**, do not rebuild |

Run B (later) = the decompose / optimize / recompose / verify state machine that calls these three,
plus the manager's P1–P8 rewrite strategy menu.

## Open gaps (recorded 2026-06-04 — do not lose)

These are knowledge gaps surfaced when Sam asked "is the optimization info all recorded?". Captured
here so they survive session loss; not yet resolved.

1. **tGenie 8-principle full definitions are NOT documented anywhere.** The tier table above
   references P1–P8 by number only. The tier mapping uses just 7 (P1/P7/P8 Safe, P2 Trap,
   P3/P4/P6 Dangerous) — **P5 is entirely absent**, and no doc defines what each Pn rewrite actually
   _is_. The tier _classifier_ (Run A primitive #3) can be built from the mapping above, but the
   per-principle _detection/optimize prompts_ (Run B) need the full definitions from Sam. **Run B blocker.**
2. **Run A scope vs this plan's T1–T4 row breakdown drifted.** The form-2 Run A driver
   (`workspace-emily/projects/genieCLI-v37/runA-driver.js`) delivers 3 deterministic primitives —
   cost reader (≈T3), row-equiv comparator (≈T4), tGenie tier classifier (pulled forward from T6) —
   and does **not** build T1 (`profiles/trino-optimize.json`) or T2 (driver sweep-by-monster). Run A's
   own `discussion` step (opus, scope gate) is expected to re-confirm this boundary. T1/T2 land in a
   later run.
3. **Source conversation (manager's 4h→1min method) lives only in this draft + the live session** —
   not yet distilled into `workspace-emily/memory/`. The 6/02 broadcast-hint piece is in memory;
   the 6/04 decompose/monster/tGenie-tier method is not. Pending memory-maintenance.
