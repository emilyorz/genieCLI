"""Pure-unit contracts for the typed optimization transition kernel."""

from __future__ import annotations

import dataclasses

import pytest

from genie.skills.mcp_trino.optimization_kernel import (
    AcceptEvent,
    AbstainEvent,
    Evidence,
    EvidenceLevel,
    KernelBudget,
    KernelSnapshot,
    KernelState,
    KernelTransitionError,
    Proposal,
    QueryDiagnosis,
    RejectEvent,
    RecordEvidenceEvent,
    RecordProposalEvent,
    RegisterObligationsEvent,
    RewriteObligation,
    apply_event,
    replay,
)


def test_accepts_proposal_with_strategy_evidence():
    diagnosis = QueryDiagnosis(
        baseline_hash="baseline-v1",
        facts=("correlated EXISTS",),
        capabilities=("sqlglot",),
        diagnosis_version="v1",
    )
    obligation = RewriteObligation(
        obligation_id="obl-p9",
        strategy_id="P9",
        preconditions=("correlated EXISTS",),
        dependencies=(),
        risk_class="dangerous",
    )
    proposal = Proposal(
        proposal_id="proposal-1",
        obligation_id="obl-p9",
        sql="SELECT * FROM rewritten",
        model="qwen-3.6-27b",
        prompt_version="v1",
        schema_version="v1",
    )
    initial = KernelSnapshot(
        baseline_hash=diagnosis.baseline_hash,
        state=KernelState.READY,
        obligations=(),
        proposal=None,
        evidence=(),
        model_calls_used=0,
        transition_seq=0,
    )

    snapshot = initial
    for event in (
        RegisterObligationsEvent(obligations=(obligation,)),
        RecordProposalEvent(proposal=proposal),
        RecordEvidenceEvent(
            evidence=Evidence(
                obligation_id="obl-p9",
                level=EvidenceLevel.STRATEGY,
                passed=True,
                source="offline verifier",
                detail="strategy preconditions hold",
                proposal_id="proposal-1",
            )
        ),
        AcceptEvent(),
    ):
        snapshot = apply_event(snapshot, event, KernelBudget(max_model_calls=1))

    assert snapshot.state is KernelState.ACCEPTED
    assert snapshot.proposal == proposal
    assert snapshot.model_calls_used == 1
    assert snapshot.transition_seq == 4


def test_rewrite_obligation_rejects_invalid_identifiers_and_dependencies():
    with pytest.raises(ValueError, match="obligation_id"):
        RewriteObligation("", "P9", (), (), "dangerous")

    with pytest.raises(ValueError, match="strategy_id"):
        RewriteObligation("obl-p9", "", (), (), "dangerous")

    with pytest.raises(ValueError, match="self dependency"):
        RewriteObligation("obl-p9", "P9", (), ("obl-p9",), "dangerous")

    with pytest.raises(ValueError, match="duplicate dependency"):
        RewriteObligation("obl-p9", "P9", (), ("obl-a", "obl-a"), "dangerous")


def test_proposal_rejects_blank_sql():
    with pytest.raises(ValueError, match="sql"):
        Proposal(
            proposal_id="proposal-1",
            obligation_id="obl-p9",
            sql="   ",
            model="qwen-3.6-27b",
            prompt_version="v1",
            schema_version="v1",
        )


def test_proposal_rejects_whitespace_only_ids():
    with pytest.raises(ValueError, match="proposal_id"):
        Proposal("   ", "obl-p9", "SELECT 1", "qwen", "v1", "v1")

    with pytest.raises(ValueError, match="obligation_id"):
        Proposal("proposal-1", "  ", "SELECT 1", "qwen", "v1", "v1")


def test_evidence_rejects_whitespace_only_obligation_id():
    with pytest.raises(ValueError, match="obligation_id"):
        Evidence("   ", EvidenceLevel.STRATEGY, True, "src", "detail", proposal_id="p-1")


