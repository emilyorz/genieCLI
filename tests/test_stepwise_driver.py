"""Offline tests for HYBRID_STEPWISE StepwiseDriver."""
from __future__ import annotations

import re

import pytest

from genie.skills.mcp_trino.phit_scan import PHit
from genie.skills.mcp_trino.rewrite_plan import (
    PlanStepV2,
    RewritePlan,
    RewriteStep,
    build_rewrite_plan,
    build_rewrite_plan_v2,
    split_composite_steps,
    upgrade_v1_plan,
)
from genie.skills.mcp_trino.stepwise_driver import (
    ReasonCode,
    StepStatus,
    StepwiseDriver,
    VerifyLevel,
    default_mode_name,
)


def _step(rule_id: str, anchor: str, *, tier: str = "safe", action: str = "execute", sid: str = "S1", deps=None):
    return PlanStepV2(
        step_id=sid,
        rule_id=rule_id,
        site={"anchor_path": anchor, "node_ref": anchor},
        tier=tier,
        action=action,
        verify="STATIC",
        depends_on=list(deps or []),
        before_fragment="",
    )


def test_driver_applies_steps_one_at_a_time():
    sql0 = "SELECT 1 AS x"
    calls: list[str] = []

    def apply_fn(sql: str, step: PlanStepV2) -> str:
        calls.append(step.step_id)
        # each apply appends a marker comment uniquely
        return sql + f"\n-- applied {step.step_id}"

    plan_steps = [
        _step("P1", "ast:a", sid="S1"),
        _step("P7", "ast:b", sid="S2"),
    ]
    driver = StepwiseDriver(apply_fn=apply_fn)
    ledger = driver.run(sql0, plan_steps)
    assert calls == ["S1", "S2"]
    assert all(r.status == StepStatus.APPLIED for r in ledger.records)
    assert ledger.final_sql.count("-- applied") == 2
    assert default_mode_name() == "HYBRID_STEPWISE"


def test_each_applied_step_yields_single_diff():
    def apply_fn(sql: str, step: PlanStepV2) -> str:
        return sql + f"\n-- {step.step_id}"

    driver = StepwiseDriver(apply_fn=apply_fn)
    ledger = driver.run("SELECT 1", [_step("P1", "ast:x", sid="S1")])
    rec = ledger.records[0]
    assert rec.before_sql != rec.after_sql
    assert rec.after_sql.count("-- S1") == 1


def test_rejected_step_does_not_block_independent_steps():
    def apply_fn(sql: str, step: PlanStepV2):
        if step.step_id == "S1":
            return None  # fail apply
        return sql + f"\n-- {step.step_id}"

    driver = StepwiseDriver(apply_fn=apply_fn)
    ledger = driver.run(
        "SELECT 1",
        [
            _step("P1", "ast:a", sid="S1"),
            _step("P7", "ast:b", sid="S2"),  # independent
        ],
    )
    by_id = {r.step_id: r for r in ledger.records}
    assert by_id["S1"].status == StepStatus.REJECTED
    assert by_id["S2"].status == StepStatus.APPLIED
    assert "-- S2" in ledger.final_sql


def test_dangerous_rule_requires_confirm_flag():
    def apply_fn(sql: str, step: PlanStepV2) -> str:
        return sql + "\n-- d"

    driver = StepwiseDriver(apply_fn=apply_fn)
    ledger = driver.run(
        "SELECT 1",
        [_step("P3", "ast:like", tier="dangerous", action="advise_only", sid="S1")],
        confirm_dangerous=False,
    )
    assert ledger.records[0].status == StepStatus.NEEDS_HUMAN
    assert ledger.records[0].reason_code == ReasonCode.DANGEROUS_UNCONFIRMED
    assert ledger.final_sql == "SELECT 1"

    ledger2 = driver.run(
        "SELECT 1",
        [_step("P3", "ast:like", tier="dangerous", action="execute", sid="S1")],
        confirm_dangerous=True,
    )
    assert ledger2.records[0].status == StepStatus.APPLIED


def test_offline_static_verify_marks_unverified():
    def apply_fn(sql: str, step: PlanStepV2) -> str:
        return "SELECT 2 AS y"

    driver = StepwiseDriver(apply_fn=apply_fn)
    ledger = driver.run("SELECT 1 AS x", [_step("P1", "ast:a", sid="S1")], mcp_client=None)
    rec = ledger.records[0]
    assert rec.status == StepStatus.APPLIED
    assert rec.verify_level == VerifyLevel.STATIC
    assert rec.unverified is True
    assert "UNVERIFIED" in ledger.to_markdown()


