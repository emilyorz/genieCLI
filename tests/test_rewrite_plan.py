import pytest
from genie.skills.mcp_trino.phit_scan import PHit, scan_phits
from genie.skills.mcp_trino.rewrite_plan import (
    RewritePlanError,
    assert_no_dangerous_execute,
    build_rewrite_plan,
)
from pathlib import Path

FIX = Path(__file__).parent / "fixtures" / "rewrite_patterns"


def test_plan_orders_and_marks_dangerous_advise_only():
    sql = (FIX / "like_and_listagg.sql").read_text()
    # mix with a safe hit
    hits = scan_phits(sql) + [
        PHit(pid="P1", node_ref="ast:manual", tier="safe", why="manual safe")
    ]
    plan = build_rewrite_plan(hits)
    assert plan.schema == "genie-rewrite-plan-v1"
    actions = [(s.pid, s.action, s.tier) for s in plan.steps]
    # all dangerous must advise_only
    for s in plan.steps:
        if s.tier == "dangerous":
            assert s.action == "advise_only"
    # first execute steps should not be dangerous
    exec_steps = [s for s in plan.steps if s.action == "execute"]
    assert exec_steps
    assert all(s.tier != "dangerous" for s in exec_steps)
    # safe execute appears before advise
    first_advise = next(i for i, s in enumerate(plan.steps) if s.action == "advise_only")
    last_safe_exec = max(i for i, s in enumerate(plan.steps) if s.action == "execute" and s.tier == "safe")
    assert last_safe_exec < first_advise
    assert_no_dangerous_execute(plan)


def test_unknown_pid_rejected():
    with pytest.raises(RewritePlanError):
        build_rewrite_plan([PHit(pid="T1", node_ref="x", tier="safe", why="no")])


def test_p10_rejected():
    with pytest.raises(RewritePlanError):
        build_rewrite_plan([PHit(pid="P10", node_ref="x", tier="trap", why="deferred")])


def test_force_dangerous_execute_guard():
    # build_rewrite_plan itself forces advise_only; assert helper still clean
    plan = build_rewrite_plan([
        PHit(pid="P3", node_ref="a", tier="dangerous", why="x"),
        PHit(pid="P1", node_ref="b", tier="safe", why="y"),
    ])
    assert_no_dangerous_execute(plan)
