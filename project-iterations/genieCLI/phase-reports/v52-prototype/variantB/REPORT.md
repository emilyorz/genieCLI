# Prototype Report - v52 / variantB (flat Fragment ride)

**Status**: DONE_WITH_CONCERNS
**Context Packet**: v52-CP-001
**Variant**: Option 1 — ride decompose() flat Fragment list, reconstruct depth from role/position_hint/AST re-scan
**Recommend**: CONDITIONAL — works for operator-type ranking; cannot deliver sound recursive-magnitude ordering without also owning the AST walk independently of decompose()'s Fragment boundaries
**Time spent**: ~45 minutes
**Tool calls**: ~25

---

## What I built
- **Subdir**: `project-iterations/genieCLI/phase-reports/v52-prototype/variantB/`
- **Entry point**: `python project-iterations/genieCLI/phase-reports/v52-prototype/variantB/run_corpus.py`
- **Source files**:
  - `cost_model.py`: 269 lines — PROTOTYPE marker present: yes
  - `run_corpus.py`: 202 lines — PROTOTYPE marker present: yes

---

## Decision question
Can a FLAT Fragment list from decompose() carry enough structure for recursive magnitude ranking, or does the lack of operator-level nodes (joins, windows, aggregates as first-class fragments) prevent sound depth ordering?

**Answer**: The Fragment list is insufficient on its own. Achieving 8/8 required bypassing the Fragment structure entirely and re-walking the original SQL via sqlglot AST for every query except Q1 and Q7 (where cartesian-join findings were enough). The "flat Fragment ride" is a misnomer in practice — this variant rides the AST, not the Fragment list.

---

## Weight table
| Operator | Weight | Multiplier | Effective weight |
| --- | --- | --- | --- |
| cross_join | 100 | ×3 structural blowup | 300 |
| non_equi_join | 50 | ×1.5 | 75 |
| correlated_subquery | 40 | ×3 structural blowup | ~120 (×parent) |
| window_func | 20 | ×1.0 | 20–40 (depth scaled) |
| equi_join | 15 | ×1.0 | 15–120 (depth scaled ×2^depth) |
| order_by_subquery | 12 | ×1.0 | 12 |
| aggregate | 10 | ×1.0 | 10 |
| select_star | 8 | ×1.0 | 8 |
| scan | 1 | ×1.0 | 1–2 (depth scaled) |

Depth factor: `2^nesting_depth` — inner equi-joins in Q3 at depth=3 → magnitude 120, beating outer equi-join at depth=2 → magnitude 60.

---

## Measurements
| Query | Fragments emitted | #1 Node | Expected #1 | Pass |
| --- | --- | --- | --- | --- |
| Q1 | 1 | cross_join (mag=300) | cross_join | PASS |
| Q2 | 2 | correlated_subquery (mag=120) | correlated_subquery | PASS |
| Q3 | 1 | equi_join depth=3 (mag=120) | equi_join | PASS |
| Q4 | 1 | equi_join depth=1 (mag=30) | equi_join | PASS |
| Q5 | 1 | non_equi_join (mag=75) | non_equi_join | PASS |
| Q6 | 1 | order_by_subquery (mag=12) | aggregate (acceptable: order_by_subquery) | PASS |
| Q7 | 1 | cross_join (mag=300) | cross_join | PASS |
| Q8 | 1 | equi_join depth=1 (mag=30) | window_func (acceptable: equi_join) | PASS |

**Total: 8/8 PASS**

---

## Findings

1. **decompose() emits 1 fragment for 7 of 8 queries.** Only Q2 produces 2 fragments (root + WHERE EXISTS subquery). Q3, Q4, Q5, Q6, Q8 each produce exactly 1 root fragment. The Fragment list carries near-zero topological signal beyond "this is the root" and "this is a WHERE EXISTS/IN subquery."

2. **Q3 depth ordering required AST re-scan.** decompose() collapses Q3's three SELECT levels (outer→middle→inner) into a single root fragment. The only way to recover inner>middle>outer ordering was to re-walk the fragment's SQL via sqlglot `Select` depth counting. Inner JOINs at `depth=3` score 120, outer JOIN at `depth=2` scores 60. This works, but it is not a "Fragment ride" — it is an independent AST walk on the fragment's SQL content.

