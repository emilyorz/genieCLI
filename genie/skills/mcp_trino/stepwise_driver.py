"""HYBRID_STEPWISE driver: apply one P-rule site at a time with ledger.

Offline-first: without Trino MCP, verify degrades to STATIC and marks UNVERIFIED.
Does NOT default to EXECUTE_ALL. Dangerous rules require confirm_dangerous.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Optional, Sequence

from genie.skills.mcp_trino.rewrite_plan import (
    PlanStepV2,
    RewritePlan,
    RewritePlanV2,
    RewriteStep,
    upgrade_v1_plan,
)


class StepStatus(str, Enum):
    APPLIED = "APPLIED"
    REJECTED = "REJECTED"
    SKIPPED = "SKIPPED"
    NEEDS_HUMAN = "NEEDS_HUMAN"


class ReasonCode(str, Enum):
    PARSE_FAIL = "PARSE_FAIL"
    SHAPE_MISMATCH = "SHAPE_MISMATCH"
    EXPLAIN_REGRESSION = "EXPLAIN_REGRESSION"
    ROWCOUNT_DIFF = "ROWCOUNT_DIFF"
    REANCHOR_FAIL = "REANCHOR_FAIL"
    DANGEROUS_UNCONFIRMED = "DANGEROUS_UNCONFIRMED"
    TIMEOUT = "TIMEOUT"
    APPLY_FAIL = "APPLY_FAIL"
    ADVISE_ONLY = "ADVISE_ONLY"
    DEPENDENCY_BLOCKED = "DEPENDENCY_BLOCKED"
    OK = "OK"
    STATIC_OK = "STATIC_OK"


class VerifyLevel(str, Enum):
    EXACT = "EXACT"
    ROW_COUNT = "ROW_COUNT"
    EXPLAIN_ONLY = "EXPLAIN_ONLY"
    STATIC = "STATIC"


ApplyFn = Callable[[str, PlanStepV2], Optional[str]]
LiveVerifyFn = Callable[[str, str, PlanStepV2], tuple[bool, str, str]]


@dataclass
class StepRecord:
    step_id: str
    rule_id: str
    site_anchor: str
    status: StepStatus
    verify_level: VerifyLevel
    reason_code: ReasonCode
    unverified: bool = False
    before_sql: str = ""
    after_sql: str = ""
    detail: str = ""
    depends_on: list[str] = field(default_factory=list)

    def rejection_card(self, *, diff_lines: int = 12) -> str:
        lines = [
            f"### Rejection card — `{self.step_id}` ({self.rule_id})",
            "",
            f"- **status:** `{self.status.value}`",
            f"- **site:** `{self.site_anchor}`",
            f"- **reason_code:** `{self.reason_code.value}`",
            f"- **verify:** `{self.verify_level.value}`"
            + (" · **UNVERIFIED**" if self.unverified else ""),
            f"- **detail:** {self.detail or '_none_'}",
            f"- **next:** human confirm (`confirm_dangerous=True`) or skip",
            "",
        ]
        if self.before_sql or self.after_sql:
            before = (self.before_sql or "").splitlines()
            after = (self.after_sql or "").splitlines()
            lines.append("```diff")
            for ln in before[:diff_lines]:
                lines.append(f"- {ln}")
            if len(before) > diff_lines:
                lines.append(f"- … ({len(before) - diff_lines} more)")
            for ln in after[:diff_lines]:
                lines.append(f"+ {ln}")
            if len(after) > diff_lines:
                lines.append(f"+ … ({len(after) - diff_lines} more)")
            lines.append("```")
            lines.append("")
        return "\n".join(lines)


@dataclass
class StepLedger:
    records: list[StepRecord] = field(default_factory=list)
    mode: str = "HYBRID_STEPWISE"
    final_sql: str = ""
    base_sql: str = ""

    def add(self, rec: StepRecord) -> None:
        self.records.append(rec)

    def to_markdown(self) -> str:
        lines = [
            f"_mode: `{self.mode}`_",
            "",
            "| step_id | rule_id | status | verify | reason | UNVERIFIED | site |",
            "|---------|---------|--------|--------|--------|------------|------|",
        ]
        for r in self.records:
            uv = "yes" if r.unverified else ""
            site = r.site_anchor.replace("|", "\\|")
            lines.append(
                f"| `{r.step_id}` | {r.rule_id} | {r.status.value} | "
                f"{r.verify_level.value} | {r.reason_code.value} | {uv} | `{site}` |"
            )
        lines.append("")
        applied = sum(1 for r in self.records if r.status == StepStatus.APPLIED)
        rejected = sum(1 for r in self.records if r.status == StepStatus.REJECTED)
        needs = sum(1 for r in self.records if r.status == StepStatus.NEEDS_HUMAN)
        skipped = sum(1 for r in self.records if r.status == StepStatus.SKIPPED)
        lines.append(
            f"_ledger summary: applied={applied} rejected={rejected} "
            f"needs_human={needs} skipped={skipped}. "
            f"STATIC-only rows are UNVERIFIED (not semantic proof). "
            f"No EXECUTE_ALL default._"
        )
        lines.append("")
        for r in self.records:
            if r.status in {StepStatus.REJECTED, StepStatus.NEEDS_HUMAN}:
                lines.append(r.rejection_card())
        return "\n".join(lines)


def _static_parse_ok(sql: str) -> tuple[bool, str]:
    try:
        import sqlglot

        tree = sqlglot.parse_one(sql, read="trino")
        if tree is None:
            return False, "parse returned None"
        return True, "ok"
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)


def _default_apply(sql: str, step: PlanStepV2) -> Optional[str]:
    """Sentinel no-op — must never produce APPLIED success."""
    _ = (sql, step)
    return None


_default_apply.is_noop = True  # type: ignore[attr-defined]


def _order_steps(steps: Sequence[PlanStepV2]) -> list[PlanStepV2]:
    by_id = {s.step_id: s for s in steps}
    remaining = set(by_id)
    ordered: list[PlanStepV2] = []

    def ready(sid: str) -> bool:
        s = by_id[sid]
        return all(d not in remaining for d in (s.depends_on or []))

    def sort_key(sid: str) -> tuple[int, int, str]:
        s = by_id[sid]
        tier = (s.tier or "").lower()
        structural = 0 if s.rule_id in {"P2", "P9", "P10", "P6"} else 1
        tier_bucket = {"safe": 0, "trap": 1, "dangerous": 2}.get(tier, 3)
        return (structural, tier_bucket, sid)

    while remaining:
        ready_ids = [sid for sid in remaining if ready(sid)]
        if not ready_ids:
            ready_ids = sorted(remaining)
        ready_ids.sort(key=sort_key)
        pick = ready_ids[0]
        ordered.append(by_id[pick])
        remaining.remove(pick)
    return ordered


def _site_still_present(sql: str, step: PlanStepV2) -> bool:
    frag = (step.before_fragment or "").strip()
    if frag and frag in sql:
        return True
    site = step.site or {}
    anchor = str(site.get("anchor_path") or site.get("node_ref") or "")
    if site.get("fragment_hash") and not frag:
        return True
    if anchor and anchor in sql:
        return True
    if not frag and not anchor:
        return True
    # node_ref markers like ast:join... are not literal SQL — allow attempt
    if anchor.startswith("ast:"):
        return True
    return False


class StepwiseDriver:
    def __init__(
        self,
        *,
        apply_fn: Optional[ApplyFn] = None,
        live_verify_fn: Optional[LiveVerifyFn] = None,
    ) -> None:
        self.apply_fn = apply_fn or _default_apply
        self.live_verify_fn = live_verify_fn

    def run(
        self,
        sql: str,
        plan: RewritePlan | RewritePlanV2 | Sequence[Any],
        *,
        mcp_client: Any = None,
        confirm_dangerous: bool = False,
        execute_all: bool = False,
        mode: str = "HYBRID_STEPWISE",
    ) -> StepLedger:
        ledger = StepLedger(
            mode=("EXECUTE_ALL_OPTIN" if execute_all else mode),
            base_sql=sql,
        )
        current = sql
        v2 = self._normalize_plan(plan)
        # Refuse dangerous execute in the plan unless human confirmed.
        if not confirm_dangerous:
            assert_no_dangerous_execute_v2(v2)
        steps = _order_steps(v2.steps)

        done_ok: set[str] = set()
        blocked: set[str] = set()

        for step in steps:
            deps = list(step.depends_on or [])
            if any(d in blocked for d in deps):
                ledger.add(
                    StepRecord(
                        step_id=step.step_id,
                        rule_id=step.rule_id,
                        site_anchor=self._anchor(step),
                        status=StepStatus.SKIPPED,
                        verify_level=VerifyLevel.STATIC,
                        reason_code=ReasonCode.DEPENDENCY_BLOCKED,
                        unverified=True,
                        before_sql=current,
                        detail=f"blocked by failed deps {deps}",
                        depends_on=deps,
                    )
                )
                blocked.add(step.step_id)
                continue

            if self._is_dangerous(step) and not confirm_dangerous:
                ledger.add(
                    StepRecord(
                        step_id=step.step_id,
                        rule_id=step.rule_id,
                        site_anchor=self._anchor(step),
                        status=StepStatus.NEEDS_HUMAN,
                        verify_level=VerifyLevel.STATIC,
                        reason_code=ReasonCode.DANGEROUS_UNCONFIRMED,
                        unverified=True,
                        before_sql=current,
                        detail="dangerous rule requires confirm_dangerous=True",
                        depends_on=deps,
                    )
                )
                continue

            if step.action == "advise_only" and not confirm_dangerous:
                ledger.add(
                    StepRecord(
                        step_id=step.step_id,
                        rule_id=step.rule_id,
                        site_anchor=self._anchor(step),
                        status=StepStatus.NEEDS_HUMAN,
                        verify_level=VerifyLevel.STATIC,
                        reason_code=ReasonCode.ADVISE_ONLY,
                        unverified=True,
                        before_sql=current,
                        detail="advise_only step not auto-applied",
                        depends_on=deps,
                    )
                )
                continue

            if not _site_still_present(current, step):
                ledger.add(
                    StepRecord(
                        step_id=step.step_id,
                        rule_id=step.rule_id,
                        site_anchor=self._anchor(step),
                        status=StepStatus.SKIPPED,
                        verify_level=VerifyLevel.STATIC,
                        reason_code=ReasonCode.REANCHOR_FAIL,
                        unverified=True,
                        before_sql=current,
                        detail="re-anchor failed after prior transforms",
                        depends_on=deps,
                    )
                )
                blocked.add(step.step_id)
                continue

            before = current
            try:
                new_sql = self.apply_fn(current, step)
            except Exception as exc:  # noqa: BLE001
                ledger.add(
                    StepRecord(
                        step_id=step.step_id,
                        rule_id=step.rule_id,
                        site_anchor=self._anchor(step),
                        status=StepStatus.REJECTED,
                        verify_level=VerifyLevel.STATIC,
                        reason_code=ReasonCode.APPLY_FAIL,
                        unverified=True,
                        before_sql=before,
                        detail=str(exc),
                        depends_on=deps,
                    )
                )
                continue

            if getattr(self.apply_fn, "is_noop", False):
                ledger.add(
                    StepRecord(
                        step_id=step.step_id,
                        rule_id=step.rule_id,
                        site_anchor=self._anchor(step),
                        status=StepStatus.SKIPPED,
                        verify_level=VerifyLevel.STATIC,
                        reason_code=ReasonCode.APPLY_FAIL,
                        unverified=True,
                        before_sql=before,
                        detail="default apply_fn is no-op; inject real apply_fn (cannot APPLIED)",
                        depends_on=deps,
                    )
                )
                continue

            if not new_sql or new_sql == before:
                ledger.add(
                    StepRecord(
                        step_id=step.step_id,
                        rule_id=step.rule_id,
                        site_anchor=self._anchor(step),
                        status=StepStatus.REJECTED,
                        verify_level=VerifyLevel.STATIC,
                        reason_code=ReasonCode.APPLY_FAIL,
                        unverified=True,
                        before_sql=before,
                        after_sql=new_sql or "",
                        detail="apply_fn returned empty or unchanged SQL",
                        depends_on=deps,
                    )
                )
                continue

            ok, detail = _static_parse_ok(new_sql)
            if not ok:
                ledger.add(
                    StepRecord(
                        step_id=step.step_id,
                        rule_id=step.rule_id,
                        site_anchor=self._anchor(step),
                        status=StepStatus.REJECTED,
                        verify_level=VerifyLevel.STATIC,
                        reason_code=ReasonCode.PARSE_FAIL,
                        unverified=True,
                        before_sql=before,
                        after_sql=new_sql,
                        detail=detail,
                        depends_on=deps,
                    )
                )
                continue

            verify_level = VerifyLevel.STATIC
            reason = ReasonCode.STATIC_OK
            unverified = True
            live_detail = detail

            if mcp_client is not None and self.live_verify_fn is not None:
                try:
                    live_ok, live_code, live_detail = self.live_verify_fn(
                        before, new_sql, step
                    )
                    verify_level = VerifyLevel(
                        step.verify or VerifyLevel.ROW_COUNT.value
                    )
                    if not live_ok:
                        code = ReasonCode.ROWCOUNT_DIFF
                        try:
                            code = ReasonCode(live_code)
                        except Exception:
                            if live_code == "TIMEOUT":
                                code = ReasonCode.TIMEOUT
                            elif live_code == "EXPLAIN_REGRESSION":
                                code = ReasonCode.EXPLAIN_REGRESSION
                        ledger.add(
                            StepRecord(
                                step_id=step.step_id,
                                rule_id=step.rule_id,
                                site_anchor=self._anchor(step),
                                status=StepStatus.REJECTED,
                                verify_level=verify_level,
                                reason_code=code,
                                unverified=False,
                                before_sql=before,
                                after_sql=new_sql,
                                detail=live_detail,
                                depends_on=deps,
                            )
                        )
                        continue
                    unverified = False
                    reason = ReasonCode.OK
                except Exception as exc:  # noqa: BLE001
                    live_detail = f"live verify degraded: {exc}"
                    verify_level = VerifyLevel.STATIC
                    unverified = True
                    reason = ReasonCode.STATIC_OK

            current = new_sql
            done_ok.add(step.step_id)
            ledger.add(
                StepRecord(
                    step_id=step.step_id,
                    rule_id=step.rule_id,
                    site_anchor=self._anchor(step),
                    status=StepStatus.APPLIED,
                    verify_level=verify_level,
                    reason_code=reason,
                    unverified=unverified,
                    before_sql=before,
                    after_sql=new_sql,
                    detail=live_detail,
                    depends_on=deps,
                )
            )

        ledger.final_sql = current
        return ledger

    @staticmethod
    def _anchor(step: PlanStepV2) -> str:
        site = step.site or {}
        return str(site.get("anchor_path") or site.get("node_ref") or step.step_id)

    @staticmethod
    def _is_dangerous(step: PlanStepV2) -> bool:
        try:
            from genie.skills.mcp_trino.p_strategies import RULE_META

            meta = RULE_META.get(step.rule_id)
            if meta is not None:
                return bool(meta.dangerous)
        except Exception:
            pass
        return (step.tier or "").lower() == "dangerous" or step.rule_id in {
            "P3",
            "P4",
            "P6",
        }

    @staticmethod
    def _normalize_plan(plan: Any) -> RewritePlanV2:
        if isinstance(plan, RewritePlanV2):
            return plan
        if isinstance(plan, RewritePlan):
            return upgrade_v1_plan(plan)
        if isinstance(plan, Sequence) and plan and isinstance(plan[0], PlanStepV2):
            return RewritePlanV2(steps=list(plan))
        if isinstance(plan, Sequence) and plan and isinstance(plan[0], RewriteStep):
            return upgrade_v1_plan(RewritePlan(steps=list(plan)))
        if isinstance(plan, Sequence) and not plan:
            return RewritePlanV2(steps=[])
        raise TypeError(f"unsupported plan type: {type(plan)!r}")


def assert_no_dangerous_execute_v2(plan: RewritePlanV2) -> None:
    for s in plan.steps:
        if s.action == "execute" and (s.tier or "").lower() == "dangerous":
            from genie.skills.mcp_trino.rewrite_plan import RewritePlanError

            raise RewritePlanError(
                f"dangerous execute forbidden: {s.step_id} {s.rule_id}"
            )


def default_mode_name(*, execute_all: bool = False) -> str:
    return "EXECUTE_ALL_OPTIN" if execute_all else "HYBRID_STEPWISE"


def stepwise_opt_in(env: dict | None = None) -> bool:
    """Explicit opt-in for attaching/running HYBRID_STEPWISE in research.

    Env: GENIE_STEPWISE=1|true|on (default off / shadow).
    """
    import os
    e = env if env is not None else os.environ
    v = str(e.get("GENIE_STEPWISE", "0")).strip().lower()
    return v in {"1", "true", "yes", "on"}


def run_stepwise_shadow(
    sql: str,
    *,
    confirm_dangerous: bool = False,
    apply_fn: ApplyFn | None = None,
) -> StepLedger:
    """Offline shadow run: SCAN hits → plan v2 → driver (no MCP)."""
    from genie.skills.mcp_trino.phit_scan import scan_phits
    from genie.skills.mcp_trino.rewrite_plan import build_rewrite_plan_v2

    hits = scan_phits(sql)
    plan = build_rewrite_plan_v2(hits)
    driver = StepwiseDriver(apply_fn=apply_fn)
    return driver.run(sql, plan, confirm_dangerous=confirm_dangerous, mcp_client=None)


__all__ = [
    "StepStatus",
    "ReasonCode",
    "VerifyLevel",
    "StepRecord",
    "StepLedger",
    "StepwiseDriver",
    "ApplyFn",
    "LiveVerifyFn",
    "default_mode_name",
    "stepwise_opt_in",
    "run_stepwise_shadow",
    "assert_no_dangerous_execute_v2",
]
