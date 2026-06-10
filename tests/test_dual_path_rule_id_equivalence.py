"""v32 regression: real static rule_id mappings stay equivalent on both paths."""
from __future__ import annotations

import json
from unittest.mock import MagicMock

from genie.skills.trino_query.sql_static import analyze
from genie.skills.trino_query.sql_static.rule_ids import (
    RULE_JOIN_FIRST_FILTER_LATE,
    RULE_REDUNDANT_DISTINCT_AFTER_GROUP_BY,
)


_PLAN_JSON = json.dumps(
    {
        "name": "Output",
        "estimates": [{"outputRowCount": 10, "outputSizeInBytes": 1000}],
        "children": [{"name": "TableScan", "estimates": [], "children": []}],
    }
)


def _mcp_client_returning_plan(plan_json: str) -> MagicMock:
    client = MagicMock()
    client.list_tools.return_value = [
        {
            "name": "query",
            "inputSchema": {"properties": {"sql": {"type": "string"}}},
        }
    ]
    client.call_tool.return_value = json.dumps(
        {"rows": [[plan_json]], "columns": ["Query Plan"]}
    )
    return client


def _non_explain_tuples(directions) -> set[tuple[str, str, str]]:
    # S3: exclude the 'metadata-unavailable' info note — it is intentionally
    # appended only by the --direct assembler and must not break MCP/direct parity
    # checks for real diagnostic directions.
    return {
        (d.kind, d.severity, d.target_metric)
        for d in directions
        if not d.evidence.startswith("explain:") and d.kind != "metadata-unavailable"
    }


def test_real_rule_id_maps_to_same_direction_kind_on_mcp_and_direct(monkeypatch):
    """Drive analyze() + both assemblers; no fabricated rule_id namespaces."""
    from genie.skills.mcp_trino import research as mcp_research
    from genie.skills.trino_query import research as direct_research

    sql = "SELECT DISTINCT account_id FROM raw.events GROUP BY account_id"
    static_report = analyze(sql)
    emitted_rule_ids = {f.rule_id for f in static_report.findings}
    assert RULE_REDUNDANT_DISTINCT_AFTER_GROUP_BY in emitted_rule_ids

    client = _mcp_client_returning_plan(_PLAN_JSON)
    monkeypatch.setattr(mcp_research, "_resolved_tool", None)

    mcp_directions, mcp_metadata = mcp_research._assemble_mcp_directions(
        client,
        sql,
        static_report,
    )
    direct_directions = direct_research._assemble_direct_directions(
        sql,
        static_report,
        lambda _sql: _PLAN_JSON,
    )

    assert mcp_metadata == []
    assert len(client.call_tool.call_args_list) == 1
    assert "EXPLAIN (FORMAT JSON)" in client.call_tool.call_args.args[1]["sql"]

    mcp_non_explain = _non_explain_tuples(mcp_directions)
    direct_non_explain = _non_explain_tuples(direct_directions)

    assert mcp_non_explain
    assert mcp_non_explain == direct_non_explain
    assert (
        "fix-distinct-after-group-by",
        "medium",
        "wall_time_ms",
    ) in mcp_non_explain


def test_join_first_filter_late_has_same_non_explain_tuple_on_both_paths(monkeypatch):
    from genie.skills.mcp_trino import research as mcp_research
    from genie.skills.trino_query import research as direct_research

    sql = (
        "WITH joined AS ("
        " SELECT o.order_id, c.region"
        " FROM orders o"
        " JOIN customers c ON c.customer_id = o.customer_id"
        ")"
        " SELECT order_id FROM joined WHERE region = 'TW'"
    )
    static_report = analyze(sql)
    emitted_rule_ids = {f.rule_id for f in static_report.findings}
    assert RULE_JOIN_FIRST_FILTER_LATE in emitted_rule_ids

    client = _mcp_client_returning_plan(_PLAN_JSON)
    monkeypatch.setattr(mcp_research, "_resolved_tool", None)

    mcp_directions, _ = mcp_research._assemble_mcp_directions(client, sql, static_report)
    direct_directions = direct_research._assemble_direct_directions(
        sql,
        static_report,
        lambda _sql: _PLAN_JSON,
    )

    assert _non_explain_tuples(mcp_directions) == _non_explain_tuples(direct_directions)
    assert (
        "fix-join-first-filter-late",
        "medium",
        "wall_time_ms",
    ) in _non_explain_tuples(mcp_directions)