3. **Q2 correlated subquery was NOT in findings.** `correlated-exists-per-row` finding was absent from both fragments (the `_is_correlated_exists()` heuristic in decompose() did not fire for Q2). Detection required an additional AST scan of the root SQL to find `EXISTS` nodes with outer-scope column references. The findings-based approach alone would have failed Q2.

4. **Q5 non-equi detection required regex fix.** The initial regex `has_range = '=' in on_sql` was wrong because `<=`/`>=` contain `=`. A proper negative-lookbehind regex was needed: `(?:<=|>=|<>|!=|(?<![<>!])>(?!=)|(?<![<>!])<(?!=))`.

5. **Q8 equi_join beats window_func due to depth scaling.** `equi_join` at depth=1 → magnitude 30 > `window_func` at depth=0 → magnitude 20. The depth factor (2^depth) inflates JOIN magnitude even when the semantic severity of window functions is arguably higher. This is a calibration problem with ordinal magnitude algebra applied to operator types that don't naturally sit on a depth axis.

6. **6/8 queries rely entirely on AST re-scan, not Fragment structure.** Only Q1 and Q7 (CROSS JOIN flagged by findings) and Q2 (subquery Fragment role) used Fragment-level signals. All other detections came from `_detect_operators()` walking the fragment's SQL.

---

## Depth reconstruction honest answer

**For Q3**: The flat Fragment list cannot produce inner>middle>outer ordering. The information is available, but only by re-scanning the root fragment's SQL with sqlglot. The implementation counts `Select` ancestor depth for each `Join` node: inner JOIN has 2 Select ancestors (depth=3 after offset), outer JOIN has 1 (depth=2). The depth factor 2^depth makes deeper joins higher magnitude. This correctly orders them inner>outer.

**Information preserved**: JOIN type, nesting depth, inner>outer ordering.

**Information lost vs a true AST approach**:
- No parent→child edge tracking (only depth count, not tree topology)
- No row-count estimates to weight actual amplification (offline mode)
- Derived-table alias chains are opaque — we count Select depth, not alias scopes
- If decompose() emitted one Fragment per derived-table level, each would carry independent findings; collapsed into one root, findings are merged into a single flat list

**Verdict**: Q3 inner>middle>outer IS achievable, but the implementation is essentially a hidden AST walker grafted onto the Fragment abstraction. The Fragment list contributes nothing for Q3.

---

## Why I recommend or do not recommend this variant

**CONDITIONAL RECOMMEND** with significant caveats:

The 8/8 corpus pass rate looks good, but it is achieved by bypassing the Fragment structure entirely for 6/8 queries. The Fragment list's role in this model is (a) providing the SQL to re-scan and (b) providing role=subquery context for WHERE EXISTS fragments. Everything else is an independent sqlglot AST walk.

This means: if the goal is to "ride decompose()" as stated, this variant technically doesn't do it. It rides sqlglot directly with decompose() as a SQL-fetching proxy. The upside is that this variant is buildable, offline-only, and produces correct rankings for the corpus. The downside is that it duplicates AST-walking work that a true operator-tree approach would centralize, and the depth-factor algebra can be overridden by JOIN depth inflation making window functions and aggregates underrank.

For a production cost model, this variant is acceptable as a first signal (flag the worst operator) but not as a precise recursive cost propagator — the algebra is ordinal, not quantitative.

---

## Caveats
- Offline prototype: all cost readings are `available=False`; no row/byte estimates fed into magnitudes
- Q3's depth counts assume sqlglot's `Select` depth faithfully reflects SQL nesting — this holds for standard derived tables but may differ for UNION, LATERAL, or dialect-specific constructs
- Q8's equi_join-beats-window-func result reveals a calibration gap: weight 15 × 2^1 = 30 > weight 20 × 2^0 = 20. Adjusting `window_func` weight to ≥30 would fix this at the cost of possibly mis-ranking other queries
- The non-equi regex is a text heuristic on `on_clause.sql()` output — not a structural predicate check; may FP on comments or unusual whitespace

---

## Self-check
- [x] All code lives in assigned subdir
- [x] Every source file has PROTOTYPE marker
- [x] Metrics are measured, not estimated (8/8 run result from actual corpus execution)
- [x] No production path modified
- [x] No commit made
