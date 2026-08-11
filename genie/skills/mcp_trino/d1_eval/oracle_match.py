"""Deterministic oracle matcher for D1 analysis coverage.

Match key (Fable): (category, normalized_object) + optional column-overlap tiebreaker.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

from genie.skills.mcp_trino.d1_eval.taxonomy import FindingCategory, normalize_object


@dataclass(frozen=True)
class Finding:
    category: str
    object: str
    columns: tuple[str, ...] = ()
    note: str = ""

    @staticmethod
    def from_dict(d: dict[str, Any]) -> "Finding":
        cols = d.get("columns") or []
        if isinstance(cols, str):
            cols = [cols]
        return Finding(
            category=str(d.get("category") or FindingCategory.OTHER.value),
            object=normalize_object(d.get("object")),
            columns=tuple(normalize_object(c) for c in cols if c),
            note=str(d.get("note") or ""),
        )


@dataclass
class MatchResult:
    matched: list[tuple[Finding, Finding]] = field(default_factory=list)
    missed: list[Finding] = field(default_factory=list)  # oracle only
    spurious: list[Finding] = field(default_factory=list)  # system only

    @property
    def tp(self) -> int:
        return len(self.matched)

    @property
    def fn(self) -> int:
        return len(self.missed)

    @property
    def fp(self) -> int:
        return len(self.spurious)

    @property
    def recall(self) -> float:
        den = self.tp + self.fn
        return (self.tp / den) if den else 0.0

    @property
    def precision(self) -> float:
        den = self.tp + self.fp
        return (self.tp / den) if den else 0.0


def _columns_ok(oracle: Finding, system: Finding) -> bool:
    if not oracle.columns:
        return True
    if not system.columns:
        # system didn't list columns — allow object-level match
        return True
    return bool(set(oracle.columns) & set(system.columns))


def match_findings(
    oracle: Sequence[Finding] | Iterable[dict],
    system: Sequence[Finding] | Iterable[dict],
) -> MatchResult:
    """Greedy 1-1 match on (category, normalized_object) + column overlap."""
    o_list = [
        f if isinstance(f, Finding) else Finding.from_dict(f) for f in oracle
    ]
    s_list = [
        f if isinstance(f, Finding) else Finding.from_dict(f) for f in system
    ]

    used_s: set[int] = set()
    matched: list[tuple[Finding, Finding]] = []
    missed: list[Finding] = []

    for o in o_list:
        hit_i = None
        for i, s in enumerate(s_list):
            if i in used_s:
                continue
            if o.category != s.category:
                continue
            if o.object != s.object and o.object and s.object:
                # allow empty object on either side only if both empty
                continue
            if o.object != s.object:
                continue
            if not _columns_ok(o, s):
                continue
            hit_i = i
            break
        if hit_i is None:
            missed.append(o)
        else:
            used_s.add(hit_i)
            matched.append((o, s_list[hit_i]))

    spurious = [s for i, s in enumerate(s_list) if i not in used_s]
    return MatchResult(matched=matched, missed=missed, spurious=spurious)


__all__ = ["Finding", "MatchResult", "match_findings"]
