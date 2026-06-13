"""Canonical P1–P8 rewrite-strategy reference — the optimization "fix menu".

Single source of truth for the manager's eight Trino-rewrite strategies. These
are the **fix menu** applied during the optimize step (how to rewrite a fragment
that detection flagged as a monster) — they are NOT a detection pass. Detection
is the R1–R10 sqlglot rules in ``genie/skills/trino_query/sql_static`` plus
cost/EXPLAIN; this module only answers "given a monster, which named, vetted
rewrite applies, and how safe is it to auto-apply?".

Safety tier gates how a strategy's rewrite is admitted (consumed by the optimize
step + the existing column/row-level semantic gates in ``write_analysis.py``):

  SAFE      — value-preserving by construction (pure relocation / plan-strategy
              change); admit and rely on the standard equivalence check.
  TRAP      — usually equivalent but carries a known correctness pitfall; admit
              ONLY with mandatory row-equivalence verification before apply.
  DANGEROUS — may change output semantics; surface as ADVISE only, never
              auto-applied.

History: P1–P8 were the manager's mental model, never codified in the repo;
"P5 undefined" was a recurring gap deferred since v37 (see STATUS.md / the v37
optimize-profile PLAN draft). v47 codifies all eight; P5 = predicate/partition
pushdown (Sam-confirmed 2026-06-13). Importing ``ALL_P_STRATEGY_IDS`` /
``P_STRATEGIES`` in producers and the contract test makes drift a test failure,
mirroring the ``rule_ids.py`` pattern.
"""
from __future__ import annotations

from dataclasses import dataclass

# --- Safety tiers -----------------------------------------------------------
SAFE = "safe"
TRAP = "trap"
DANGEROUS = "dangerous"

ALL_TIERS = frozenset({SAFE, TRAP, DANGEROUS})

# Tier → optimize-step action: SAFE/TRAP rewrite (TRAP needs verify), DANGEROUS advises only.
TIER_ACTION = {
    SAFE: "rewrite",
    TRAP: "rewrite",       # admitted only after row-equivalence verification
    DANGEROUS: "advise",   # never auto-applied
}
# Tiers whose rewrite MUST pass a row-equivalence check before being applied.
TIERS_REQUIRE_VERIFY = frozenset({TRAP})


@dataclass(frozen=True)
class PStrategy:
    """One named rewrite strategy from the P1–P8 menu."""

    id: str           # "P1".."P8"
    name: str         # stable kebab-case slug
    trigger: str      # the pattern/monster this strategy applies to
    recipe: str       # how to rewrite it
    tier: str         # SAFE | TRAP | DANGEROUS
    safety_note: str  # why this tier; the correctness pitfall if any


