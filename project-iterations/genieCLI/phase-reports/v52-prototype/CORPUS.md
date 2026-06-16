# v52 Prototype Corpus — queries with known structural bottleneck

> Shared evaluation set for all prototype variants. Each query has an EXPECTED critical-path
> dominant node (the node a sound offline structural model should rank #1) and an expected
> relative ordering note. The model is judged on RELATIVE ordering match, never absolute cost.
> Q7 is the deliberate truth-ceiling case (structural #1 disagrees with data reality) — a
> correct model must still pick the structural answer AND the report must flag the ceiling.

All queries are read SELECTs. Use sqlglot trino dialect. Tables are illustrative; no DB exists
(offline). Column qualifiers are intentional where they matter (correlation tests).

---

## Q1 — Cartesian (cross join). Expected #1: the CROSS JOIN node.
```sql
SELECT o.id, c.name
FROM orders o
CROSS JOIN customers c
WHERE o.status = 'OPEN';
```
Expected: cross join dominates (N×M). Must rank #1 unconditionally.

## Q2 — Correlated subquery in WHERE over a driving table. Expected #1: the correlated subquery (per-row).
```sql
SELECT o.id
FROM orders o
WHERE EXISTS (
  SELECT 1 FROM line_items li WHERE li.order_id = o.id AND li.qty > 100
);
```
Expected: correlated EXISTS = parent_magnitude × inner_cost dominates over the outer scan.

## Q3 — Deep nested subquery (3 levels) with a join at the bottom. Expected #1: the bottom join (deepest under most accumulated magnitude).
```sql
SELECT t.*
FROM (
  SELECT a.id, a.v
  FROM (
    SELECT x.id, x.v
    FROM facts x
    JOIN dims d ON d.id = x.dim_id
    WHERE x.v > 0
  ) a
  JOIN more m ON m.id = a.id
) t
WHERE t.v < 1000;
```
Expected: deepest join (facts⋈dims) carries highest magnitude; outer wrappers lower. Ordering:
inner join > middle join > outer filter/project.

## Q4 — SELECT * feeding a join vs a clean scan. Expected #1: the wide join input (select-star scan feeding the build/probe).
```sql
SELECT *
FROM big_fact f
JOIN small_dim d ON d.id = f.dim_id;
```
Expected: select-star on a join input (wide shuffle) outranks the join key itself; the scan of
big_fact under select-star is the hot node. (R2 select-star penalty on a node already under
join magnitude.)

## Q5 — Multiple joins, one non-equi (range join). Expected #1: the NON-EQUI join.
```sql
SELECT a.id
FROM a
JOIN b ON b.id = a.id
JOIN c ON c.lo <= a.v AND c.hi >= a.v;
```
Expected: the range/non-equi join (c) dominates over the equi join (b) — non-equi = nested
loop blowup. Ordering: non-equi join (c) > equi join (b).

## Q6 — ORDER BY without LIMIT inside a subquery, then aggregated. Expected #1: depends — likely the GROUP BY aggregate over the sorted input, with the wasted sort as a strong secondary.
```sql
SELECT s.k, count(*)
FROM (
  SELECT k, v FROM events ORDER BY v
) s
GROUP BY s.k;
```
Expected: the unnecessary ORDER BY (R4) is a clear penalty node; the GROUP BY is the structural
work. A sound model ranks both above the bare scan; document which it puts #1 and why.

## Q7 — TRUTH-CEILING CASE: cross join of two tiny dims. Structural #1: the cross join. Data reality: harmless.
```sql
SELECT a.label, b.label
FROM color_dim a
CROSS JOIN size_dim b;
```
Expected: structural model MUST still rank the cross join #1 (it cannot know the dims are tiny).
The report MUST flag the offline truth-ceiling here ("ranked by structure; actual cost depends
on data volume unknown offline"). This is the honest-limitation test, not a model failure.

## Q8 — Window function over a large partition + a join. Expected #1: the window (sort+partition) OR the join — document the model's pick and the reasoning.
```sql
SELECT f.id, row_number() OVER (PARTITION BY f.cust_id ORDER BY f.ts DESC) rn
FROM fact f
JOIN cust c ON c.id = f.cust_id;
```
Expected: window (partition sort) and the join both heavy; a sound model ranks both above the
bare scans. Document ordering + reasoning.

---

## Scoring rubric for variants (the selector uses this)

- **Hard requirements (must pass all):** Q1 cross join #1; Q7 cross join #1 + ceiling flagged;
  Q2 correlated subquery #1; Q5 non-equi join > equi join.
- **Ordering credibility:** Q3 inner > middle > outer; Q4 wide join-input hot; Q6/Q8 both
  heavy nodes above bare scans with documented reasoning.
- **Design fidelity:** does the variant implement RECURSIVE magnitude propagation (depth
  matters) or a flat approximation? The fork (Option 1 flat Fragment ride vs Option 2 fresh
  AST tree) is decided HERE: a variant that cannot reproduce Q3's depth ordering fails the
  recursive-magnitude requirement.
- **No absolute-number assertions** — only relative ordering. Zero EXPLAIN / zero query.
