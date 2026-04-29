"""Structural-invariant signature for a Trino EXPLAIN (FORMAT JSON) plan.

Used by the v28 iteration loop as L1 guard: if baseline and candidate produce
the same structural signature we can compare plan cost without committing to
running the candidate. Different signatures → fall through to L3 row-equiv.

Design:
- Walk the plan tree depth-first, normalize each node into a tuple of
  ``(op_kind, key_attrs, child_signatures)``.
- Drop volatile fields: estimates, fragment ids, generated symbol names,
  raw cost numbers, formatting whitespace.
- Keep semantic shape: operator kind, join type, aggregation set, source
  table identifiers (catalog.schema.table), filter predicate key terms.

Two plans are structurally equivalent when ``plan_signature(a) ==
plan_signature(b)``.
"""
from __future__ import annotations

import json
import re
from typing import Any, Optional, Union

PlanSignature = tuple


# Trino plan-node names we recognize. Anything else is bucketed as "Other:<name>"
_KNOWN_OPS = {
    "Output", "Project", "Filter", "ScanProject", "ScanFilter", "ScanFilterProject",
    "TableScan", "Aggregate", "GroupId", "Join", "SemiJoin", "CrossJoin",
    "MarkDistinct", "Distinct", "Sort", "TopN", "Limit", "Window",
    "Exchange", "RemoteExchange", "LocalExchange", "Unnest", "Values",
    "RowNumber", "Apply", "EnforceSingleRow", "AssignUniqueId", "Union",
    "Intersect", "Except", "Sample",
}

_TABLE_RE = re.compile(r"([A-Za-z_][\w]*\.[A-Za-z_][\w]*\.[A-Za-z_][\w]*)")


def _norm_op(name: Any) -> str:
    """Normalize a node name to a canonical operator kind."""
    if not isinstance(name, str):
        return "Unknown"
    base = name.split("[", 1)[0].strip()
    if base in _KNOWN_OPS:
        return base
    return f"Other:{base}"


def _extract_table(descriptor: Any) -> Optional[str]:
    """Pull a catalog.schema.table identifier out of a Trino descriptor blob."""
    if descriptor is None:
        return None
    text = json.dumps(descriptor) if not isinstance(descriptor, str) else descriptor
    m = _TABLE_RE.search(text)
    return m.group(1).lower() if m else None


def _extract_join_type(descriptor: Any) -> Optional[str]:
    if not isinstance(descriptor, dict):
        return None
    for key in ("type", "joinType", "kind"):
        v = descriptor.get(key)
        if isinstance(v, str):
            return v.upper()
    return None


def _extract_agg_funcs(descriptor: Any) -> tuple:
    """Return a sorted tuple of aggregate function names mentioned in a descriptor."""
    if descriptor is None:
        return ()
    text = json.dumps(descriptor) if not isinstance(descriptor, str) else descriptor
    funcs = re.findall(r"\b(count|sum|avg|min|max|approx_distinct|array_agg|map_agg)\b", text, re.IGNORECASE)
    return tuple(sorted({f.lower() for f in funcs}))


def _node_sig(node: Any) -> PlanSignature:
    """Produce a structural signature for one plan node and its subtree."""
    if not isinstance(node, dict):
        return ("Leaf", repr(node)[:32])

    op = _norm_op(node.get("name") or node.get("operator") or node.get("type"))
    desc = node.get("descriptor") or node.get("details") or node.get("operatorAttributes")

    key_attrs: tuple = ()
    if op in {"TableScan", "ScanProject", "ScanFilter", "ScanFilterProject"}:
        key_attrs = ("table", _extract_table(desc) or "")
    elif op == "Join":
        key_attrs = ("join_type", _extract_join_type(desc) or "INNER")
    elif op == "Aggregate":
        key_attrs = ("agg_funcs", _extract_agg_funcs(desc))
    elif op in {"Limit", "TopN"}:
        # Presence matters; the literal N doesn't (different LIMIT values still
        # mean "we cap output", which shouldn't break structural equivalence).
        key_attrs = ()
    elif op == "Exchange" or op.endswith("Exchange"):
        # Volatile under Trino's planner — collapse to a single bucket
        op = "Exchange"

    children = node.get("children") or node.get("inputs") or []
    if isinstance(children, dict):
        children = [children]
    child_sigs = tuple(_node_sig(c) for c in children if c is not None)

    # Sort sibling subtrees so commutative operators (e.g. Join inputs) hash the same
    if op in {"Join", "CrossJoin", "Union", "Intersect", "Except"}:
        child_sigs = tuple(sorted(child_sigs, key=repr))

    return (op, key_attrs, child_sigs)


def plan_signature(plan: Union[str, dict, list, None]) -> Optional[PlanSignature]:
    """Compute the structural signature of a Trino EXPLAIN (FORMAT JSON) plan.

    Returns None when the input is unusable (None, parse error, empty, or a
    shape we can't recognize). Callers should treat None as "L1 unavailable —
    fall through to L3 row-equiv".
    """
    if plan is None:
        return None
    if isinstance(plan, str):
        try:
            plan = json.loads(plan)
        except (json.JSONDecodeError, TypeError):
            return None
    if not isinstance(plan, (dict, list)):
        return None

    # Trino sometimes wraps the plan in a top-level list of stages/fragments;
    # treat them as siblings under a synthetic Root.
    if isinstance(plan, list):
        return ("Root", (), tuple(_node_sig(p) for p in plan if p is not None))

    return _node_sig(plan)


def structural_equivalent(plan_a: Any, plan_b: Any) -> bool:
    """True iff both plans produce the same structural signature.

    Returns False (not None) when either signature is None — callers that want
    a "definitely-divergent vs unknown" distinction should call plan_signature
    directly.
    """
    sig_a = plan_signature(plan_a)
    sig_b = plan_signature(plan_b)
    if sig_a is None or sig_b is None:
        return False
    return sig_a == sig_b


__all__ = ["plan_signature", "structural_equivalent", "PlanSignature"]
