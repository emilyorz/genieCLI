"""RewritePlan builder (Stage-3 artifact, stepwise — not EXECUTE_ALL).

Consumes engine PHit list → ordered plan with execute vs advise_only.
DANGEROUS hits are never action=execute.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, List

from genie.skills.mcp_trino.phit_scan import PHit
from genie.skills.mcp_trino.p_strategies import ALL_P_STRATEGY_IDS, DANGEROUS, TIER_ACTION


class RewritePlanError(ValueError):
    """Invalid plan construction."""


@dataclass(frozen=True)
class RewriteStep:
    seq: int
    pid: str
    tier: str
    targets: List[str]
    action: str  # execute | advise_only


@dataclass(frozen=True)
class RewritePlan:
    schema: str = "genie-rewrite-plan-v1"
    steps: List[RewriteStep] = field(default_factory=list)


def _norm_tier(tier: str) -> str:
    return str(tier or "").strip().lower()


def _action_for_tier(tier: str) -> str:
    t = _norm_tier(tier)
    # Catalog uses SAFE/TRAP/DANGEROUS constants; TIER_ACTION maps to rewrite|advise
    try:
        from genie.skills.mcp_trino import p_strategies as ps
        # map lowercase to constant
        key = {
            "safe": ps.SAFE,
            "trap": ps.TRAP,
            "dangerous": ps.DANGEROUS,
        }.get(t)
        if key is not None:
            act = TIER_ACTION.get(key)
            if act == "advise":
                return "advise_only"
            return "execute"
    except Exception:
        pass
    if t == "dangerous":
        return "advise_only"
    return "execute"


def build_rewrite_plan(hits: Iterable[PHit]) -> RewritePlan:
    """Build a stepwise plan from P-hits.

    - unknown pid → RewritePlanError
    - dangerous → advise_only (hard)
    - sort: safe execute, trap execute, then advise_only
    - dedupe pid+node_ref
    """
    prepared: list[tuple[PHit, str]] = []
    seen: set[tuple[str, str]] = set()
    for h in hits:
        if h.pid not in ALL_P_STRATEGY_IDS:
            raise RewritePlanError(f"unknown catalog pid: {h.pid!r}")
        if h.pid == "P10":
            # deferred: reject if ever slipped in
            raise RewritePlanError("P10 deferred this run; remove hit")
        key = (h.pid, h.node_ref)
        if key in seen:
            continue
        seen.add(key)
        action = _action_for_tier(h.tier)
        # belt-and-suspenders: dangerous never execute
        if _norm_tier(h.tier) == "dangerous" or str(h.tier).upper() == DANGEROUS:
            action = "advise_only"
        if action == "execute" and _norm_tier(h.tier) == "dangerous":
            raise RewritePlanError(f"refusing execute for dangerous hit {h.pid}")
        prepared.append((h, action))

    def sort_key(item: tuple[PHit, str]) -> tuple[int, int, str]:
        h, action = item
        tier = _norm_tier(h.tier)
        # execute safes, execute traps, advise
        if action == "execute" and tier == "safe":
            bucket = 0
        elif action == "execute" and tier == "trap":
            bucket = 1
        else:
            bucket = 2
        return (bucket, 0, h.pid + h.node_ref)

    prepared.sort(key=sort_key)
    steps: list[RewriteStep] = []
    for i, (h, action) in enumerate(prepared, start=1):
        steps.append(
            RewriteStep(
                seq=i,
                pid=h.pid,
                tier=_norm_tier(h.tier),
                targets=[h.node_ref],
                action=action,
            )
        )
    return RewritePlan(steps=steps)


def assert_no_dangerous_execute(plan: RewritePlan) -> None:
    for s in plan.steps:
        if s.action == "execute" and s.tier == "dangerous":
            raise RewritePlanError(f"dangerous execute forbidden: step {s.seq} {s.pid}")


def format_plan_markdown(plan: RewritePlan) -> str:
    if not plan.steps:
        return "_Empty rewrite plan._\n"
    lines = [
        f"_schema: `{plan.schema}`_",
        "",
        "| seq | pid | tier | action | targets |",
        "|-----|-----|------|--------|---------|",
    ]
    for s in plan.steps:
        tgt = ", ".join(f"`{t}`" for t in s.targets)
        lines.append(f"| {s.seq} | {s.pid} | {s.tier} | {s.action} | {tgt} |")
    lines.append("")
    lines.append(
        "_Execute steps one-at-a-time through the existing research loop gates. "
        "advise_only is not an optimization candidate. No EXECUTE_ALL._"
    )
    return "\n".join(lines) + "\n"


__all__ = [
    "RewritePlanError",
    "RewriteStep",
    "RewritePlan",
    "build_rewrite_plan",
    "assert_no_dangerous_execute",
    "format_plan_markdown",
]
