"""Typed, deterministic optimization-kernel domain seam.

This module is deliberately pure: callers capture proposals and evidence, while
the kernel owns only transitions, budget accounting, and acceptance policy.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum
from typing import Sequence, Union


@dataclass(frozen=True)
class QueryDiagnosis:
    baseline_hash: str
    facts: tuple[str, ...]
    capabilities: tuple[str, ...]
    diagnosis_version: str


@dataclass(frozen=True)
class RewriteObligation:
    obligation_id: str
    strategy_id: str
    preconditions: tuple[str, ...]
    dependencies: tuple[str, ...]
    risk_class: str

    def __post_init__(self) -> None:
        if not self.obligation_id.strip():
            raise ValueError("obligation_id must be non-empty")
        if not self.strategy_id.strip():
            raise ValueError("strategy_id must be non-empty")
        if self.obligation_id in self.dependencies:
            raise ValueError("self dependency is not allowed")
        if len(self.dependencies) != len(set(self.dependencies)):
            raise ValueError("duplicate dependency is not allowed")


@dataclass(frozen=True)
class Proposal:
    proposal_id: str
    obligation_id: str
    sql: str
    model: str
    prompt_version: str
    schema_version: str

    def __post_init__(self) -> None:
        if not self.proposal_id.strip():
            raise ValueError("proposal_id must be non-empty")
        if not self.obligation_id.strip():
            raise ValueError("obligation_id must be non-empty")
        if not self.sql.strip():
            raise ValueError("sql must be non-empty")


class EvidenceLevel(str, Enum):
    AST = "ast"
    STRUCTURAL = "structural"
    STRATEGY = "strategy"
    EXPLAIN = "explain"
    RUNTIME = "runtime"


_EVIDENCE_LEVEL_RANK = {
    EvidenceLevel.AST: 1,
    EvidenceLevel.STRUCTURAL: 2,
    EvidenceLevel.STRATEGY: 3,
    EvidenceLevel.EXPLAIN: 4,
    EvidenceLevel.RUNTIME: 5,
}


@dataclass(frozen=True)
class Evidence:
    obligation_id: str
    level: EvidenceLevel
    passed: bool
    source: str
    detail: str
    proposal_id: str

    def __post_init__(self) -> None:
        if not self.obligation_id.strip():
            raise ValueError("obligation_id must be non-empty")
        if not self.proposal_id.strip():
            raise ValueError("proposal_id must be non-empty")
        if not isinstance(self.passed, bool):
            raise ValueError("passed must be a bool")


class KernelState(str, Enum):
    READY = "ready"
    PROPOSED = "proposed"
    VERIFIED = "verified"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    ABSTAINED = "abstained"


_TERMINAL_STATES = frozenset(
    {
        KernelState.ACCEPTED,
        KernelState.REJECTED,
        KernelState.ABSTAINED,
    }
)


@dataclass(frozen=True)
class KernelSnapshot:
    baseline_hash: str
    state: KernelState
    obligations: tuple[RewriteObligation, ...]
    proposal: Proposal | None
    evidence: tuple[Evidence, ...]
    model_calls_used: int
    transition_seq: int
    proposal_ids_seen: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if (
            isinstance(self.model_calls_used, bool)
            or not isinstance(self.model_calls_used, int)
            or self.model_calls_used < 0
        ):
            raise ValueError("model_calls_used must be a non-negative integer")
        if (
            isinstance(self.transition_seq, bool)
            or not isinstance(self.transition_seq, int)
            or self.transition_seq < 0
        ):
            raise ValueError("transition_seq must be a non-negative integer")
        if len(self.proposal_ids_seen) != len(set(self.proposal_ids_seen)):
            raise ValueError("proposal_ids_seen must not contain duplicates")
        if self.model_calls_used != len(self.proposal_ids_seen):
            raise ValueError("model_calls_used must equal the number of proposal_ids_seen")
        if self.transition_seq < self.model_calls_used:
            raise ValueError("transition_seq must be at least model_calls_used")
        if self.proposal is not None and (
            not self.proposal_ids_seen or self.proposal_ids_seen[-1] != self.proposal.proposal_id
        ):
            raise ValueError("current proposal must be the last proposal_ids_seen entry")


@dataclass(frozen=True)
class KernelBudget:
    max_model_calls: int

    def __post_init__(self) -> None:
        if (
            isinstance(self.max_model_calls, bool)
            or not isinstance(self.max_model_calls, int)
            or self.max_model_calls <= 0
        ):
            raise ValueError("max_model_calls must be a positive integer")


@dataclass(frozen=True)
class RegisterObligationsEvent:
    obligations: tuple[RewriteObligation, ...]


@dataclass(frozen=True)
class RecordProposalEvent:
    proposal: Proposal


@dataclass(frozen=True)
class RecordEvidenceEvent:
    evidence: Evidence


@dataclass(frozen=True)
class AcceptEvent:
    pass


@dataclass(frozen=True)
class RejectEvent:
    reason: str = ""


@dataclass(frozen=True)
class AbstainEvent:
    reason: str = ""


KernelEvent = Union[
    RegisterObligationsEvent,
    RecordProposalEvent,
    RecordEvidenceEvent,
    AcceptEvent,
    RejectEvent,
    AbstainEvent,
]


class KernelTransitionError(Exception):
    """Raised when an event cannot legally advance a kernel snapshot."""


def _advance(snapshot: KernelSnapshot, **changes: object) -> KernelSnapshot:
    return replace(snapshot, transition_seq=snapshot.transition_seq + 1, **changes)


def _require_registered_obligation(
    snapshot: KernelSnapshot,
    obligation_id: str,
) -> None:
    if not any(obligation.obligation_id == obligation_id for obligation in snapshot.obligations):
        raise KernelTransitionError(f"unknown obligation: {obligation_id!r}")


def _require_state(
    snapshot: KernelSnapshot,
    event_name: str,
    allowed: frozenset[KernelState],
) -> None:
    if snapshot.state not in allowed:
        expected = ", ".join(sorted(state.value for state in allowed))
        raise KernelTransitionError(
            f"{event_name} is illegal in state {snapshot.state.value}; allowed states: {expected}"
        )


def _require_acyclic(obligations: tuple[RewriteObligation, ...]) -> None:
    graph = {obligation.obligation_id: obligation.dependencies for obligation in obligations}
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(obligation_id: str) -> None:
        if obligation_id in visiting:
            raise KernelTransitionError("obligation dependency cycle detected")
        if obligation_id in visited:
            return
        visiting.add(obligation_id)
        for dependency in graph[obligation_id]:
            visit(dependency)
        visiting.remove(obligation_id)
        visited.add(obligation_id)

    for obligation_id in graph:
        visit(obligation_id)


def apply_event(
    snapshot: KernelSnapshot,
    event: KernelEvent,
    budget: KernelBudget,
) -> KernelSnapshot:
    """Apply one captured event without I/O, clocks, or input mutation."""
    if snapshot.state in _TERMINAL_STATES:
        raise KernelTransitionError(
            f"terminal state {snapshot.state.value} rejects further transitions"
        )
    if isinstance(event, RegisterObligationsEvent):
        _require_state(
            snapshot,
            "register obligations",
            frozenset({KernelState.READY}),
        )
        new_ids = [obligation.obligation_id for obligation in event.obligations]
        if len(new_ids) != len(set(new_ids)):
            raise KernelTransitionError("duplicate obligation id in batch")
        existing_ids = {obligation.obligation_id for obligation in snapshot.obligations}
        if existing_ids & set(new_ids):
            raise KernelTransitionError("duplicate obligation id already registered")
        resolvable_ids = existing_ids | set(new_ids)
        for obligation in event.obligations:
            unresolved = [
                dependency
                for dependency in obligation.dependencies
                if dependency not in resolvable_ids
            ]
            if unresolved:
                raise KernelTransitionError(
                    f"unresolved dependency for {obligation.obligation_id!r}: {unresolved!r}"
                )
        combined_obligations = snapshot.obligations + event.obligations
        _require_acyclic(combined_obligations)
        return _advance(snapshot, obligations=combined_obligations)
    if isinstance(event, RecordProposalEvent):
        _require_state(
            snapshot,
            "record proposal",
            frozenset({KernelState.READY, KernelState.PROPOSED, KernelState.VERIFIED}),
        )
        _require_registered_obligation(snapshot, event.proposal.obligation_id)
        if event.proposal.proposal_id in snapshot.proposal_ids_seen:
            raise KernelTransitionError(f"proposal_id already used: {event.proposal.proposal_id!r}")
        if snapshot.model_calls_used >= budget.max_model_calls:
            raise KernelTransitionError(
                f"model-call budget exceeded ({snapshot.model_calls_used}/{budget.max_model_calls})"
            )
        return _advance(
            snapshot,
            state=KernelState.PROPOSED,
            proposal=event.proposal,
            model_calls_used=snapshot.model_calls_used + 1,
            proposal_ids_seen=snapshot.proposal_ids_seen + (event.proposal.proposal_id,),
        )
    if isinstance(event, RecordEvidenceEvent):
        if snapshot.proposal is None:
            raise KernelTransitionError("record evidence requires a proposal")
        _require_state(
            snapshot,
            "record evidence",
            frozenset({KernelState.PROPOSED, KernelState.VERIFIED}),
        )
        _require_registered_obligation(snapshot, event.evidence.obligation_id)
        if event.evidence.obligation_id != snapshot.proposal.obligation_id:
            raise KernelTransitionError("evidence obligation_id must match current proposal")
        if event.evidence.proposal_id != snapshot.proposal.proposal_id:
            raise KernelTransitionError("evidence proposal_id must match current proposal")
        return _advance(
            snapshot,
            state=KernelState.VERIFIED,
            evidence=snapshot.evidence + (event.evidence,),
        )
    if isinstance(event, AcceptEvent):
        if snapshot.proposal is None:
            raise KernelTransitionError("accept requires a proposal")
        _require_state(
            snapshot,
            "accept",
            frozenset({KernelState.VERIFIED}),
        )
        related_evidence = tuple(
            evidence
            for evidence in snapshot.evidence
            if evidence.obligation_id == snapshot.proposal.obligation_id
            and evidence.proposal_id == snapshot.proposal.proposal_id
        )
        if any(not evidence.passed for evidence in related_evidence):
            raise KernelTransitionError("failed evidence vetoes accept")
        has_sufficient_evidence = any(
            evidence.passed
            and _EVIDENCE_LEVEL_RANK[evidence.level] >= _EVIDENCE_LEVEL_RANK[EvidenceLevel.STRATEGY]
            for evidence in related_evidence
        )
        if not has_sufficient_evidence:
            raise KernelTransitionError("accept requires passed STRATEGY-or-higher evidence")
        return _advance(snapshot, state=KernelState.ACCEPTED)
    if isinstance(event, RejectEvent):
        return _advance(snapshot, state=KernelState.REJECTED)
    if isinstance(event, AbstainEvent):
        return _advance(snapshot, state=KernelState.ABSTAINED)
    raise KernelTransitionError(f"unknown event type: {type(event)!r}")


def replay(
    initial: KernelSnapshot,
    events: Sequence[KernelEvent],
    budget: KernelBudget,
) -> KernelSnapshot:
    """Replay captured events with the same deterministic transition function."""
    current = initial
    for event in events:
        current = apply_event(current, event, budget)
    return current
