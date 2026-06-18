"""Contract tests for the P1–P8 rewrite-strategy menu (``p_strategies.py``).

These pin the manager's eight-strategy fix menu so it cannot silently drift or
lose a slot the way "P5 undefined" persisted from v37 to v47. They assert the
menu is complete (P1–P8, no gaps/dupes), every field is populated, every tier is
valid, and the tier→action / verify mapping holds. Mirrors the
``test_rule_id_contract.py`` completeness-gate pattern.
"""
from __future__ import annotations

import pytest

from genie.skills.mcp_trino.p_strategies import (
    ALL_P_STRATEGY_IDS,
    ALL_TIERS,
    DANGEROUS,
    P_STRATEGIES,
    P_STRATEGY_BY_ID,
    SAFE,
    TIER_ACTION,
    TIERS_REQUIRE_VERIFY,
    TRAP,
    PStrategy,
    render_menu,
    strategies_for_action,
)

_EXPECTED_IDS = frozenset(f"P{i}" for i in range(1, 10))


def test_menu_has_exactly_p1_through_p9():
    # No gap (the historical "P5 undefined" hole) and no extra/missing slot.
    assert ALL_P_STRATEGY_IDS == _EXPECTED_IDS
    assert len(P_STRATEGIES) == 9


def test_ids_are_unique():
    ids = [s.id for s in P_STRATEGIES]
    assert len(ids) == len(set(ids))


def test_p5_is_predicate_partition_pushdown():
    # P5 was the undefined slot; v47 fixed it. Lock the definition.
    assert P_STRATEGY_BY_ID["P5"].name == "predicate-partition-pushdown"


@pytest.mark.parametrize("strategy", P_STRATEGIES, ids=lambda s: s.id)
def test_every_field_is_populated(strategy: PStrategy):
    assert strategy.id and strategy.id.startswith("P")
    assert strategy.name and strategy.name == strategy.name.lower()
    assert strategy.trigger.strip()
    assert strategy.recipe.strip()
    assert strategy.safety_note.strip()


@pytest.mark.parametrize("strategy", P_STRATEGIES, ids=lambda s: s.id)
def test_tier_is_valid_and_mapped(strategy: PStrategy):
    assert strategy.tier in ALL_TIERS
    assert strategy.tier in TIER_ACTION
    assert TIER_ACTION[strategy.tier] in {"rewrite", "advise"}


def test_dangerous_strategies_are_advise_only():
    # Safety invariant: a DANGEROUS strategy must never map to an auto-apply action.
    for s in P_STRATEGIES:
        if s.tier == DANGEROUS:
            assert TIER_ACTION[s.tier] == "advise"


def test_trap_strategies_require_verification():
    # TRAP strategies rewrite, but only behind a row-equivalence check.
    for s in P_STRATEGIES:
        if s.tier == TRAP:
            assert s.tier in TIERS_REQUIRE_VERIFY
            assert TIER_ACTION[s.tier] == "rewrite"


def test_safe_strategies_do_not_require_verification():
    for s in P_STRATEGIES:
        if s.tier == SAFE:
            assert s.tier not in TIERS_REQUIRE_VERIFY


def test_strategies_for_action_partitions_the_menu():
    rewrite = strategies_for_action("rewrite")
    advise = strategies_for_action("advise")
    assert len(rewrite) + len(advise) == len(P_STRATEGIES)
    assert set(rewrite).isdisjoint(advise)


def test_known_tier_assignments():
    # Lock the Safe/Trap/Dangerous classification from the v37 tier table + P5.
    expected = {
        "P1": SAFE, "P2": TRAP, "P3": DANGEROUS, "P4": DANGEROUS,
        "P5": TRAP, "P6": DANGEROUS, "P7": SAFE, "P8": SAFE,
        "P9": TRAP,
    }
    actual = {s.id: s.tier for s in P_STRATEGIES}
    assert actual == expected


def test_render_menu_lists_all_nine_with_safety_flags():
    menu = render_menu()
    for i in range(1, 10):
        assert f"P{i}" in menu
    # Advise-only and verify flags must be visible to the optimizer LLM.
    assert "ADVISE ONLY" in menu
    assert "MUST verify row-equivalence" in menu
