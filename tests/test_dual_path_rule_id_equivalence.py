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
    return {
        (d.kind, d.severity, d.target_metric)
        for d in directions
        if not d.evidence.startswith("explain:")
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
