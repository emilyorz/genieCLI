"""Map engine P-hits / lightweight extras → D1 findings."""
from __future__ import annotations

from typing import Any

from genie.skills.mcp_trino.d1_eval.oracle_match import Finding
from genie.skills.mcp_trino.d1_eval.taxonomy import (
    FindingCategory,
    P_TO_CATEGORY,
    normalize_object,
)
from genie.skills.mcp_trino.phit_scan import PHit, scan_phits


# Stable D1 objects by P-id so oracle matching is deterministic across site anchors.
_PID_OBJECT = {
    "P1": "join",
    "P2": "exists",
    "P3": "like",
    "P4": "listagg",
    "P5": "predicate",
    "P6": "lambda",
    "P7": "join",
    "P8": "join",
    "P9": "exists",
    "P10": "cte",
}


def _object_from_hit(h: PHit) -> str:
    if h.pid in _PID_OBJECT:
        return _PID_OBJECT[h.pid]
    ref = h.node_ref or ""
    return normalize_object(ref) or "query"


def findings_from_phits(hits: list[PHit]) -> list[Finding]:
    out: list[Finding] = []
    seen: set[tuple[str, str]] = set()
    for h in hits:
        cat = P_TO_CATEGORY.get(h.pid, FindingCategory.OTHER)
        obj = _object_from_hit(h)
        key = (cat.value, obj)
        if key in seen:
            continue
        seen.add(key)
        out.append(
            Finding(
                category=cat.value,
                object=obj,
                columns=(),
                note=f"{h.pid}: {h.why}",
            )
        )
    return out


def analyze_sql(sql: str) -> list[Finding]:
    """Static D1 analysis path (no LLM, no MCP)."""
    hits = scan_phits(sql)
    findings = findings_from_phits(hits)
    # Lightweight extras that complement P-hit menu
    findings.extend(_extra_static_findings(sql))
    # dedupe
    uniq: dict[tuple[str, str], Finding] = {}
    for f in findings:
        uniq[(f.category, f.object)] = f
    return list(uniq.values())


def _extra_static_findings(sql: str) -> list[Finding]:
    """Cheap extras: SELECT *, leading-wildcard LIKE if not already P3."""
    out: list[Finding] = []
    try:
        import sqlglot
        from sqlglot import exp

        tree = sqlglot.parse_one(sql, read="trino")
    except Exception:
        return out

    # SELECT *
    for star in tree.find_all(exp.Star):
        out.append(
            Finding(
                category=FindingCategory.SELECT_STAR_WIDE.value,
                object="select",
                note="SELECT * wide projection",
            )
        )
        break

    # Cartesian: JOIN without ON/USING
    for j in tree.find_all(exp.Join):
        on = j.args.get("on")
        using = j.args.get("using")
        kind = (j.args.get("kind") or "").upper()
        if kind in {"CROSS", "COMMA"}:
            out.append(
                Finding(
                    category=FindingCategory.CARTESIAN_RISK.value,
                    object=normalize_object(j.this.sql() if j.this else "join"),
                    note="CROSS/COMMA join",
                )
            )
        elif on is None and not using and kind not in {"CROSS"}:
            # bare JOIN may still have on elsewhere — only flag if truly missing
            if "on" not in (j.args or {}) or j.args.get("on") is None:
                out.append(
                    Finding(
                        category=FindingCategory.CARTESIAN_RISK.value,
                        object=normalize_object(
                            getattr(j.this, "name", None) or "join"
                        ),
                        note="JOIN without ON/USING",
                    )
                )

    return out


__all__ = ["analyze_sql", "findings_from_phits"]