@pytest.mark.parametrize("max_model_calls", (0, -1, True, 1.5))
def test_kernel_budget_requires_a_positive_integer(max_model_calls):
    with pytest.raises(ValueError, match="max_model_calls"):
        KernelBudget(max_model_calls=max_model_calls)


def test_register_obligations_rejects_duplicate_in_batch_ids():
    initial = KernelSnapshot("baseline-v1", KernelState.READY, (), None, (), 0, 0)
    obligation_a = RewriteObligation("obl-p9", "P9", (), (), "dangerous")
    obligation_b = RewriteObligation("obl-p9", "P2", (), (), "safe")

    with pytest.raises(KernelTransitionError, match="duplicate obligation"):
        apply_event(
            initial,
            RegisterObligationsEvent((obligation_a, obligation_b)),
            KernelBudget(1),
        )


def test_register_obligations_rejects_duplicate_against_existing():
    obligation_a = RewriteObligation("obl-p9", "P9", (), (), "dangerous")
    budget = KernelBudget(1)
    initial = KernelSnapshot("baseline-v1", KernelState.READY, (), None, (), 0, 0)
    registered = apply_event(
        initial,
        RegisterObligationsEvent((obligation_a,)),
        budget,
    )
    obligation_dup = RewriteObligation("obl-p9", "P2", (), (), "safe")

    with pytest.raises(KernelTransitionError, match="duplicate obligation"):
        apply_event(
            registered,
            RegisterObligationsEvent((obligation_dup,)),
            budget,
        )


def test_register_obligations_rejects_unresolvable_dependency():
    initial = KernelSnapshot("baseline-v1", KernelState.READY, (), None, (), 0, 0)
    obligation = RewriteObligation("obl-p9", "P9", (), ("obl-missing",), "dangerous")

    with pytest.raises(KernelTransitionError, match="unresolved dependency"):
        apply_event(
            initial,
            RegisterObligationsEvent((obligation,)),
            KernelBudget(1),
        )


def test_register_obligations_allows_dependency_resolved_in_same_batch():
    initial = KernelSnapshot("baseline-v1", KernelState.READY, (), None, (), 0, 0)
    obligation_a = RewriteObligation("obl-a", "P9", (), (), "dangerous")
    obligation_b = RewriteObligation("obl-b", "P9", (), ("obl-a",), "dangerous")

    registered = apply_event(
        initial,
        RegisterObligationsEvent((obligation_a, obligation_b)),
        KernelBudget(1),
    )

    assert registered.obligations == (obligation_a, obligation_b)


def test_register_obligations_allows_dependency_resolved_against_existing():
    obligation_a = RewriteObligation("obl-a", "P9", (), (), "dangerous")
    budget = KernelBudget(1)
    initial = KernelSnapshot("baseline-v1", KernelState.READY, (), None, (), 0, 0)
    registered = apply_event(
        initial,
        RegisterObligationsEvent((obligation_a,)),
        budget,
    )
    obligation_b = RewriteObligation("obl-b", "P9", (), ("obl-a",), "dangerous")

    twice_registered = apply_event(
        registered,
        RegisterObligationsEvent((obligation_b,)),
        budget,
    )

    assert twice_registered.obligations == (obligation_a, obligation_b)


def test_proposal_requires_a_registered_obligation():
    initial = KernelSnapshot(
        baseline_hash="baseline-v1",
        state=KernelState.READY,
        obligations=(),
        proposal=None,
        evidence=(),
        model_calls_used=0,
        transition_seq=0,
    )
    proposal = Proposal(
        proposal_id="proposal-unknown",
        obligation_id="missing",
        sql="SELECT 1",
        model="qwen-3.6-27b",
        prompt_version="v1",
        schema_version="v1",
    )

    with pytest.raises(KernelTransitionError, match="unknown obligation"):
        apply_event(initial, RecordProposalEvent(proposal), KernelBudget(1))