# ---------------------------------------------------------------------------
# S3 — Pin KIND and ACTION for every rule in ALL_RULE_IDS (v43)
# ---------------------------------------------------------------------------

import pytest
from genie.skills.trino_query.sql_static.rule_ids import (
    ALL_RULE_IDS,
    RULE_CARTESIAN_JOIN,
    RULE_SELECT_STAR,
    RULE_REDUNDANT_DISTINCT_AFTER_GROUP_BY,
    RULE_UNNECESSARY_ORDER_BY_IN_SUBQUERY,
    RULE_SUBQUERY_IN_SELECT_PUSHABLE_TO_JOIN,
    RULE_PREDICATE_NOT_PUSHED_TO_CTE,
    RULE_NULL_UNSAFE_EQUALS,
    RULE_REDUNDANT_CAST_CHAIN,
    RULE_JOIN_FIRST_FILTER_LATE,
    RULE_JOIN_KEY_COMPUTED,
)
from genie.skills.mcp_trino.pre_execution_diagnosis import _RULE_KIND_MAP
from genie.skills.mcp_trino.rule_gate import ACTION_BLOCK, ACTION_REWRITE, ACTION_ADVISE, _STATIC_ACTIONS

_PINS = [
    (RULE_CARTESIAN_JOIN,                      "fix-cartesian-join",           ACTION_BLOCK),
    (RULE_SELECT_STAR,                         "fix-select-star",              ACTION_ADVISE),
    (RULE_REDUNDANT_DISTINCT_AFTER_GROUP_BY,   "fix-distinct-after-group-by",  ACTION_REWRITE),
    (RULE_UNNECESSARY_ORDER_BY_IN_SUBQUERY,    "fix-order-by-in-subquery",     ACTION_REWRITE),
    (RULE_SUBQUERY_IN_SELECT_PUSHABLE_TO_JOIN, "fix-subquery-in-select",       ACTION_ADVISE),
    (RULE_PREDICATE_NOT_PUSHED_TO_CTE,         "fix-predicate-pushdown",       ACTION_REWRITE),
    (RULE_NULL_UNSAFE_EQUALS,                  "fix-null-unsafe-equals",       ACTION_BLOCK),
    (RULE_REDUNDANT_CAST_CHAIN,                "fix-redundant-cast",           ACTION_REWRITE),
    (RULE_JOIN_FIRST_FILTER_LATE,              "fix-join-first-filter-late",   ACTION_REWRITE),
    (RULE_JOIN_KEY_COMPUTED,                   "fix-join-key-computed",        ACTION_ADVISE),
]


def test_pin_table_covers_all_rule_ids():
    """Completeness gate: pin table must cover ALL_RULE_IDS exactly.
    Adding an 11th rule fails this test until the pin table is updated.
    """
    assert {rid for rid, _, _ in _PINS} == set(ALL_RULE_IDS)


@pytest.mark.parametrize("rule_id,kind,action", _PINS)
def test_rule_kind_and_action_pinned(rule_id, kind, action):
    """Pin _RULE_KIND_MAP and _STATIC_ACTIONS[0] for every rule in ALL_RULE_IDS.
    Catches VALUE renames that are invisible to key-set-only tests.
    """
    assert _RULE_KIND_MAP[rule_id] == kind
    assert _STATIC_ACTIONS[rule_id][0] == action
