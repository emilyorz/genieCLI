# v51 HANDOVER — critical-path (correlated-subquery-in-join) detection & optimization

> Written 2026-06-15 by Emily for continuation in a FRESH session. Self-contained.
> File:line anchors are verified against HEAD `f367a1c` (main) — RE-VERIFY before citing; the tree moves.
> Read `project-iterations/genieCLI/CODEBASE-NOTES.md` first (durable facts, verify-then-trust).

## Lineage (what's already shipped — all on main)
- **v49** (`d4d989e`'s parent line): trino_optimize now extracts top-level `WHERE EXISTS` / `WHERE IN (subquery)` predicate subqueries as `role="subquery"` fragments with monotonic ordinals; position-based `_apply_rewrites`; correlation detection sets `is_independently_runnable=False`. Fake-green `test_t2_6` rewritten real.
- **v50** (`d4d989e` feat + `f367a1c` docs): made decomposition VISIBLE — live TUI breadcrumb via `render_tui` wired into the shared `_seed_decompose_and_select` locus; report shows each fragment's full SQL via `_fmt_detail_fragment`, including unchanged/SKIPPED fragments.
- Baseline suite at HEAD: **1482 passed / 11 skipped / 0 failed**. Run: `source ~/.hermes/hermes-agent/venv/bin/activate && python -m pytest -q -rs`.

## The problem v51 solves (Sam's real workload)
A correlated `EXISTS` buried inside a JOIN's derived-table subquery (which itself has `AND exists(select 1 from xxx where aa=bb)`):
```
SELECT ...
FROM a
JOIN (SELECT ... FROM b WHERE ... AND exists(select 1 from xxx where aa=bb)) j ON ...
```
Two facts established with Sam:
1. **It is not extracted today.** v49's walk is FLAT (top-level WHERE only). EXISTS inside a JOIN subquery / CTE body is swallowed in `__root__`. (Verified: top-level EXISTS extracts; CTE-body EXISTS and select-list scalar do NOT.)
2. **Naive recurse-and-isolate is WORSE than nothing.** If you extract the EXISTS as a standalone `select 1 from xxx where aa=bb` fragment and cost it in isolation, it looks cheap → the report says "fine" → you LOSE the real signal. The cost is positional: a correlated EXISTS evaluated **per-row inside a join** = `subquery_cost × join_fan_out`. The problem IS the position, not the SQL text.

**The right unit of analysis is the PATTERN "correlated EXISTS/IN evaluated per-row inside a join/derived subquery", carrying its enclosing context — not the isolated fragment.** Sam's framing: "遞迴進去挖出 critical path 把各條找出來後再進行優化" — recurse to enumerate the critical paths WITH context, then optimize each.

## Current judgment flow (VERIFIED against HEAD — this is what must change)
In `genie/skills/mcp_trino/trino_optimize.py`:
1. **`decompose()` (~:475)** → `_extract_fragments()` (~:582): extracts CTEs (`with_` clause) + root + top-level `WHERE EXISTS/IN`. FLAT — no recursion into join subqueries / CTE bodies / nesting.
2. **Per-fragment cost** (in `decompose`, ~:428): `if raw["is_independently_runnable"]: cost = cost_reader_fn(frag_sql)` else cost = unavailable (`"not_independently_runnable"`). → **correlated fragments get NO cost signal.**
3. **Monster ranking**:
   - `_heuristic_monster_ids()` (~:707): a fragment is a monster candidate **ONLY IF its ISOLATED sql triggered a static-rule finding** with action in {block, rewrite, advise}.
   - LLM refine `_build_monster_prompt()` (~:718): sees per-fragment findings + `cost_scalar`.
4. **Optimize**: in `_produce_decompose_candidate()` (`genie/skills/mcp_trino/research.py`, the read-path wrapper, ~:1965+): only `is_monster` fragments (cap 5) → `optimize(fr, llm)`; the rest → passthrough `"unchanged"`.

**Character of the current judgment: isolated-fragment-centric.** It ranks each fragment by (a) findings on its OWN sql and (b) its OWN standalone cost. A positional anti-pattern is structurally invisible to it.

### The three break points for the critical-path case
1. **Extraction**: nested EXISTS not extracted (flat walk).
2. **Cost**: correlated → `is_independently_runnable=False` → no cost signal.
3. **Ranking**: isolated `select 1 from xxx where aa=bb` → no finding + no cost → never a monster → never surfaced. The per-row × fan-out cost is computed nowhere.

## v51a — DETECT & ENUMERATE (this iteration; form-1; LOW risk, display/detection only)
**Goal:** running it SURFACES each critical path (with context) and flags it as "should optimize (advise)". It does **NOT rewrite the query.** After v51a, the report gains the critical-path entries + an advisory; the SQL is unchanged.

**The judgment-flow change (core of v51a) — feed a positional signal into the EXISTING ranking, don't rebuild ranking:**
| # | Change | Where |
|---|---|---|
| ① | **Recursive extraction** with an ancestor/enclosing-context trail | `_extract_fragments` (walk into JOIN subqueries + CTE bodies; track enclosing construct + nesting depth) |
| ② | **New position-aware structural rule** that fires on "correlated EXISTS/IN evaluated per-row inside a join/derived subquery" and **attaches a finding (action=ADVISE)** to that fragment | new detector, run DURING extraction (where ancestor context is available — `scan_sql` on isolated SQL CANNOT see position) |
| ③ | **Reuse** `_heuristic_monster_ids` — it already promotes "fragment with a finding → monster", so ② automatically gets the cost-less correlated fragment ranked | no change to ranking logic |

**Critical scope guard for v51a:** action must be **ADVISE, not REWRITE**. Do NOT let the generic `optimize(fr, llm)` rewrite a correlated fragment in isolation — that is unsafe (it references outer columns; an isolated rewrite is wrong). v51a surfaces + advises only. The route: positional finding → monster (so it's surfaced/explained) → BUT the optimize step for a correlated/positional fragment emits an advisory ("correlated EXISTS in join — evaluated per outer row; candidate for decorrelation to semi-join"), NOT an LLM rewrite. Confirm `optimize()` / the read-path loop does not auto-rewrite ADVISE-only positional fragments.

**Done = :** for Sam's join-nested EXISTS query, the report lists the critical path(s) with: the fragment SQL, its enclosing context (inside join subquery), correlated=yes, per-row evaluation note, and an advisory direction. Query unchanged. New tests fail-when-removed. Zero regression.

## v51b — DECORRELATE & OPTIMIZE (NEXT iteration; form-2; HIGH risk, SEMANTIC)
Actually rewrite each surfaced critical path: `EXISTS`→semi-join / `LEFT JOIN ... IS NOT NULL` / decorrelation, as a **deterministic rule** (NOT free-form LLM). This CHANGES query semantics → **row-equivalence is the gate** → run under **form-2** (independent-rerun gate; this is exactly the "looks-green-but-silently-wrong" class form-2 is for — see `memory/feedback_form2_producer_vs_gate.md`). Safety net: `_plan_cost_loop_core` re-verifies every winner with full row-equivalence at `preflight.py:641`. Do v51b ONLY after v51a's detection is solid.

## Design unknowns for the v51a EXPLORE step to resolve
1. **How to carry enclosing context on `Fragment`** (frozen dataclass at `trino_optimize.py:102`): a new field (`enclosing`/`nesting_depth`/`per_row` flag) vs encoding it in a finding's metadata. Mind the dual-path + all Fragment constructors.
2. **Robust correlation detection.** Current `is_independently_runnable` treats unqualified `aa=bb` as inner (NOT correlated) — so Sam's exact `where aa=bb` (no qualifier) may be MIS-detected as non-correlated. The positional rule needs sound correlation detection (outer-column reference resolution across scopes). This is the subtlest unknown.
3. **Exact firing condition** of the positional rule (which ancestor constructs count: JOIN subquery, derived table, lateral; what nesting; EXISTS vs IN vs scalar).
4. **How cost-ranking represents positional cost** (× fan-out) when the fragment has no standalone cost.
5. Whether this lives as a new rule under `genie/skills/trino_query/sql_static/` (the R1–R10 rule home, registered via `rule_gate.py`; adding a rule needs all registration points or `tests/test_rule_id_contract.py` fails — see CODEBASE-NOTES) or inside `trino_optimize` extraction. Likely the detection is in extraction (needs AST context) but the advisory wording can reuse the rule-gate vocabulary.

## Boundaries / deferred (do NOT silently expand)
- v51a is ADVISE-only; no semantic rewrite (that's v51b).
- Still deferred from v49 (out of v51a unless explore shows they're free): select-list **scalar** subqueries, **derived-table** (FROM-subquery) extraction as its own fragment, **UNION** arms.
- Deferred from v50: live breadcrumb for the **plan-cost loop** (`mcp_trino/research.py:1470`) + no-data path (report SQL already covered via shared `render_report`).
- DORMANT, do not touch: `trino_optimize.baseline()` / `verify()`.

## Conventions / how to start the new session
- **Dual-path trap** (CODEBASE-NOTES): `/trino-research` has two sibling entry paths — `mcp_trino/research.py` (MCP default) and `trino_query/research.py` (--direct). Cross-cutting changes must hit BOTH or silently no-op on one. The shared decompose locus is `_produce_decompose_candidate` / `_seed_decompose_and_select` (both in `mcp_trino/research.py`), which the --direct path imports — prefer editing the shared locus.
- **RULE 1**: every new test must fail-when-removed (this whole lineage exists because of one fake-green test — prove RED on revert).
- **RULE 2**: never trust subagent-reported pytest counts; the orchestrator re-runs `pytest -q -rs` and reads the diff. For display/detection features, ALSO render the real example and read the output (v50 develop passed its tests but the deliverable failed for the unchanged-fragment case — caught only by live render).
- **Orchestration**: v51a → task-ledger **feature** profile, **form-1** (manual orchestrate; design-heavy explore+prototype, low semantic risk). v51b → **form-2** (semantic, row-equiv gated). For form-2, wire `NOTIFY = 'cc-connect send --message'` in the driver CONFIG at launch (it defaults to '' — easy to forget; gives per-step Telegram pings).
- Start: `tlv4 init --profile feature --project genieCLI --project-root <abs> --ledger-root <abs>/.tlv4-v51a-critical-path --max-attempts 3` (tlv4 at `/Users/leeabc/.claude/skills/task-ledger-cycle/scripts/tlv4.py`). Branch `v51a-critical-path` off main. Do NOT push/merge until Sam reviews.

## One-line premise lineage
v51 continues v49 (extract subqueries) + v50 (make them visible) → v51a (recurse to enumerate the positional critical paths + advise) → v51b (decorrelate/optimize them, form-2).