def test_record_proposal_rejects_reused_proposal_id():
    obligation_a = RewriteObligation("obl-a", "P9", (), (), "dangerous")
    obligation_b = RewriteObligation("obl-b", "P9", (), (), "dangerous")
    budget = KernelBudget(2)
    initial = KernelSnapshot("baseline-v1", KernelState.READY, (), None, (), 0, 0)
    registered = apply_event(
        initial,
        RegisterObligationsEvent((obligation_a, obligation_b)),
        budget,
    )
    proposed = apply_event(
        registered,
        RecordProposalEvent(Proposal("proposal-1", "obl-a", "SELECT 1", "qwen", "v1", "v1")),
        budget,
    )

    with pytest.raises(KernelTransitionError, match="proposal_id"):
        apply_event(
            proposed,
            RecordProposalEvent(Proposal("proposal-1", "obl-b", "SELECT 2", "qwen", "v1", "v1")),
            budget,
        )


def test_model_call_budget_cannot_be_exceeded():
    obligation = RewriteObligation("obl-p9", "P9", (), (), "dangerous")
    initial = KernelSnapshot(
        baseline_hash="baseline-v1",
        state=KernelState.READY,
        obligations=(),
        proposal=None,
        evidence=(),
        model_calls_used=0,
        transition_seq=0,
    )
    budget = KernelBudget(1)
    registered = apply_event(
        initial,
        RegisterObligationsEvent((obligation,)),
        budget,
    )
    first = apply_event(
        registered,
        RecordProposalEvent(Proposal("proposal-1", "obl-p9", "SELECT 1", "qwen", "v1", "v1")),
        budget,
    )

    with pytest.raises(KernelTransitionError, match="budget"):
        apply_event(
            first,
            RecordProposalEvent(Proposal("proposal-2", "obl-p9", "SELECT 2", "qwen", "v1", "v1")),
            budget,
        )

    assert first.model_calls_used == 1


def test_accept_requires_a_proposal_and_strategy_or_higher_evidence():
    initial = KernelSnapshot(
        baseline_hash="baseline-v1",
        state=KernelState.READY,
        obligations=(),
        proposal=None,
        evidence=(),
        model_calls_used=0,
        transition_seq=0,
    )
    budget = KernelBudget(1)

    with pytest.raises(KernelTransitionError, match="proposal"):
        apply_event(initial, AcceptEvent(), budget)

    obligation = RewriteObligation("obl-p9", "P9", (), (), "dangerous")
    registered = apply_event(
        initial,
        RegisterObligationsEvent((obligation,)),
        budget,
    )
    proposed = apply_event(
        registered,
        RecordProposalEvent(Proposal("proposal-1", "obl-p9", "SELECT 1", "qwen", "v1", "v1")),
        budget,
    )
    ast_only = apply_event(
        proposed,
        RecordEvidenceEvent(
            Evidence(
                "obl-p9",
                EvidenceLevel.AST,
                True,
                "ast",
                "parseable",
                proposal_id="proposal-1",
            )
        ),
        budget,
    )

    with pytest.raises(KernelTransitionError, match="STRATEGY"):
        apply_event(ast_only, AcceptEvent(), budget)


def test_evidence_requires_proposal_id_matching_current_proposal():
    obligation = RewriteObligation("obl-p9", "P9", (), (), "dangerous")
    budget = KernelBudget(1)
    initial = KernelSnapshot("baseline-v1", KernelState.READY, (), None, (), 0, 0)
    registered = apply_event(
        initial,
        RegisterObligationsEvent((obligation,)),
        budget,
    )
    proposed = apply_event(
        registered,
        RecordProposalEvent(Proposal("proposal-1", "obl-p9", "SELECT 1", "qwen", "v1", "v1")),
        budget,
    )

    with pytest.raises(KernelTransitionError, match="proposal_id"):
        apply_event(
            proposed,
            RecordEvidenceEvent(
                Evidence(
                    "obl-p9",
                    EvidenceLevel.STRATEGY,
                    True,
                    "strategy",
                    "sound",
                    proposal_id="proposal-stale",
                )
            ),
            budget,
        )