P_STRATEGIES: tuple[PStrategy, ...] = (
    PStrategy(
        id="P1",
        name="function-pushup",
        trigger="a function / cast / arithmetic wraps a column used in a JOIN ON key, "
                "WHERE predicate, or partition filter — killing sargability and "
                "partition/file pruning.",
        recipe="move the function off the column (apply it to the literal/other side, "
               "or precompute), leaving a bare column the engine can prune on.",
        tier=SAFE,
        safety_note="pure relocation of computation; the selected values are unchanged.",
    ),
    PStrategy(
        id="P2",
        name="exists-to-left-join",
        trigger="a correlated EXISTS subquery used to enrich/filter the outer row.",
        recipe="rewrite the correlated EXISTS as a LEFT JOIN to the same relation.",
        tier=TRAP,
        safety_note="EXISTS is a semi-join (does NOT multiply rows); a LEFT JOIN on a "
                    "1-to-many key fans out → duplicate base rows → downstream "
                    "MAX/SUM/COUNT/LISTAGG change. Safe ONLY when the join key is "
                    "provably unique on the joined side — must verify row-equivalence.",
    ),
    PStrategy(
        id="P3",
        name="like-to-contains",
        trigger="a `LIKE '%value%'` substring predicate on a delimited/token column.",
        recipe="rewrite to `contains(split(col, delim), 'value')` for token matching.",
        tier=DANGEROUS,
        safety_note="substring match (LIKE) and exact-token match (contains/split) have "
                    "DIFFERENT semantics — a substring inside a token is matched by LIKE "
                    "but not by contains. Advise only; never auto-apply.",
    ),
    PStrategy(
        id="P4",
        name="listagg-to-slice",
        trigger="a `LISTAGG` over a large group where only the first N elements are needed.",
        recipe="rewrite to `array_join(slice(array_agg(x), 1, N), delim)` to cap work.",
        tier=DANGEROUS,
        safety_note="`slice(...,1,N)` adds an element cap that TRUNCATES the result when a "
                    "group has > N elements — output differs from full LISTAGG. Advise only.",
    ),
    PStrategy(
        id="P5",
        name="predicate-partition-pushdown",
        trigger="a filter (WHERE / partition predicate) evaluated late — after joins or "
                "aggregations — so the scan reads far more rows/files/partitions than needed.",
        recipe="push the predicate down to the scan (or into the CTE/subquery that feeds "
               "the join) so partition/file pruning happens before the heavy work.",
        tier=TRAP,
        safety_note="value-preserving for inner joins and base-table filters, but pushing a "
                    "predicate across the null-producing side of an OUTER join changes "
                    "results. When an LLM relocates the filter, verify row-equivalence.",
    ),
    PStrategy(
        id="P6",
        name="lambda-rewrite",
        trigger="row-by-row array/map processing expressible as a higher-order "
                "lambda (transform/filter/reduce) over a collection column.",
        recipe="rewrite the per-row expansion as a lambda over the array/map.",
        tier=DANGEROUS,
        safety_note="a heavy structural rewrite; null handling, empty-collection behaviour "
                    "and element ordering are easy to change. Advise only.",
    ),
    PStrategy(
        id="P7",
        name="skinny-join",
        trigger="a join whose inputs carry columns that are never used downstream, "
                "widening the shuffle/build side.",
        recipe="project only the join keys + columns actually needed BEFORE the join "
               "(wrap each side in a narrowing subquery).",
        tier=SAFE,
        safety_note="pure projection reduction; the values of the retained columns and the "
                    "join result are unchanged.",
    ),
    PStrategy(
        id="P8",
        name="broadcast-hint",
        trigger="a join with a small build side that the planner distributes (PARTITIONED) "
                "instead of broadcasting.",
        recipe="add a broadcast join-distribution hint so the small side is replicated to "
               "every worker, avoiding a large shuffle.",
        tier=SAFE,
        safety_note="an execution-strategy change only; the join result values are unchanged. "
                    "(Cost regresses if the build side is mis-estimated, but never wrong.)",
    ),
)

ALL_P_STRATEGY_IDS: frozenset[str] = frozenset(s.id for s in P_STRATEGIES)

# id → strategy, for producer lookup.
P_STRATEGY_BY_ID: dict[str, PStrategy] = {s.id: s for s in P_STRATEGIES}


def strategies_for_action(action: str) -> tuple[PStrategy, ...]:
    """Return strategies whose tier maps to the given optimize-step action ('rewrite'/'advise')."""
    return tuple(s for s in P_STRATEGIES if TIER_ACTION[s.tier] == action)


def render_menu() -> str:
    """Render the P1–P8 menu as a compact prompt block for the optimize step."""
    lines = ["Rewrite strategy menu (apply ONLY a named strategy that fits; do not freestyle):"]
    for s in P_STRATEGIES:
        verify = " [MUST verify row-equivalence]" if s.tier in TIERS_REQUIRE_VERIFY else ""
        advise = " [ADVISE ONLY — do not auto-apply]" if TIER_ACTION[s.tier] == "advise" else ""
        lines.append(
            f"- {s.id} {s.name} ({s.tier}{verify}{advise}): {s.recipe} "
            f"Trigger: {s.trigger}"
        )
    return "\n".join(lines)


__all__ = [
    "SAFE",
    "TRAP",
    "DANGEROUS",
    "ALL_TIERS",
    "TIER_ACTION",
    "TIERS_REQUIRE_VERIFY",
    "PStrategy",
    "P_STRATEGIES",
    "ALL_P_STRATEGY_IDS",
    "P_STRATEGY_BY_ID",
    "strategies_for_action",
    "render_menu",
]
