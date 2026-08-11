"""D1 analysis-coverage taxonomy (Fable planner SHIP).

Findings are structural *analysis* labels — agreement with a frozen oracle,
not ground truth and not speedup claims.
"""
from __future__ import annotations

from enum import Enum


class FindingCategory(str, Enum):
    NON_SARGABLE = "NON_SARGABLE"
    IMPLICIT_CAST = "IMPLICIT_CAST"
    MISSING_JOIN_INDEX_CANDIDATE = "MISSING_JOIN_INDEX_CANDIDATE"
    CORRELATED_SUBQUERY = "CORRELATED_SUBQUERY"
    SELECT_STAR_WIDE = "SELECT_STAR_WIDE"
    CARTESIAN_RISK = "CARTESIAN_RISK"
    REDUNDANT_DISTINCT = "REDUNDANT_DISTINCT"
    OR_ON_INDEXED_COL = "OR_ON_INDEXED_COL"
    LEADING_WILDCARD_LIKE = "LEADING_WILDCARD_LIKE"
    REDUNDANT_CTE_JOIN = "REDUNDANT_CTE_JOIN"
    OTHER = "OTHER"


# Map public P-strategy ids → D1 taxonomy (best-effort).
P_TO_CATEGORY: dict[str, FindingCategory] = {
    "P1": FindingCategory.NON_SARGABLE,
    "P2": FindingCategory.CORRELATED_SUBQUERY,
    "P3": FindingCategory.LEADING_WILDCARD_LIKE,
    "P4": FindingCategory.OTHER,
    "P5": FindingCategory.OTHER,
    "P6": FindingCategory.OTHER,
    "P7": FindingCategory.OTHER,
    "P8": FindingCategory.OTHER,
    "P9": FindingCategory.CORRELATED_SUBQUERY,
    "P10": FindingCategory.REDUNDANT_CTE_JOIN,
}


def normalize_object(name: str | None) -> str:
    """Lowercase base table/CTE; strip schema qualifier; drop alias noise."""
    if not name:
        return ""
    s = str(name).strip().lower()
    # strip quotes
    s = s.replace('"', "").replace("`", "")
    if "." in s:
        s = s.split(".")[-1]
    # node_ref style anchors: keep last path token if ast:...
    if s.startswith("ast:"):
        s = s.split(":")[-1]
    # drop trailing site junk
    for sep in ("#", "/", " "):
        if sep in s:
            s = s.split(sep)[0]
    return s.strip()


__all__ = [
    "FindingCategory",
    "P_TO_CATEGORY",
    "normalize_object",
]