def test_evidence_requires_a_proposal_id_before_a_proposal_exists():
    initial = KernelSnapshot("baseline-v1", KernelState.READY, (), None, (), 0, 0)

    with pytest.raises(KernelTransitionError, match="proposal"):
        apply_event(
            initial,
            RecordEvidenceEvent(
                Evidence(
                    "obl-p9",
                    EvidenceLevel.STRATEGY,
                    True,
                    "strategy",
                    "sound",
                    proposal_id="proposal-1",
                )
            ),
            KernelBudget(1),
        )


def test_stale_evidence_cannot_authorize_a_new_proposal():
    obligation = RewriteObligation("obl-p9", "P9", (), (), "dangerous")
    budget = KernelBudget(2)
    initial = KernelSnapshot("baseline-v1", KernelState.READY, (), None, (), 0, 0)
    registered = apply_event(initial, RegisterObligationsEvent((obligation,)), budget)
    first = apply_event(
        registered,
        RecordProposalEvent(Proposal("proposal-1", "obl-p9", "SELECT 1", "qwen", "v1", "v1")),
        budget,
    )
    first_verified = apply_event(
        first,
        RecordEvidenceEvent(
            Evidence("obl-p9", EvidenceLevel.STRATEGY, True, "strategy", "sound", "proposal-1")
        ),
        budget,
    )
    second = apply_event(
        first_verified,
        RecordProposalEvent(Proposal("proposal-2", "obl-p9", "SELECT 2", "qwen", "v1", "v1")),
        budget,
    )
    second_ast_only = apply_event(
        second,
        RecordEvidenceEvent(
            Evidence(
                "obl-p9",
                EvidenceLevel.AST,
                True,
                "ast",
                "parseable",
                "proposal-2",
            )
        ),
        budget,
    )

    with pytest.raises(KernelTransitionError, match="STRATEGY"):
        apply_event(second_ast_only, AcceptEvent(), budget)


def test_stale_failed_evidence_does_not_veto_a_new_verified_proposal():
    obligation = RewriteObligation("obl-p9", "P9", (), (), "dangerous")
    budget = KernelBudget(2)
    initial = KernelSnapshot("baseline-v1", KernelState.READY, (), None, (), 0, 0)
    registered = apply_event(initial, RegisterObligationsEvent((obligation,)), budget)
    first = apply_event(
        registered,
        RecordProposalEvent(Proposal("proposal-1", "obl-p9", "SELECT 1", "qwen", "v1", "v1")),
        budget,
    )
    first_failed = apply_event(
        first,
        RecordEvidenceEvent(
            Evidence("obl-p9", EvidenceLevel.AST, False, "ast", "bad", "proposal-1")
        ),
        budget,
    )
    second = apply_event(
        first_failed,
        RecordProposalEvent(Proposal("proposal-2", "obl-p9", "SELECT 2", "qwen", "v1", "v1")),
        budget,
    )
    second_verified = apply_event(
        second,
        RecordEvidenceEvent(
            Evidence("obl-p9", EvidenceLevel.STRATEGY, True, "strategy", "sound", "proposal-2")
        ),
        budget,
    )

    accepted = apply_event(second_verified, AcceptEvent(), budget)
    assert accepted.state is KernelState.ACCEPTED


def test_accept_requires_verified_state_and_accepted_is_terminal():
    obligation = RewriteObligation("obl-p9", "P9", (), (), "dangerous")
    budget = KernelBudget(1)
    initial = KernelSnapshot("baseline-v1", KernelState.READY, (), None, (), 0, 0)
    registered = apply_event(initial, RegisterObligationsEvent((obligation,)), budget)
    proposed = apply_event(
        registered,
        RecordProposalEvent(Proposal("proposal-1", "obl-p9", "SELECT 1", "qwen", "v1", "v1")),
        budget,
    )
    with pytest.raises(KernelTransitionError, match="verified"):
        apply_event(proposed, AcceptEvent(), budget)

    verified = apply_event(
        proposed,
        RecordEvidenceEvent(
            Evidence("obl-p9", EvidenceLevel.STRATEGY, True, "strategy", "sound", "proposal-1")
        ),
        budget,
    )
    accepted = apply_event(verified, AcceptEvent(), budget)
    with pytest.raises(KernelTransitionError, match="terminal"):
        apply_event(accepted, RejectEvent("too late"), budget)


