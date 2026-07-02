"""Tests for E′ evidence coverage rendering and wiring helpers."""
from __future__ import annotations

import os

from genie.skills.mcp_trino.strategy_verify import (
    CoverageStatus,
    FanoutResult,
    FanoutVerdict,
    LiveEvidence,
    ShipStatus,
    build_evidence_coverage,
    evidence_coverage_enabled,
    render_evidence_coverage,
)


def _fanout(verdict: FanoutVerdict) -> FanoutResult:
    return FanoutResult(verdict, "detail", ("fk",), ("fk",))


def test_coverage_l1_proven_no_fanout_pass_and_advised():
    cov = build_evidence_coverage(
        strategy_id="P9",
        fanout_result=_fanout(FanoutVerdict.PROVEN_NO_FANOUT),
        p9_claimed=True,
        has_correlated_exists=True,
        live_result=None,
    )

    assert cov.l1.status is CoverageStatus.PASS
    assert cov.l2.status is CoverageStatus.PENDING
    assert cov.l3.status is CoverageStatus.NOT_APPLICABLE
    assert cov.ship_status is ShipStatus.ADVISED


def test_coverage_l1_key_mismatch_fail_and_pending_live():
    cov = build_evidence_coverage(
        strategy_id="P9",
        fanout_result=_fanout(FanoutVerdict.KEY_MISMATCH),
        p9_claimed=True,
        has_correlated_exists=True,
        live_result=None,
    )

    assert cov.l1.status is CoverageStatus.FAIL
    assert cov.ship_status is ShipStatus.PENDING_LIVE


def test_coverage_l1_cannot_verify_partial():
    cov = build_evidence_coverage(
        strategy_id="P9",
        fanout_result=_fanout(FanoutVerdict.CANNOT_VERIFY),
        p9_claimed=True,
        has_correlated_exists=True,
        live_result=None,
    )

    assert cov.l1.status is CoverageStatus.PARTIAL
    assert cov.ship_status is ShipStatus.PENDING_LIVE


def test_coverage_l1_not_applied_claimed_p9_fail_exact_reason():
    cov = build_evidence_coverage(
        strategy_id="P9",
        fanout_result=_fanout(FanoutVerdict.NOT_APPLIED),
        p9_claimed=True,
        has_correlated_exists=True,
        live_result=None,
    )

    assert cov.l1.status is CoverageStatus.FAIL
    assert cov.l1.reason == "P9 not applied"
    assert cov.ship_status is ShipStatus.PENDING_LIVE


def test_coverage_l1_no_p9_candidate_not_applicable():
    cov = build_evidence_coverage(
        strategy_id="P9",
        fanout_result=None,
        p9_claimed=False,
        has_correlated_exists=False,
        live_result=None,
    )

    assert cov.l1.status is CoverageStatus.NOT_APPLICABLE
    assert cov.ship_status is ShipStatus.PENDING_LIVE


def test_ship_status_ship_requires_l3_pass_and_sets_l2_not_applicable():
    cov = build_evidence_coverage(
        strategy_id="P9",
        fanout_result=_fanout(FanoutVerdict.PROVEN_NO_FANOUT),
        p9_claimed=True,
        has_correlated_exists=True,
        live_result=LiveEvidence(row_equivalent=True, faster=True, metric_before=10, metric_after=5),
    )

    assert cov.l3.status is CoverageStatus.PASS
    assert cov.ship_status is ShipStatus.SHIP
    assert cov.l2.status is CoverageStatus.NOT_APPLICABLE
    assert cov.l2.reason == "superseded by L3 live validation; EXPLAIN producer not required for shipping verdict"


def test_live_not_equivalent_and_not_faster_reasons():
    not_equiv = build_evidence_coverage(
        "P9", _fanout(FanoutVerdict.PROVEN_NO_FANOUT), True, True,
        LiveEvidence(row_equivalent=False, faster=True, metric_before=10, metric_after=5),
    )
    not_faster = build_evidence_coverage(
        "P9", _fanout(FanoutVerdict.PROVEN_NO_FANOUT), True, True,
        LiveEvidence(row_equivalent=True, faster=False, metric_before=10, metric_after=10),
    )

    assert not_equiv.l3.status is CoverageStatus.FAIL
    assert not_equiv.l3.reason == "not equivalent"
    assert not_faster.l3.status is CoverageStatus.FAIL
    assert not_faster.l3.reason == "not faster"


def test_l2_never_pass_or_fail_exhaustive():
    for verdict in FanoutVerdict:
        for live in (None, LiveEvidence(True, True), LiveEvidence(False, False)):
            cov = build_evidence_coverage("P9", _fanout(verdict), True, True, live)
            assert cov.l2.status in {CoverageStatus.PENDING, CoverageStatus.NOT_APPLICABLE}


def test_env_flag_enabled_unless_exactly_zero(monkeypatch):
    monkeypatch.delenv("GENIE_EVIDENCE_COVERAGE", raising=False)
    assert evidence_coverage_enabled()
    monkeypatch.setenv("GENIE_EVIDENCE_COVERAGE", "")
    assert evidence_coverage_enabled()
    monkeypatch.setenv("GENIE_EVIDENCE_COVERAGE", "false")
    assert evidence_coverage_enabled()
    monkeypatch.setenv("GENIE_EVIDENCE_COVERAGE", "0")
    assert not evidence_coverage_enabled()


def test_render_evidence_coverage_contains_status_and_reasons():
    cov = build_evidence_coverage("P9", _fanout(FanoutVerdict.PROVEN_NO_FANOUT), True, True, None)

    out = render_evidence_coverage(cov)

    assert "Evidence Coverage — P9" in out
    assert "ADVISED" in out
    assert "L1" in out
    assert "PASS" in out
    assert "EXPLAIN producer out of scope this iteration" in out


def test_write_analysis_advisory_report_appends_coverage(monkeypatch):
    from genie.skills.mcp_trino.write_analysis import _render_decompose_advisory
    from tests.test_strategy_verify import ORIGINAL_CORRELATED, REWRITE_PROVEN

    monkeypatch.delenv("GENIE_EVIDENCE_COVERAGE", raising=False)
    lines = _render_decompose_advisory({
        "original_sql": ORIGINAL_CORRELATED,
        "decompose_advisory": {
            "ran": True,
            "changed": True,
            "recompose_status": "ok",
            "recomposed_advisory_sql": REWRITE_PROVEN,
            "fragments": [],
            "candidates": [],
            "reverted_fragments": [],
        },
    })
    out = "\n".join(lines)

    assert "Evidence Coverage — P9" in out
    assert "ADVISED" in out


def test_write_analysis_coverage_disabled_omits_coverage(monkeypatch):
    from genie.skills.mcp_trino.write_analysis import _render_decompose_advisory
    from tests.test_strategy_verify import ORIGINAL_CORRELATED, REWRITE_PROVEN

    monkeypatch.setenv("GENIE_EVIDENCE_COVERAGE", "0")
    lines = _render_decompose_advisory({
        "original_sql": ORIGINAL_CORRELATED,
        "decompose_advisory": {
            "ran": True,
            "changed": True,
            "recompose_status": "ok",
            "recomposed_advisory_sql": REWRITE_PROVEN,
            "fragments": [],
            "candidates": [],
            "reverted_fragments": [],
        },
    })
    out = "\n".join(lines)

    assert "Evidence Coverage — P9" not in out
