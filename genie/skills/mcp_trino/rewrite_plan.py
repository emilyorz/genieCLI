"""RewritePlan builder (Stage-3 artifact, stepwise — not EXECUTE_ALL).

Consumes engine PHit list → ordered plan with execute vs advise_only.
DANGEROUS hits are never action=execute.

Schema:
- v1: RewriteStep (seq, pid, tier, targets, action) — backward compatible
- v2: PlanStepV2 atomic (rule_id, site) with depends_on / verify / anchors
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, List, Optional, Sequence

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


@dataclass
class PlanStepV2:
    """Atomic step: one (rule_id, site) application."""

    step_id: str
    rule_id: str
    site: dict[str, Any]
    tier: str
    action: str  # execute | advise_only
    verify: str = "STATIC"
    depends_on: list[str] = field(default_factory=list)
    before_fragment: str = ""
    after_fragment: str = ""
    rationale: str = ""
    line_hint: Optional[int] = None


@dataclass
class RewritePlanV2:
    plan_schema: str = "v2"
    steps: List[PlanStepV2] = field(default_factory=list)

    # compat alias used by some callers
    @property
    def schema(self) -> str:
        return f"genie-rewrite-plan-{self.plan_schema}"


def _norm_tier(tier: str) -> str:
    return str(tier or "").strip().lower()


def _action_for_tier(tier: str) -> str:
    t = _norm_tier(tier)
    try:
        from genie.skills.mcp_trino import p_strategies as ps

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


def _default_verify_for(rule_id: str) -> str:
    try:
        from genie.skills.mcp_trino.p_strategies import RULE_META

        meta = RULE_META.get(rule_id)
        if meta is not None:
            return str(meta.default_verify)
    except Exception:
        pass
    return "STATIC"


def _site_from_hit(h: PHit) -> dict[str, Any]:
    site: dict[str, Any] = {
        "anchor_path": h.node_ref,
        "node_ref": h.node_ref,
        "fragment_hash": None,
        "line_hint": h.span.line if h.span else None,
    }
    return site


def build_rewrite_plan(hits: Iterable[PHit]) -> RewritePlan:
    """Build a v1 stepwise plan from P-hits (backward compatible)."""
    prepared: list[tuple[PHit, str]] = []
    seen: set[tuple[str, str]] = set()
    for h in hits:
        if h.pid not in ALL_P_STRATEGY_IDS:
            raise RewritePlanError(f"unknown catalog pid: {h.pid!r}")
        key = (h.pid, h.node_ref)
        if key in seen:
            continue
        seen.add(key)
        action = _action_for_tier(h.tier)
        if _norm_tier(h.tier) == "dangerous" or str(h.tier).upper() == DANGEROUS:
            action = "advise_only"
        if action == "execute" and _norm_tier(h.tier) == "dangerous":
            raise RewritePlanError(f"refusing execute for dangerous hit {h.pid}")
        prepared.append((h, action))

    def sort_key(item: tuple[PHit, str]) -> tuple[int, int, str]:
        h, action = item
        tier = _norm_tier(h.tier)
        if action == "execute" and tier == "safe":
            bucket = 0
        elif action == "execute" and tier == "trap":
            bucket = 1
        else:
            bucket = 2
        # structural prefer among same bucket
        structural = 0 if h.pid in {"P2", "P9", "P10"} else 1
        return (bucket, structural, h.pid + h.node_ref)

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


def build_rewrite_plan_v2(hits: Iterable[PHit]) -> RewritePlanV2:
    """Build atomic v2 plan: one step per (rule_id, site)."""
    v1 = build_rewrite_plan(hits)
    return upgrade_v1_plan(v1, hits=list(hits))


def upgrade_v1_plan(
    plan: RewritePlan,
    *,
    hits: Optional[Sequence[PHit]] = None,
) -> RewritePlanV2:
    """Upgrade v1 plan to atomic v2 steps."""
    hit_by_ref: dict[str, PHit] = {}
    if hits:
        for h in hits:
            hit_by_ref[h.node_ref] = h

    # First expand targets to atomic steps
    raw: list[PlanStepV2] = []
    for s in plan.steps:
        targets = list(s.targets or []) or [f"site:{s.seq}"]
        for j, tgt in enumerate(targets):
            sid = f"S{s.seq}" if len(targets) == 1 else f"S{s.seq}_{j+1}"
            site = {"anchor_path": tgt, "node_ref": tgt, "fragment_hash": None, "line_hint": None}
            h = hit_by_ref.get(tgt)
            if h is not None and h.span is not None:
                site["line_hint"] = h.span.line
            raw.append(
                PlanStepV2(
                    step_id=sid,
                    rule_id=s.pid,
                    site=site,
                    tier=_norm_tier(s.tier),
                    action=s.action,
                    verify=_default_verify_for(s.pid),
                    depends_on=[],
                    rationale=f"from v1 step {s.seq}",
                    line_hint=site.get("line_hint"),
                )
            )

    split = split_composite_steps(RewritePlanV2(steps=raw))
    return split


def split_composite_steps(plan: RewritePlanV2) -> RewritePlanV2:
    """Ensure each step is one rule_id + one site; generate depends_on for same-site chains."""
    atomic: list[PlanStepV2] = []
    # group by site anchor to serialize multi-rule same site
    by_site: dict[str, list[PlanStepV2]] = {}
    for s in plan.steps:
        # explode if rule_id somehow composite (comma-separated) — defensive
        rule_ids = [r.strip() for r in str(s.rule_id).split(",") if r.strip()]
        sites = [s.site]
        # if targets sneaked in as multiple anchors in one site list
        if isinstance(s.site.get("anchors"), list):
            sites = [
                {
                    "anchor_path": a,
                    "node_ref": a,
                    "fragment_hash": s.site.get("fragment_hash"),
                    "line_hint": s.site.get("line_hint"),
                }
                for a in s.site["anchors"]
            ]
        idx = 0
        for rid in rule_ids:
            for site in sites:
                idx += 1
                step_id = s.step_id if (len(rule_ids) == 1 and len(sites) == 1) else f"{s.step_id}.{idx}"
                atomic.append(
                    PlanStepV2(
                        step_id=step_id,
                        rule_id=rid,
                        site=dict(site),
                        tier=_norm_tier(s.tier),
                        action=s.action,
                        verify=s.verify or _default_verify_for(rid),
                        depends_on=list(s.depends_on or []),
                        before_fragment=s.before_fragment,
                        after_fragment=s.after_fragment,
                        rationale=s.rationale,
                        line_hint=s.line_hint,
                    )
                )

    # same-site serial depends_on
    site_last: dict[str, str] = {}
    for step in atomic:
        anchor = str((step.site or {}).get("anchor_path") or step.step_id)
        if anchor in site_last:
            dep = site_last[anchor]
            if dep not in step.depends_on:
                step.depends_on = list(step.depends_on) + [dep]
        site_last[anchor] = step.step_id

    # re-number stable step_ids if duplicates
    seen_ids: set[str] = set()
    final: list[PlanStepV2] = []
    for i, step in enumerate(atomic, start=1):
        sid = step.step_id or f"S{i}"
        if sid in seen_ids:
            sid = f"S{i}"
        seen_ids.add(sid)
        step.step_id = sid
        final.append(step)
    return RewritePlanV2(steps=final)


def assert_no_dangerous_execute(plan: RewritePlan) -> None:
    for s in plan.steps:
        if s.action == "execute" and s.tier == "dangerous":
            raise RewritePlanError(f"dangerous execute forbidden: step {s.seq} {s.pid}")


def format_plan_markdown(plan: RewritePlan | RewritePlanV2) -> str:
    if isinstance(plan, RewritePlanV2):
        if not plan.steps:
            return "_Empty rewrite plan._\n"
        lines = [
            f"_schema: `{plan.schema}`_",
            "",
            "| step_id | rule_id | tier | action | verify | depends_on | site |",
            "|---------|---------|------|--------|--------|------------|------|",
        ]
        for s in plan.steps:
            dep = ",".join(s.depends_on) if s.depends_on else ""
            site = str((s.site or {}).get("anchor_path") or "")
            lines.append(
                f"| `{s.step_id}` | {s.rule_id} | {s.tier} | {s.action} | {s.verify} | {dep} | `{site}` |"
            )
        lines.append("")
        lines.append(
            "_Execute steps one-at-a-time (HYBRID_STEPWISE). "
            "advise_only is not an optimization candidate. No EXECUTE_ALL default. "
            "ONE change = one (rule_id, site) P-rule application._"
        )
        return "\n".join(lines) + "\n"

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
    "PlanStepV2",
    "RewritePlanV2",
    "build_rewrite_plan",
    "build_rewrite_plan_v2",
    "upgrade_v1_plan",
    "split_composite_steps",
    "assert_no_dangerous_execute",
    "format_plan_markdown",
]