def test_register_obligations_rejects_dependency_cycles():
    initial = KernelSnapshot("baseline-v1", KernelState.READY, (), None, (), 0, 0)
    obligation_a = RewriteObligation("obl-a", "P9", (), ("obl-b",), "dangerous")
    obligation_b = RewriteObligation("obl-b", "P9", (), ("obl-a",), "dangerous")

    with pytest.raises(KernelTransitionError, match="cycle"):
        apply_event(
            initial, RegisterObligationsEvent((obligation_a, obligation_b)), KernelBudget(1)
        )


def test_registration_is_illegal_after_a_proposal():
    obligation = RewriteObligation("obl-a", "P9", (), (), "dangerous")
    budget = KernelBudget(1)
    initial = KernelSnapshot("baseline-v1", KernelState.READY, (), None, (), 0, 0)
    registered = apply_event(initial, RegisterObligationsEvent((obligation,)), budget)
    proposed = apply_event(
        registered,
        RecordProposalEvent(Proposal("proposal-1", "obl-a", "SELECT 1", "qwen", "v1", "v1")),
        budget,
    )

    with pytest.raises(KernelTransitionError, match="state"):
        apply_event(
            proposed,
            RegisterObligationsEvent((RewriteObligation("obl-b", "P9", (), (), "dangerous"),)),
            budget,
        )


@pytest.mark.parametrize(
    "kwargs",
    (
        {"model_calls_used": -1},
        {"transition_seq": -1},
        {"proposal_ids_seen": ("proposal-1", "proposal-1")},
    ),
)
def test_snapshot_rejects_invalid_counters_and_proposal_history(kwargs):
    values = dict(
        baseline_hash="baseline-v1",
        state=KernelState.READY,
        obligations=(),
        proposal=None,
        evidence=(),
        model_calls_used=0,
        transition_seq=0,
    )
    values.update(kwargs)
    with pytest.raises(ValueError):
        KernelSnapshot(**values)


@pytest.mark.parametrize("passed", ("false", 0, 1, None))
def test_evidence_passed_requires_bool(passed):
    with pytest.raises(ValueError, match="passed"):
        Evidence("obl-p9", EvidenceLevel.STRATEGY, passed, "src", "detail", "p-1")


def test_snapshot_rejects_cross_field_inconsistency():
    proposal = Proposal("proposal-1", "obl-p9", "SELECT 1", "qwen", "v1", "v1")
    base = dict(
        baseline_hash="baseline-v1",
        state=KernelState.PROPOSED,
        obligations=(RewriteObligation("obl-p9", "P9", (), (), "dangerous"),),
        proposal=proposal,
        evidence=(),
    )
    with pytest.raises(ValueError, match="model_calls_used"):
        KernelSnapshot(
            **base,
            model_calls_used=0,
            transition_seq=1,
            proposal_ids_seen=("proposal-1",),
        )
    with pytest.raises(ValueError, match="transition_seq"):
        KernelSnapshot(
            **base,
            model_calls_used=1,
            transition_seq=0,
            proposal_ids_seen=("proposal-1",),
        )
    with pytest.raises(ValueError, match="current proposal"):
        KernelSnapshot(
            **base,
            model_calls_used=1,
            transition_seq=1,
            proposal_ids_seen=("proposal-other",),
        )