def test_reanchor_after_applied_step():
    """Second step uses before_fragment; after first apply, fragment still found."""

    def apply_fn(sql: str, step: PlanStepV2) -> str:
        if step.step_id == "S1":
            return sql.replace("A", "AX")
        return sql.replace("B", "BX")

    s1 = _step("P1", "ast:1", sid="S1")
    s1.before_fragment = "A"
    s2 = _step("P7", "ast:2", sid="S2")
    s2.before_fragment = "B"
    driver = StepwiseDriver(apply_fn=apply_fn)
    ledger = driver.run("SELECT A, B", [s1, s2])
    assert [r.status for r in ledger.records] == [StepStatus.APPLIED, StepStatus.APPLIED]
    assert "AX" in ledger.final_sql and "BX" in ledger.final_sql


def test_reanchor_failure_yields_skipped():
    def apply_fn(sql: str, step: PlanStepV2) -> str:
        # remove the fragment the next step needs
        return "SELECT 1"

    s1 = _step("P1", "ast:1", sid="S1")
    s2 = _step("P7", "gone_fragment_xyz", sid="S2")
    s2.before_fragment = "gone_fragment_xyz"
    driver = StepwiseDriver(apply_fn=apply_fn)
    ledger = driver.run("SELECT gone_fragment_xyz", [s1, s2])
    by_id = {r.step_id: r for r in ledger.records}
    assert by_id["S1"].status == StepStatus.APPLIED
    assert by_id["S2"].status == StepStatus.SKIPPED
    assert by_id["S2"].reason_code == ReasonCode.REANCHOR_FAIL


def test_ledger_statuses_and_reason_codes():
    def apply_fn(sql: str, step: PlanStepV2):
        if step.step_id == "bad":
            return "SELECT !!!"
        return sql + f"\n-- {step.step_id}"

    driver = StepwiseDriver(apply_fn=apply_fn)
    ledger = driver.run(
        "SELECT 1",
        [
            _step("P3", "ast:d", tier="dangerous", action="advise_only", sid="dang"),
            _step("P1", "ast:b", sid="bad"),
            _step("P7", "ast:c", sid="ok"),
        ],
    )
    statuses = {r.step_id: r.status for r in ledger.records}
    assert statuses["dang"] == StepStatus.NEEDS_HUMAN
    assert statuses["bad"] == StepStatus.REJECTED
    assert statuses["ok"] == StepStatus.APPLIED
    md = ledger.to_markdown()
    assert "reason_code" in md or "DANGEROUS_UNCONFIRMED" in md
    assert re.search(r"\d+%", md) is None  # no percentage claims


def test_rejection_card_contains_reason_code_and_diff():
    def apply_fn(sql: str, step: PlanStepV2):
        return None

    driver = StepwiseDriver(apply_fn=apply_fn)
    ledger = driver.run("SELECT 1", [_step("P1", "ast:a", sid="S1")])
    card = ledger.records[0].rejection_card()
    assert "reason_code" in card
    assert "APPLY_FAIL" in card


def test_plan_steps_are_atomic_one_rule_one_site():
    hits = [
        PHit(pid="P1", node_ref="ast:j1", tier="safe", why="a"),
        PHit(pid="P1", node_ref="ast:j2", tier="safe", why="b"),
    ]
    plan = build_rewrite_plan_v2(hits)
    assert plan.plan_schema == "v2"
    assert len(plan.steps) == 2
    assert all(s.rule_id == "P1" for s in plan.steps)
    anchors = {s.site["anchor_path"] for s in plan.steps}
    assert anchors == {"ast:j1", "ast:j2"}


def test_composite_step_split_generates_depends_on():
    # same site two rules via manual composite
    from genie.skills.mcp_trino.rewrite_plan import RewritePlanV2

    composite = PlanStepV2(
        step_id="S9",
        rule_id="P1,P7",
        site={"anchor_path": "ast:same", "node_ref": "ast:same"},
        tier="safe",
        action="execute",
    )
    split = split_composite_steps(RewritePlanV2(steps=[composite]))
    assert len(split.steps) == 2
    # second should depend on first (same site)
    assert split.steps[1].depends_on


def test_v1_plan_upgrade_compat():
    v1 = RewritePlan(
        steps=[
            RewriteStep(seq=1, pid="P1", tier="safe", targets=["ast:a", "ast:b"], action="execute")
        ]
    )
    v2 = upgrade_v1_plan(v1)
    assert v2.plan_schema == "v2"
    assert len(v2.steps) == 2


def test_execute_all_flag_changes_mode_name_only_with_flag():
    assert default_mode_name(execute_all=False) == "HYBRID_STEPWISE"
    assert default_mode_name(execute_all=True) == "EXECUTE_ALL_OPTIN"
