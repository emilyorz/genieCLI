"""v56 — offline structural verification + per-strategy review checklists.

Pure-AST (sqlglot) only: no cluster, no EXPLAIN, no data, no catalog. This is the
honest ceiling of "程式管驗證" on the company's offline-only environment, where the
target tables do not exist so the row-equivalence gate (which needs execution) can
never run. We cannot PROVE row-equivalence offline; we CAN:

  1. Prove the one structural property that makes P9 (exists-to-preagg-join) beat
     P2's naive LEFT JOIN — that the decorrelation pre-aggregates the SAME inner table
     by the correlation key and joins it back on that key, so the join is row-preserving
     by construction (no fan-out). This is a deterministic AST fact, independent of data.
  2. Emit a per-strategy verification CHECKLIST for the residual, data-dependent
     properties a human (or a second LLM judge) must confirm before applying.

IRON RULE: never emit a false PROVEN_NO_FANOUT. The proof is bound to ONE chain —
a correlated EXISTS over inner table T → a CTE that GROUPs T BY the correlation key →
a LEFT JOIN that ties THAT CTE back on that key, with the GROUP BY grain no finer than
the join key. Any broken link, ambiguity, or parse failure degrades to CANNOT_VERIFY.

Why the grain rule is `GROUP BY ⊆ join key` (not the reverse): GROUP BY G makes the
CTE unique per distinct G-tuple. The LEFT JOIN matches on key set K. The CTE is unique
on K iff G ⊆ K — grouping NO FINER than the join key. A pre-agg grouped by (fk, param)
joined on fk alone yields many rows per fk → fan-out.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class FanoutVerdict(str, Enum):
    """Result of the offline P9 fan-out structural check."""

    PROVEN_NO_FANOUT = "proven_no_fanout"  # bound chain proves one row per join key → no fan-out by construction
    KEY_MISMATCH = "key_mismatch"          # a pre-agg CTE is joined back but on the wrong grain → likely wrong
    NOT_APPLIED = "not_applied"            # the rewrite did not decorrelate (correlated EXISTS still present)
    CANNOT_VERIFY = "cannot_verify"        # parse error / shape not recognisable offline — human must check


@dataclass(frozen=True)
class FanoutResult:
    verdict: FanoutVerdict
    detail: str
    correlation_keys: tuple[str, ...]  # inner-side correlation column names found in the original EXISTS
    groupby_keys: tuple[str, ...]      # GROUP BY column names of the bound pre-aggregation CTE (if any)


# ---------------------------------------------------------------------------
# Correlation extraction from the ORIGINAL query (fail-safe, read-only)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class _Correlation:
    inner_keys: frozenset[str]    # inner-side correlation column names (the keys to GROUP BY)
    inner_tables: frozenset[str]  # real table names inside the EXISTS body (the relation to pre-aggregate)


def _correlations(sql: str) -> tuple[list[_Correlation], bool]:
    """Extract one _Correlation per correlated EXISTS in *sql*.

    Returns (correlations, parsed_ok). A correlated EXISTS carries an equality
    ``inner.col = outer.col`` where exactly one side is a table inside the subquery.
    Never raises. parsed_ok=False signals a parse failure (→ caller CANNOT_VERIFY).
    """
    try:
        import sqlglot
        import sqlglot.expressions as exp

        tree = sqlglot.parse_one(sql, read="trino")
        if tree is None:
            return [], False
    except Exception:
        return [], False

    out: list[_Correlation] = []
    try:
        for exists_node in tree.find_all(exp.Exists):
            inner_select = exists_node.this
            if inner_select is None:
                continue
            real_names, ident_scope = _inner_identifiers(inner_select)
            where = inner_select.args.get("where")
            if where is None:
                continue
            inner_keys: set[str] = set()
            for eq in where.find_all(exp.EQ):
                lhs, rhs = eq.left, eq.right
                if not (isinstance(lhs, exp.Column) and isinstance(rhs, exp.Column)):
                    continue
                lhs_in = (lhs.table or "").lower() in ident_scope and bool(lhs.table)
                rhs_in = (rhs.table or "").lower() in ident_scope and bool(rhs.table)
                # Exactly one side inner-scoped → correlation predicate.
                if lhs_in and not rhs_in:
                    inner_keys.add(lhs.name.lower())
                elif rhs_in and not lhs_in:
                    inner_keys.add(rhs.name.lower())
            if inner_keys:
                out.append(_Correlation(frozenset(inner_keys), frozenset(real_names)))
    except Exception:
        return [], False
    return out, True


def _inner_identifiers(inner_select) -> tuple[set[str], set[str]]:
    """Return (real_table_names, scope_identifiers) for an EXISTS body.

    real_table_names: lowercased real table names (used to match the pre-agg source).
    scope_identifiers: real names PLUS aliases (used to classify a column as inner-scoped,
    since real queries qualify by alias: ``FROM spec s WHERE s.fk = ...``).
    Never raises.
    """
    import sqlglot.expressions as exp

    real: set[str] = set()
    scope: set[str] = set()
    try:
        nodes = []
        from_node = inner_select.args.get("from_")
        if from_node is not None:
            nodes.append(from_node)
        nodes.extend(inner_select.args.get("joins") or [])
        for node in nodes:
            for tbl in node.find_all(exp.Table):
                if tbl.name:
                    real.add(str(tbl.name).lower())
                    scope.add(str(tbl.name).lower())
                if tbl.alias:
                    scope.add(str(tbl.alias).lower())
    except Exception:
        pass
    return real, scope


# ---------------------------------------------------------------------------
# Pre-aggregation CTE binding in the REWRITTEN query
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class _PreaggCte:
    name: str                  # CTE name (lowercased)
    group_keys: frozenset[str] # GROUP BY column names (bare columns only)
    from_tables: frozenset[str]  # real table names in the CTE's FROM/JOINs
    expr_group: bool           # True if the GROUP BY has a non-column expression (→ unmatchable, fail-safe)


def _preagg_ctes(tree) -> dict[str, _PreaggCte]:
    """Map CTE name → _PreaggCte for every CTE that has a GROUP BY. Never raises."""
    import sqlglot.expressions as exp

    out: dict[str, _PreaggCte] = {}
    try:
        for cte in tree.find_all(exp.CTE):
            select = cte.this
            if select is None:
                continue
            group = select.args.get("group")
            if group is None:
                continue
            keys: set[str] = set()
            expr_group = False
            for child in group.expressions if hasattr(group, "expressions") else []:
                if isinstance(child, exp.Column):
                    keys.add(child.name.lower())
                else:
                    expr_group = True  # GROUP BY <expr> we can't name-match → fail-safe flag
            # real source tables of the CTE
            tables: set[str] = set()
            for tbl in select.find_all(exp.Table):
                if tbl.name:
                    tables.add(str(tbl.name).lower())
            name = (cte.alias or "").lower()
            if name:
                out[name] = _PreaggCte(name, frozenset(keys), frozenset(tables), expr_group)
    except Exception:
        return {}
    return out


# Comparison operators that, in a join ON, can match more than one CTE row per base
# row even when the CTE is unique on its key → fan-out.
def _NON_EQUI_TYPES():
    import sqlglot.expressions as exp
    return (exp.GT, exp.LT, exp.GTE, exp.LTE, exp.NEQ, exp.Between, exp.Like, exp.ILike)


@dataclass(frozen=True)
class _JoinToCte:
    cte_name: str
    agg_keys: frozenset[str]  # CTE-side equi-join column names
    fanning: bool             # True if the ON has a non-equi predicate (range/like/etc.)


def _joins_to_ctes(tree, cte_names: set[str]) -> list[_JoinToCte]:
    """Every join (any side) whose target is one of *cte_names*, with its agg-side equi
    keys and whether it carries a fan-out-capable non-equi predicate. Never raises."""
    import sqlglot.expressions as exp

    results: list[_JoinToCte] = []
    try:
        non_equi = _NON_EQUI_TYPES()
        for join in tree.find_all(exp.Join):
            target = join.this
            if not isinstance(target, exp.Table):
                continue
            tgt_name = (target.name or "").lower()
            tgt_alias = (target.alias or "").lower()
            if tgt_name not in cte_names:
                continue
            target_quals = {q for q in (tgt_alias, tgt_name) if q}
            on = join.args.get("on")
            agg_keys: set[str] = set()
            fanning = False
            if on is not None:
                for eq in on.find_all(exp.EQ):
                    for side in (eq.left, eq.right):
                        if isinstance(side, exp.Column) and (side.table or "").lower() in target_quals:
                            agg_keys.add(side.name.lower())
                if any(on.find_all(*non_equi)):
                    fanning = True
            else:
                fanning = True  # no ON (cross-like) → cannot bound
            results.append(_JoinToCte(tgt_name, frozenset(agg_keys), fanning))
    except Exception:
        return []
    return results


def _inner_tables_outside_preagg(tree, preagg_cte_names: set[str], inner_tables: set[str]) -> set[str]:
    """Original inner correlation tables that appear in the rewrite OUTSIDE a pre-agg CTE
    body — i.e. as a raw FROM/JOIN relation that can fan out. Never raises.

    A former correlated-subquery table is only safe if it lives inside its pre-aggregation;
    a plain JOIN to it in the main body (the partial-decorrelation trap) re-introduces fan-out.
    """
    import sqlglot.expressions as exp

    try:
        # node ids of every Table inside a pre-agg CTE subtree (legitimate home)
        inside_ids: set[int] = set()
        for cte in tree.find_all(exp.CTE):
            if (cte.alias or "").lower() not in preagg_cte_names:
                continue
            for tbl in cte.find_all(exp.Table):
                inside_ids.add(id(tbl))
        offenders: set[str] = set()
        for tbl in tree.find_all(exp.Table):
            name = (tbl.name or "").lower()
            if name in inner_tables and id(tbl) not in inside_ids:
                offenders.add(name)
        return offenders
    except Exception:
        # fail-safe: report "cannot tell" by flagging all inner tables as offenders
        return set(inner_tables)


def _main_body_closed(tree, preagg_names: set[str]) -> tuple[bool, str]:
    """True iff the outermost query body multiplies no rows beyond vetted pre-agg CTE joins.

    Sound, conservative closure for PROVEN: the final SELECT must be a real base table
    + JOINs that each target a vetted pre-aggregation CTE. Any other relation — a raw
    dimension join, comma/cross join, a non-pre-agg CTE, a subquery in FROM, or a base
    that is itself a CTE (whose body we do NOT recurse offline) — makes the row count
    unprovable → not closed → caller degrades to CANNOT_VERIFY. Under-claims for nested
    shapes by design; never lets a row-multiplying construct through. Never raises.
    """
    import sqlglot.expressions as exp

    try:
        if not isinstance(tree, exp.Select):
            return False, "outermost query is not a single SELECT (set-op / subquery)"
        all_cte_names = {(c.alias or "").lower() for c in tree.find_all(exp.CTE)}
        from_ = tree.args.get("from_")
        if from_ is None or not isinstance(from_.this, exp.Table):
            return False, "base relation is not a plain table (subquery / missing FROM)"
        if (from_.this.name or "").lower() in all_cte_names:
            return False, "base relation is a CTE (nested shape not recursed offline)"
        for join in (tree.args.get("joins") or []):
            target = join.this
            if not isinstance(target, exp.Table) or (target.name or "").lower() not in preagg_names:
                return False, ("a main-body join targets a non-pre-aggregation relation "
                               "(raw table / cross / comma join)")
            # The whole proof rests on base-row-preserving LEFT JOIN semantics. RIGHT/FULL
            # add unmatched CTE-side rows; INNER drops non-matching base rows — both change
            # the row set the base-driven original produces. Only LEFT reaches PROVEN.
            if (join.side or "").upper() != "LEFT":
                return False, ("a main-body join to a pre-aggregation is not a LEFT JOIN "
                               "(RIGHT/FULL add rows, INNER drops rows)")
        return True, "base + LEFT joins to vetted pre-agg CTEs only"
    except Exception:
        return False, "could not analyse main-body closure"


# ---------------------------------------------------------------------------
# P9 fan-out structural check
# ---------------------------------------------------------------------------

def verify_p9_fanout(original_sql: str, rewritten_sql: str) -> FanoutResult:
    """Offline structural check: did *rewritten_sql* decorrelate a correlated EXISTS in
    *original_sql* into a fan-out-safe pre-aggregated LEFT JOIN (P9)?

    PROVEN_NO_FANOUT requires a fully bound chain for at least one original correlation:
      a CTE C that (a) sources the EXISTS inner table, (b) GROUP BYs by the correlation
      key, (c) is the target of a LEFT JOIN whose ON ties C back on the correlation key,
      and (d) has GROUP BY grain ⊆ that join key (no finer → unique per join key).
    AND no correlated EXISTS may remain anywhere in the rewrite.

    Proves row-COUNT safety only. Row-VALUE equivalence (the post-join predicate
    reproducing the EXISTS truth value; the key being unique upstream) is data/semantic
    and belongs on the checklist. Any broken link → KEY_MISMATCH or CANNOT_VERIFY.
    """
    if not original_sql or not rewritten_sql:
        return FanoutResult(FanoutVerdict.CANNOT_VERIFY, "missing SQL input", (), ())

    corrs, ok = _correlations(original_sql)
    if not ok:
        return FanoutResult(FanoutVerdict.CANNOT_VERIFY, "could not parse original query", (), ())
    if not corrs:
        return FanoutResult(
            FanoutVerdict.NOT_APPLIED,
            "original query has no correlated EXISTS — P9 does not apply", (), (),
        )

    all_corr_keys = tuple(sorted({k for c in corrs for k in c.inner_keys}))

    rewritten_corrs, rok = _correlations(rewritten_sql)
    if not rok:
        return FanoutResult(FanoutVerdict.CANNOT_VERIFY, "could not parse rewritten query",
                            all_corr_keys, ())
    if rewritten_corrs:
        return FanoutResult(
            FanoutVerdict.NOT_APPLIED,
            "rewrite still contains a correlated EXISTS — not decorrelated (conservative)",
            all_corr_keys, (),
        )

    try:
        import sqlglot
        tree = sqlglot.parse_one(rewritten_sql, read="trino")
        if tree is None:
            return FanoutResult(FanoutVerdict.CANNOT_VERIFY, "rewrite did not parse",
                                all_corr_keys, ())
    except Exception:
        return FanoutResult(FanoutVerdict.CANNOT_VERIFY, "rewrite did not parse", all_corr_keys, ())

    ctes = _preagg_ctes(tree)
    if not ctes:
        return FanoutResult(
            FanoutVerdict.CANNOT_VERIFY,
            "correlated EXISTS was removed but no pre-aggregation CTE found — "
            "decorrelation shape not recognisable offline; verify manually",
            all_corr_keys, (),
        )

    joins = _joins_to_ctes(tree, set(ctes.keys()))
    # index joins by CTE so the proof can reason about EVERY join to a bound CTE
    joins_by_cte: dict[str, list[_JoinToCte]] = {}
    for j in joins:
        joins_by_cte.setdefault(j.cte_name, []).append(j)

    # GLOBAL CHECK 1: a former inner table joined raw in the main body (partial
    # decorrelation — one EXISTS pre-aggregated, another turned into a fan-out JOIN).
    all_inner_tables = {t for c in corrs for t in c.inner_tables}
    offenders = _inner_tables_outside_preagg(tree, set(ctes.keys()), all_inner_tables)
    if offenders:
        return FanoutResult(
            FanoutVerdict.CANNOT_VERIFY,
            f"former correlated table(s) {sorted(offenders)} appear as a raw JOIN/FROM outside "
            f"a pre-aggregation — partial decorrelation can fan out; verify manually",
            all_corr_keys, (),
        )

    # GLOBAL CHECK 2: EVERY join to ANY pre-aggregation CTE must be an equi-join whose
    # keys COVER that CTE's GROUP BY grain — else the CTE is not unique on that join key
    # and fans out. Run independent of which correlation binds, so a fanning join on a
    # second CTE (range) or a non-key join to the bound CTE cannot slip past a clean bind.
    for j in joins:
        cte = ctes[j.cte_name]
        if j.fanning:
            # non-equi (range/LIKE/...) or ON-less join → can match many CTE rows per base row.
            # Checked FIRST, before grain-nameability, so an expr-group CTE cannot buy a free
            # pass by being unprovable (round-4 C4).
            return FanoutResult(
                FanoutVerdict.CANNOT_VERIFY,
                f"a join to pre-aggregation CTE '{j.cte_name}' is non-equi (range/LIKE/etc.) "
                f"→ can match many rows per base row → fan-out; verify manually",
                all_corr_keys, (),
            )
        if cte.expr_group or not cte.group_keys:
            # grain unprovable (expression GROUP BY / no key) → cannot prove the equi join is
            # unique per key → CANNOT_VERIFY. An unprovable-grain CTE that is joined is more
            # dangerous, not safe; never skip it.
            return FanoutResult(
                FanoutVerdict.CANNOT_VERIFY,
                f"a join to pre-aggregation CTE '{j.cte_name}' whose GROUP BY grain is an "
                f"expression / unnameable → cannot prove uniqueness per join key; verify manually",
                all_corr_keys, (),
            )
        if not (cte.group_keys <= j.agg_keys):
            # equi join, but the CTE is grouped finer than the join key (or joined on a
            # non-group column) → not unique per join key → fan-out. A fixable wrong key.
            return FanoutResult(
                FanoutVerdict.KEY_MISMATCH,
                f"a join to pre-aggregation CTE '{j.cte_name}' keys on {sorted(j.agg_keys)} "
                f"which does not cover its GROUP BY grain {sorted(cte.group_keys)} → not unique "
                f"per join key → fan-out; fix the join/GROUP BY key",
                all_corr_keys, tuple(sorted(cte.group_keys)),
            )

    # Bind EVERY correlation to a safe pre-agg CTE chain; PROVEN only if all bind.
    saw_key_mismatch = False
    mismatch_detail = ""
    mismatch_gb: tuple[str, ...] = ()
    bound = 0
    proven_detail = ""
    proven_gb: tuple[str, ...] = ()
    # By here, GLOBAL CHECK 2 has guaranteed that EVERY join to a nameable-grain pre-agg
    # CTE is equi and key-covering (unique per join key). The bind loop only needs to find,
    # per correlation, a CTE that sources its inner table and is joined back on its key.
    for corr in corrs:
        corr_bound = False
        for cte_name, cte in ctes.items():
            if not (cte.from_tables & corr.inner_tables):
                continue  # this CTE doesn't pre-aggregate the EXISTS inner table
            cte_joins = joins_by_cte.get(cte_name, [])
            # the join(s) that tie this CTE back on the correlation key
            keyed_joins = [j for j in cte_joins if j.agg_keys & corr.inner_keys]
            if not keyed_joins:
                continue  # not joined back on the correlation key
            if cte.expr_group or not cte.group_keys:
                saw_key_mismatch = True
                mismatch_detail = (
                    f"CTE '{cte_name}' is joined on the correlation key but its GROUP BY is "
                    f"empty or expression-based — grain unprovable offline"
                )
                mismatch_gb = tuple(sorted(cte.group_keys))
                continue
            corr_bound = True
            proven_detail = (
                f"every correlated EXISTS is decorrelated into a pre-aggregation grouped by "
                f"its key and equi-joined back (grain ⊆ join key) → one row per join key by "
                f"construction → no fan-out"
            )
            proven_gb = tuple(sorted(cte.group_keys))
            break
        if corr_bound:
            bound += 1

    if bound == len(corrs) and bound > 0:
        # COMPLETENESS CLOSURE: every correlation is decorrelated, but PROVEN also requires
        # the main body to introduce no OTHER row-multiplying construct (raw dimension join,
        # comma/cross join, nested base CTE). Otherwise the fan-out could come from outside
        # the decorrelation we proved.
        closed, reason = _main_body_closed(tree, set(ctes.keys()))
        if not closed:
            return FanoutResult(
                FanoutVerdict.CANNOT_VERIFY,
                f"every correlation binds to a pre-aggregation, but the main query body is not "
                f"fully closed ({reason}) → total row count unprovable offline; verify manually",
                all_corr_keys, proven_gb,
            )
        return FanoutResult(FanoutVerdict.PROVEN_NO_FANOUT, proven_detail, all_corr_keys, proven_gb)

    if saw_key_mismatch:
        return FanoutResult(FanoutVerdict.KEY_MISMATCH, mismatch_detail, all_corr_keys, mismatch_gb)

    return FanoutResult(
        FanoutVerdict.CANNOT_VERIFY,
        "the correlated EXISTS was removed but not every correlation could be bound to a "
        "pre-aggregation joined on its key — decorrelation shape unclear; verify manually",
        all_corr_keys, (),
    )


# ---------------------------------------------------------------------------
# Per-strategy verification checklists
# ---------------------------------------------------------------------------

# Concrete, data/semantic verify items a reviewer must confirm before applying an
# UNVERIFIED advisory rewrite. Keyed by P-strategy id; only the TRAP/DANGEROUS
# strategies (which can change output) carry a checklist — SAFE strategies don't.
_STRATEGY_CHECKLISTS: dict[str, tuple[str, ...]] = {
    "P9": (
        "GROUP BY key in the pre-aggregation CTE equals the EXISTS correlation key.",
        "The aggregated column is COALESCEd on no-match (NULL → ARRAY[] / default) so "
        "the LEFT JOIN reproduces EXISTS=false instead of dropping/changing the row.",
        "The post-join predicate (contains / arrays_overlap / comparison) reproduces the "
        "original EXISTS truth value — including NULL semantics.",
        "The pre-aggregation sources the SAME inner table as the original EXISTS (not a "
        "look-alike) and drops no predicate from the EXISTS body.",
        "The correlation key is genuinely the join grain upstream (data-dependent — "
        "confirm with the table owner or a count when a cluster is available).",
    ),
    "P2": (
        "The joined key is provably UNIQUE on the joined side (1-to-1); if 1-to-many, "
        "P2 fans out and P9 should be used instead.",
        "No downstream MAX/SUM/COUNT/LISTAGG depends on the original (un-fanned) row count.",
    ),
    "P3": (
        "Substring match (LIKE '%v%') vs exact-token match (contains/split) have DIFFERENT "
        "semantics — confirm the column is truly a delimited token list, not free text.",
    ),
    "P4": (
        "slice(...,1,N) truncates groups with > N elements — confirm no group legitimately "
        "exceeds N, or the truncation is acceptable.",
    ),
    "P5": (
        "If the predicate was pushed across the null-producing side of an OUTER join, the "
        "result changes — confirm it was only pushed into inner-join / base-table scans.",
    ),
    "P6": (
        "Lambda (transform/filter/reduce) null handling, empty-collection behaviour, and "
        "element ordering match the original per-row expansion.",
    ),
}


def strategy_checklist(strategy_id: str) -> tuple[str, ...]:
    """Return the verification checklist for a strategy id, or () if none (SAFE strategies)."""
    return _STRATEGY_CHECKLISTS.get(strategy_id, ())


# ---------------------------------------------------------------------------
# Markdown rendering for the advisory report
# ---------------------------------------------------------------------------

_VERDICT_HEADLINE: dict[FanoutVerdict, str] = {
    FanoutVerdict.PROVEN_NO_FANOUT:
        "✓ Row-COUNT safety PROVEN (offline, structural) — row-VALUE equivalence is NOT "
        "verified. The decorrelation pre-aggregates the inner table by the correlation key "
        "and joins it back on that key, so the LEFT JOIN cannot multiply rows. You MUST still "
        "confirm the value-level checklist below before applying.",
    FanoutVerdict.KEY_MISMATCH:
        "✗ LIKELY WRONG: a pre-aggregation is joined back but on the wrong grain (it groups "
        "finer than the join key, or by no nameable key) — this fans out or is unprovable. "
        "Do not apply without fixing the GROUP BY / join key.",
    FanoutVerdict.NOT_APPLIED:
        "— Decorrelation NOT applied: the correlated EXISTS was left in place "
        "(conservative). No fan-out risk introduced; no order-of-magnitude win either.",
    FanoutVerdict.CANNOT_VERIFY:
        "? CANNOT VERIFY offline: the rewrite changed the correlated EXISTS into a shape "
        "this checker cannot bind to a pre-agg → LEFT JOIN → key chain. Treat as fully "
        "UNVERIFIED and review manually.",
}


def render_advisory_verification(original_sql: str, rewritten_sql: str) -> list[str]:
    """Render the v56 offline verification section for a P9 (decorrelation) candidate.

    Returns [] when the original query has no correlated EXISTS (nothing to verify here).
    Otherwise returns a markdown block: the structural fan-out verdict + the P9 checklist.
    """
    corrs, _ = _correlations(original_sql or "")
    if not corrs:
        return []

    result = verify_p9_fanout(original_sql, rewritten_sql or original_sql)
    lines = [
        "### Offline strategy verification — P9 exists-to-preagg-join",
        "",
        "_Pure-AST checks only (no cluster / EXPLAIN / data). Proves row-COUNT safety "
        "at most; row-VALUE equivalence stays UNVERIFIED._",
        "",
        _VERDICT_HEADLINE.get(result.verdict, result.verdict.value),
        "",
        f"Detail: {result.detail}",
        "",
    ]
    checklist = strategy_checklist("P9")
    if checklist:
        lines.append("Before applying, a reviewer must confirm:")
        lines += [f"- [ ] {item}" for item in checklist]
        lines.append("")
    return lines


__all__ = [
    "FanoutVerdict",
    "FanoutResult",
    "verify_p9_fanout",
    "strategy_checklist",
    "render_advisory_verification",
]