def test_failed_evidence_vetoes_acceptance():
    obligation = RewriteObligation("obl-p9", "P9", (), (), "dangerous")
    budget = KernelBudget(1)
    initial = KernelSnapshot("baseline-v1", KernelState.READY, (), None, (), 0, 0)
    registered = apply_event(
        initial,
        RegisterObligationsEvent((obligation,)),
        budget,
    )
    proposed = apply_event(
        registered,
        RecordProposalEvent(Proposal("proposal-1", "obl-p9", "SELECT 1", "qwen", "v1", "v1")),
        budget,
    )
    passed = apply_event(
        proposed,
        RecordEvidenceEvent(
            Evidence(
                "obl-p9",
                EvidenceLevel.STRATEGY,
                True,
                "strategy",
                "sound",
                proposal_id="proposal-1",
            )
        ),
        budget,
    )
    vetoed = apply_event(
        passed,
        RecordEvidenceEvent(
            Evidence(
                "obl-p9",
                EvidenceLevel.AST,
                False,
                "ast",
                "mismatch",
                proposal_id="proposal-1",
            )
        ),
        budget,
    )

    with pytest.raises(KernelTransitionError, match="failed evidence"):
        apply_event(vetoed, AcceptEvent(), budget)


@pytest.mark.parametrize(
    ("terminal_event", "terminal_state"),
    (
        (RejectEvent("operator rejected"), KernelState.REJECTED),
        (AbstainEvent("insufficient evidence"), KernelState.ABSTAINED),
    ),
)
def test_reject_and_abstain_lock_the_kernel(terminal_event, terminal_state):
    budget = KernelBudget(1)
    initial = KernelSnapshot("baseline-v1", KernelState.READY, (), None, (), 0, 0)
    terminal = apply_event(initial, terminal_event, budget)

    assert terminal.state is terminal_state
    with pytest.raises(KernelTransitionError, match="terminal"):
        apply_event(
            terminal,
            RegisterObligationsEvent((RewriteObligation("obl-p9", "P9", (), (), "dangerous"),)),
            budget,
        )


def test_replay_is_deterministic_and_matches_stepwise_application():
    obligation = RewriteObligation("obl-p9", "P9", (), (), "dangerous")
    proposal = Proposal("proposal-1", "obl-p9", "SELECT 1", "qwen", "v1", "v1")
    events = (
        RegisterObligationsEvent((obligation,)),
        RecordProposalEvent(proposal),
        RecordEvidenceEvent(
            Evidence(
                "obl-p9",
                EvidenceLevel.EXPLAIN,
                True,
                "explain",
                "lower cost",
                proposal_id="proposal-1",
            )
        ),
        AcceptEvent(),
    )
    initial = KernelSnapshot("baseline-v1", KernelState.READY, (), None, (), 0, 0)
    budget = KernelBudget(1)

    stepwise = initial
    for event in events:
        stepwise = apply_event(stepwise, event, budget)

    replayed_once = replay(initial, events, budget)
    replayed_twice = replay(initial, events, budget)

    assert replayed_once == stepwise
    assert replayed_twice == stepwise
    assert initial.state is KernelState.READY
    assert initial.transition_seq == 0


def test_ir_and_snapshots_are_frozen_and_transitions_return_new_snapshots():
    diagnosis = QueryDiagnosis("baseline-v1", (), (), "v1")
    obligation = RewriteObligation("obl-p9", "P9", (), (), "dangerous")
    proposal = Proposal("proposal-1", "obl-p9", "SELECT 1", "qwen", "v1", "v1")
    evidence = Evidence(
        "obl-p9",
        EvidenceLevel.STRATEGY,
        True,
        "strategy",
        "sound",
        proposal_id="proposal-1",
    )
    budget = KernelBudget(1)
    initial = KernelSnapshot("baseline-v1", KernelState.READY, (), None, (), 0, 0)

    for instance, field_name, replacement in (
        (diagnosis, "baseline_hash", "other"),
        (obligation, "strategy_id", "P2"),
        (proposal, "sql", "SELECT 2"),
        (evidence, "source", "other"),
        (budget, "max_model_calls", 2),
        (initial, "state", KernelState.ACCEPTED),
    ):
        with pytest.raises(dataclasses.FrozenInstanceError):
            setattr(instance, field_name, replacement)

    registered = apply_event(
        initial,
        RegisterObligationsEvent((obligation,)),
        budget,
    )
    assert registered is not initial
    assert initial.obligations == ()
