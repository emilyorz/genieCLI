"""cost_reader — thin, well-typed wrapper over preflight.plan_cost.

Provides a single cost surface (CostReading) for Run A so callers do not
re-parse EXPLAIN JSON or call plan_cost directly.

The ``explain_runner`` is the MCP boundary: a ``(sql) -> str | None`` callable
bound by the caller to the MCP-trino EXPLAIN tool.  cost_reader does NOT import
the MCP client — the runner is injected (matches the existing plan_cost contract;
keeps it unit-testable with stub closures).

Public API:
    read_cost(sql: str, explain_runner: Optional[ExplainRunner]) -> CostReading
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional


# Type alias for the injected EXPLAIN runner
ExplainRunner = Callable[[str], Optional[str]]


@dataclass(frozen=True)
class CostReading:
    rows_est: Optional[int]      # estimate_from_explain int()-coerces outputRowCount
    bytes_est: Optional[int]     # estimate_from_explain int()-coerces outputSizeInBytes
    cost_scalar: Optional[int]   # _combine_cost(rows_est, bytes_est) -> Optional[int]
    plan_json: Optional[object]  # parsed plan (dict|list) or None
    available: bool              # True iff any of plan_json/rows_est/bytes_est is non-None
    reason: str                  # "ok" | "no_runner" | "cost_unavailable"


def read_cost(sql: str, explain_runner: Optional[ExplainRunner]) -> CostReading:
    """Return a CostReading for *sql* using the injected *explain_runner*.

    Degradation contract (COARSE by design — see spec):
    - explain_runner is None  -> all-None, available=False, reason="no_runner"
    - plan_cost returns all-None (cluster down, garbage output, any exception
      caught internally by plan_cost) -> available=False, reason="cost_unavailable"
    - plan_cost returns at least one non-None field -> available=True, reason="ok"

    NEVER raises to the caller.
    """
    from genie.skills.mcp_trino.preflight import _combine_cost, plan_cost

    if explain_runner is None:
        return CostReading(
            rows_est=None,
            bytes_est=None,
            cost_scalar=None,
            plan_json=None,
            available=False,
            reason="no_runner",
        )

    try:
        rows, bytes_, plan = plan_cost(sql, explain_runner)
    except Exception:
        # plan_cost is specified to catch all exceptions internally, but we add
        # a belt-and-suspenders guard so read_cost never propagates.
        rows, bytes_, plan = None, None, None

    available = plan is not None or rows is not None or bytes_ is not None
    cost_scalar = _combine_cost(rows, bytes_)
    reason = "ok" if available else "cost_unavailable"

    return CostReading(
        rows_est=rows,
        bytes_est=bytes_,
        cost_scalar=cost_scalar,
        plan_json=plan,
        available=available,
        reason=reason,
    )


__all__ = ["CostReading", "ExplainRunner", "read_cost"]
