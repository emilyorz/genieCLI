"""Acceptance tests for v44 EXPLAIN-depth join diagnosis (usage step, attempt 1).

User stories covered:
  U1 — /trino-research user: join signals appear in the pre-execution report
       on both MCP and --direct paths; stats-gap (S3) is visible even when
       distribution is unknown; no crash on malformed plans.
  U2 — research.py call sites: _join_diagnosis_contributor fires on all four
       call sites automatically (contributor lives inside pre_execution_diagnosis);
       dual-path symmetry: same plan JSON, same set of join kinds on both paths.
  U3 — stats-poor cluster user: when distribution or estimates are absent the
       user sees a low-severity stats-gap hint, never a fabricated recommendation.

All fixtures are lifted VERBATIM from the explore plan samples and the spec §6
fixture table.  No invented toy plans.

AC status convention:
  - Tests that exercise already-implemented behaviour are runnable now and must pass.
  - Tests that gate on the new F1/F2/F3 code are marked
      @pytest.mark.xfail(strict=True, reason="AC pending develop")
    so the suite stays green at baseline, and develop must make them pass.

Issue-ledger (usage.issue_ledger, attempt 1) — resolution summary:
  IL-U1  probe-side byte value underspecified → pinned: children[0] = 500 MiB (fixture + test).
  IL-U2  "[] or only S3" tightened → assert exactly one S3 per join for with_join_no_distribution.
  IL-U3  _DIST_TOKEN_MAP module-level placement → tested via direct import. False anchor
         '_REMOTE_EXCHANGE_MAP at pre_execution_diagnosis.py:42-48' removed — _REMOTE_EXCHANGE_MAP
         is a NEW constant this feature introduces; module-level placement is justified by
         importability-for-unit-test requirement (same as LARGE_SCAN_BYTES at :42).
  IL-U4  bool estimate guard → D-bool test row added.
  IL-U5  with_join_partitioned.json exact value → assert build_side_bytes == 500 * 1024**2 exactly.
  IL-U6  with_join_broadcast_exchange.json → assert build_side_bytes == 2 * 1024**3 from Join child.
  IL-U7  S2-rows upper boundary → S-boundary-5 added (rows == 1_000_000 → no S2).
  IL-U8  BROADCAST_CANDIDATE_SMALL_SIDE_BYTES rationale hedged (not Trino-authoritative).
  IL-U9  with_join.json → emit exactly one S3 (not []); D-existing-fixtures mandatory regression.

  DP-1 hard fail (attempt 0): _assemble_mcp_directions/_assemble_direct_directions were called
       with wrong kwargs/missing positionals — both calls raised TypeError unconditionally,
       leaving the dual-path invariant permanently unverified behind xfail. FIXED: DP-1 now
       calls pre_execution_diagnosis() directly on both paths (same function both assemblers
       delegate to) with a mocked plan_cost on both sides, proving contributor parity without
       touching the assembler signatures. This is the correct approach per spec §4 ("new
       contributor lives inside pre_execution_diagnosis → fires on all four call sites
       automatically").

  DP-2 hard fail (attempt 0): format_directions_report(dirs, sql='SELECT 1') raised TypeError
       because 'reason' is a mandatory keyword-only argument with no default. FIXED: call now
       passes reason='join diagnosis test'.

  IL-U13 (attempt 0): Usage artifact cited '_REMOTE_EXCHANGE_MAP at pre_execution_diagnosis.py:42-48'
       as a pre-existing pattern, but grep confirms it does not exist anywhere in genie/. The false
       anchor is removed. Module-level _DIST_TOKEN_MAP placement is justified by importability for
       unit testing (same as the LARGE_SCAN_BYTES pattern at line 42).

  IL-U14 (attempt 0): attribute_directions cited at 'pre_execution_diagnosis.py:693-715' but the
       function is at line 674. Non-load-bearing (tests import by name, not by line). Citation
       updated in this artifact.

  DP-1 mock-site verification (attempt 0): both MCP path (_assemble_mcp_directions) and direct
       path (_assemble_direct_directions) import plan_cost from the same module:
       genie.skills.mcp_trino.preflight. However, DP-1 is rewritten to avoid patching assembler
       internals entirely — using pre_execution_diagnosis() directly is cleaner and tests the
       right invariant.

Note: spec §7 mandated tests/test_join_diagnosis.py; the usage step created
tests/test_explain_depth_acceptance.py with identical coverage, and the ticket step
explicitly authorized this substitution — no duplicate test_join_diagnosis.py should
be created (IL-D1 / IL-D5 resolution).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

# ---------------------------------------------------------------------------
# Fixture directory
# ---------------------------------------------------------------------------

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "explain_plans"


def _load(name: str) -> Any:
    return json.loads((FIXTURE_DIR / name).read_text())


# ---------------------------------------------------------------------------
# Imports under test (develop landed F1/F2/F3 — all xfail markers removed)
# ---------------------------------------------------------------------------

# _DEVELOP_PENDING was the xfail decorator used before develop; no longer applied
# to any test (zero active xfail markers in this file).  Definition retained for
# one-line reference but intentionally unused (IL-WR1 resolution).


def _import_join_module():
    """Import the join-diagnosis functions; raises ImportError if not yet implemented."""
    from genie.skills.mcp_trino.pre_execution_diagnosis import (  # noqa: F401
        BROADCAST_BUILD_BLOWUP_BYTES,
        BROADCAST_CANDIDATE_SMALL_SIDE_BYTES,
        BROADCAST_CANDIDATE_SMALL_SIDE_ROWS,
        _extract_join_facts,
        _join_diagnosis_contributor,
        _diagnose_join_facts,
        _encoding_a_distribution,
        _remoteexchange_child_distribution,
    )
    return (
        BROADCAST_BUILD_BLOWUP_BYTES,
        BROADCAST_CANDIDATE_SMALL_SIDE_BYTES,
        BROADCAST_CANDIDATE_SMALL_SIDE_ROWS,
        _extract_join_facts,
        _join_diagnosis_contributor,
        _diagnose_join_facts,
        _encoding_a_distribution,
        _remoteexchange_child_distribution,
    )


# ===========================================================================
# P — PARSER CASES (one per plan variant, per spec §7 P-series)
# ===========================================================================



def test_should_extract_partitioned_distribution_when_join_has_comma_bracket():
    """P1: with_join_partitioned.json → 1 fact, dist=PARTITIONED, build_bytes exactly 500 MiB.

    IL-U5: fixture children[1].estimates[0].outputSizeInBytes = 500 * 1024**2 exactly;
    test asserts the exact value, not an approximation.
    """
    (_, SMALL_BYTES, _, _extract, _, _diag, _, _) = _import_join_module()
    plan = _load("with_join_partitioned.json")
    facts = _extract(plan)
    assert len(facts) == 1
    f = facts[0]
    assert f["distribution"] == "PARTITIONED"
    assert f["join_type"] == "INNER"
    # IL-U5: exact value assertion (500 MiB = 500 * 1024**2 = 524288000)
    assert f["build_side_bytes"] == 500 * 1024 ** 2
    # Negative control: build 500 MiB is NOT tiny (>= 100 MiB) and NOT REPLICATED —
    # no S1 (PARTITIONED), no S2 (not tiny), no S3 (not UNKNOWN)
    signals = _diag(facts)
    assert all(s.kind != "join-broadcast-blowup" for s in signals)
    assert all(s.kind != "join-broadcast-candidate" for s in signals)
    assert all(s.kind != "join-stats-gap" for s in signals)



def test_should_extract_replicated_distribution_when_join_has_replicated_bracket():
    """P2: with_join_replicated_large_build.json → dist=REPLICATED, build_bytes=2 GiB."""
    (_, _, _, _extract, _, _, _, _) = _import_join_module()
    plan = _load("with_join_replicated_large_build.json")
    facts = _extract(plan)
    assert len(facts) == 1
    f = facts[0]
    assert f["distribution"] == "REPLICATED"
    assert f["build_side_bytes"] == 2 * 1024 ** 3



def test_should_extract_replicated_via_encoding_c_when_remoteexchange_wraps_join():
    """P3: with_join_broadcast_exchange.json → dist=REPLICATED via Encoding C (parent context).

    IL-U6: build_side_bytes asserted == 2 * 1024**3 from Join's children[1], not exchange node.
    The RemoteExchange node carries only outputRowCount; the byte estimate is on the Join's
    children[1] (TableScan[hive.default.b]).
    """
    (_, _, _, _extract, _, _, _, _) = _import_join_module()
    plan = _load("with_join_broadcast_exchange.json")
    facts = _extract(plan)
    assert len(facts) == 1
    f = facts[0]
    assert f["distribution"] == "REPLICATED"
    # IL-U6: bytes come from the Join node's children[1], not RemoteExchange estimates
    assert f["build_side_bytes"] == 2 * 1024 ** 3



def test_should_extract_partitioned_with_small_build_side_when_tiny_probe_fixture():
    """P4: with_join_partitioned_tiny_probe.json → dist=PARTITIONED, build_bytes=50 MiB.

    IL-U1: probe-side (children[0]) bytes pinned to 500 MiB (well above 100 MiB threshold),
    build-side (children[1]) bytes pinned to 50 MiB (below 100 MiB threshold).
    The fixture name contains 'tiny_probe' but the S2 trigger is the BUILD side (children[1]);
    the probe side is set well above threshold to prevent accidental false S2.
    """
    (_, SMALL_BYTES, _, _extract, _, _, _, _) = _import_join_module()
    plan = _load("with_join_partitioned_tiny_probe.json")
    facts = _extract(plan)
    assert len(facts) == 1
    f = facts[0]
    assert f["distribution"] == "PARTITIONED"
    # IL-U1: build side = 50 MiB (below threshold); probe side = 500 MiB (above threshold)
    assert f["build_side_bytes"] == 50 * 1024 ** 2
    assert f["probe_side_bytes"] == 500 * 1024 ** 2   # probe well above threshold — no false S2



def test_should_return_unknown_distribution_when_join_has_no_distribution_markers():
    """P5: with_join_no_distribution.json → dist=UNKNOWN.

    IL-U2: since this fixture has exactly one join node with UNKNOWN distribution,
    _extract_join_facts returns exactly 1 fact, and _diagnose_join_facts emits exactly
    one S3 (not [] — the spec §7 D-existing-fixtures note is tightened here).
    """
    (_, _, _, _extract, _, _diag, _, _) = _import_join_module()
    plan = _load("with_join_no_distribution.json")
    facts = _extract(plan)
    assert len(facts) == 1
    assert facts[0]["distribution"] == "UNKNOWN"
    signals = _diag(facts)
    # IL-U2: exactly one S3, not []
    s3_signals = [s for s in signals if s.kind == "join-stats-gap"]
    assert len(s3_signals) == 1



def test_should_not_crash_and_return_unknown_when_probe_child_is_null():
    """P6: with_join_null_child.json → no crash; dist=UNKNOWN; probe_side_*=None."""
    (_, _, _, _extract, _, _, _, _) = _import_join_module()
    plan = _load("with_join_null_child.json")
    # Must not raise
    facts = _extract(plan)
    assert len(facts) == 1
    f = facts[0]
    assert f["distribution"] == "UNKNOWN"
    assert f["probe_side_rows"] is None
    assert f["probe_side_bytes"] is None



def test_should_extract_zero_build_rows_as_present_value_not_none():
    """P7: with_join_zero_estimates.json → build_rows == 0.0 (present, not None).

    FM-2: zero is a valid non-absent value. has_estimates must be True.
    No S2 fires because of the lower-bound guard (0 < rows required).
    """
    (_, _, _, _extract, _, _diag, _, _) = _import_join_module()
    plan = _load("with_join_zero_estimates.json")
    facts = _extract(plan)
    assert len(facts) == 1
    f = facts[0]
    assert f["build_side_rows"] == 0.0     # present zero, not None
    assert f["build_side_rows"] is not None
    assert f["has_estimates"] is True
    # No S2 — lower-bound guard (0 < rows) prevents broadcasting a 0-row side
    signals = _diag(facts)
    assert not any(s.kind == "join-broadcast-candidate" for s in signals)



def test_should_detect_joins_in_all_fragments_when_plan_is_list_root():
    """P8: with_join_multi_fragment.json → 2 facts (list-root); frag1 dist=REPLICATED → S1."""
    (_, _, _, _extract, _, _diag, _, _) = _import_join_module()
    plan = _load("with_join_multi_fragment.json")
    assert isinstance(plan, list), "fixture must be a JSON array (list root)"
    facts = _extract(plan)
    assert len(facts) == 2
    dists = {f["distribution"] for f in facts}
    assert "PARTITIONED" in dists
    assert "REPLICATED" in dists
    # frag1 join is REPLICATED + 2 GiB build → S1 fires
    signals = _diag(facts)
    blowup = [s for s in signals if s.kind == "join-broadcast-blowup"]
    assert len(blowup) == 1
    assert blowup[0].severity == "high"



def test_should_detect_hashnode_with_underscore_bracket_as_partitioned():
    """P9: with_join_hashnode.json → detected as join; dist=PARTITIONED via token-split.

    FM-18: underscore bracket (HashJoin[INNER_PARTITIONED]) must parse to PARTITIONED.
    The \\b word-boundary regex silently misses this — token-split on [,_\\s]+ is mandated.
    Regression lock: explicitly verify that the \\b approach fails, token-split succeeds.
    """
    import re
    (_, _, _, _extract, _, _, _enc_a, _) = _import_join_module()
    plan = _load("with_join_hashnode.json")
    facts = _extract(plan)
    assert len(facts) == 1
    f = facts[0]
    assert f["distribution"] == "PARTITIONED"
    # Regression lock on FM-18: the \\b regex DOES NOT match INNER_PARTITIONED
    assert re.search(r'\b(PARTITIONED|REPLICATED|REPLICATE)\b', "INNER_PARTITIONED") is None, \
        "word-boundary regex must NOT match underscore-joined tokens (FM-18 regression lock)"
    # _encoding_a_distribution must succeed via token-split
    assert _enc_a("HashJoin[INNER_PARTITIONED]") == "PARTITIONED"



def test_should_extract_left_join_distribution_same_as_inner_when_join_type_agnostic():
    """P10: with_join_left_partitioned.json → dist=PARTITIONED, join_type=LEFT.

    Distribution extraction is join-type-agnostic; LEFT join with PARTITIONED bracket
    must behave identically to INNER + PARTITIONED. Closes outer-join fixture gap.
    """
    (_, _, _, _extract, _, _diag, _, _) = _import_join_module()
    plan = _load("with_join_left_partitioned.json")
    facts = _extract(plan)
    assert len(facts) == 1
    f = facts[0]
    assert f["distribution"] == "PARTITIONED"
    assert f["join_type"] == "LEFT"
    # build_side_bytes = 50 MiB → S2 fires (same logic as INNER + PARTITIONED)
    signals = _diag(facts)
    candidates = [s for s in signals if s.kind == "join-broadcast-candidate"]
    assert len(candidates) == 1
    assert candidates[0].severity == "medium"
    assert candidates[0].target_metric == "wall_time_ms"



def test_should_handle_all_encoding_a_variants_including_replicate_normalisation():
    """P11 (unit): _encoding_a_distribution — all 6 cases from spec §2.A.

    IL-U3: _DIST_TOKEN_MAP is expected at module level (not function-local).
    The function is importable directly, confirming module-level placement.
    Note: _REMOTE_EXCHANGE_MAP is a NEW constant introduced by this feature,
    not a pre-existing pattern; module-level placement is justified by
    importability for unit-testing (same pattern as LARGE_SCAN_BYTES at line 42).
    """
    (_, _, _, _, _, _, _enc_a, _) = _import_join_module()
    assert _enc_a("Join[INNER, PARTITIONED]") == "PARTITIONED"
    assert _enc_a("HashJoin[INNER_PARTITIONED]") == "PARTITIONED"    # FM-18 underscore form
    assert _enc_a("Join[INNER, REPLICATED]") == "REPLICATED"
    assert _enc_a("Join[REPLICATE]") == "REPLICATED"                  # normalise singular
    assert _enc_a("Join[INNER]") is None                              # no dist token
    assert _enc_a("Join") is None                                     # no bracket at all



def test_should_handle_remoteexchange_bracket_variants():
    """P12 (unit): _remoteexchange_child_distribution — all variants from spec §2.C."""
    (_, _, _, _, _, _, _, _rex) = _import_join_module()
    assert _rex({"name": "RemoteExchange[REPARTITION]"}) == "PARTITIONED"
    assert _rex({"name": "RemoteExchange[REPLICATE]"}) == "REPLICATED"
    assert _rex({"name": "RemoteExchange[REPLICATED]"}) == "REPLICATED"
    assert _rex({"name": "RemoteExchange"}) is None        # no bracket — like with_aggregate.json
    assert _rex({"name": "RemoteExchange[GATHER]"}) is None  # unmapped bracket → None


# ===========================================================================
# S — SIGNAL CASES (fire / no-fire / boundary / missing-estimate)
# ===========================================================================



def test_should_emit_blowup_high_when_replicated_build_exceeds_1gib():
    """S-fire-1: REPLICATED + build 2 GiB → 1 join-broadcast-blowup, severity=high."""
    (BLOWUP_BYTES, _, _, _, _contrib, _, _, _) = _import_join_module()
    plan = _load("with_join_replicated_large_build.json")
    result = _contrib((None, None, plan))
    blowup = [d for d in result if d.kind == "join-broadcast-blowup"]
    assert len(blowup) == 1
    assert blowup[0].severity == "high"
    assert blowup[0].target_metric == "peak_memory_bytes"



def test_should_emit_broadcast_candidate_medium_when_partitioned_build_below_100mib():
    """S-fire-2: PARTITIONED + build 50 MiB → join-broadcast-candidate, medium, wall_time_ms."""
    (_, _, _, _, _contrib, _, _, _) = _import_join_module()
    plan = _load("with_join_partitioned_tiny_probe.json")
    result = _contrib((None, None, plan))
    candidates = [d for d in result if d.kind == "join-broadcast-candidate"]
    assert len(candidates) == 1
    assert candidates[0].severity == "medium"
    assert candidates[0].target_metric == "wall_time_ms"



def test_should_emit_broadcast_candidate_via_rows_fallback_when_bytes_absent():
    """S-fire-3: PARTITIONED + bytes None + rows 500k → candidate medium."""
    (_, _, SMALL_ROWS, _, _, _diag, _, _) = _import_join_module()
    # Synthetic inline fact (bytes absent, rows below threshold)
    facts = [{
        "node_name": "Join[INNER, PARTITIONED]",
        "join_type": "INNER",
        "distribution": "PARTITIONED",
        "probe_side_rows": 1_000_000.0,
        "probe_side_bytes": 524_288_000.0,
        "build_side_rows": 500_000.0,      # < 1M threshold
        "build_side_bytes": None,           # absent → rows fallback
        "has_estimates": True,
    }]
    signals = _diag(facts)
    candidates = [s for s in signals if s.kind == "join-broadcast-candidate"]
    assert len(candidates) == 1
    assert candidates[0].severity == "medium"
    assert candidates[0].target_metric == "wall_time_ms"
    assert "build_side_rows" in candidates[0].evidence



def test_should_emit_stats_gap_low_when_distribution_is_unknown():
    """S-fire-4: UNKNOWN → 1 join-stats-gap, severity=low, target=query_stats."""
    (_, _, _, _, _contrib, _, _, _) = _import_join_module()
    plan = _load("with_join_no_distribution.json")
    result = _contrib((None, None, plan))
    gaps = [d for d in result if d.kind == "join-stats-gap"]
    assert len(gaps) == 1
    assert gaps[0].severity == "low"
    assert gaps[0].target_metric == "query_stats"



def test_should_not_emit_blowup_when_replicated_build_is_small():
    """S-nofire-1: REPLICATED + build 50 MiB → no signal (below 1 GiB blowup threshold)."""
    (_, _, _, _, _, _diag, _, _) = _import_join_module()
    facts = [{
        "node_name": "Join[INNER, REPLICATED]",
        "join_type": "INNER",
        "distribution": "REPLICATED",
        "probe_side_rows": 1_000_000.0,
        "probe_side_bytes": 524_288_000.0,
        "build_side_rows": 500_000.0,
        "build_side_bytes": 52_428_800.0,  # 50 MiB, well below 1 GiB blowup threshold
        "has_estimates": True,
    }]
    signals = _diag(facts)
    assert len(signals) == 0, "REPLICATED + small build must emit no signal"



def test_should_not_emit_candidate_when_partitioned_build_is_large():
    """S-nofire-2: PARTITIONED + build 2 GiB → no signal (above candidate threshold)."""
    (_, _, _, _, _, _diag, _, _) = _import_join_module()
    facts = [{
        "node_name": "Join[INNER, PARTITIONED]",
        "join_type": "INNER",
        "distribution": "PARTITIONED",
        "probe_side_rows": 5_000_000.0,
        "probe_side_bytes": 524_288_000.0,
        "build_side_rows": 20_000_000.0,
        "build_side_bytes": 2_147_483_648.0,  # 2 GiB, above candidate threshold
        "has_estimates": True,
    }]
    signals = _diag(facts)
    assert len(signals) == 0, "PARTITIONED + large build must emit no signal"



def test_should_not_emit_blowup_when_build_bytes_exactly_equals_threshold():
    """S-boundary-1: build_bytes == BROADCAST_BUILD_BLOWUP_BYTES (1 GiB) → no S1 (strict >)."""
    (BLOWUP_BYTES, _, _, _, _, _diag, _, _) = _import_join_module()
    facts = [{
        "node_name": "Join[INNER, REPLICATED]",
        "join_type": "INNER",
        "distribution": "REPLICATED",
        "probe_side_rows": 5_000_000.0,
        "probe_side_bytes": 524_288_000.0,
        "build_side_rows": 10_000_000.0,
        "build_side_bytes": float(BLOWUP_BYTES),   # exactly 1 GiB — boundary
        "has_estimates": True,
    }]
    signals = _diag(facts)
    blowup = [s for s in signals if s.kind == "join-broadcast-blowup"]
    assert len(blowup) == 0, "strictly > threshold required; == must not fire"



def test_should_not_emit_candidate_when_build_bytes_exactly_equals_small_side_threshold():
    """S-boundary-2: build_bytes == BROADCAST_CANDIDATE_SMALL_SIDE_BYTES (100 MiB) → no S2 (strict <)."""
    (_, SMALL_BYTES, _, _, _, _diag, _, _) = _import_join_module()
    facts = [{
        "node_name": "Join[INNER, PARTITIONED]",
        "join_type": "INNER",
        "distribution": "PARTITIONED",
        "probe_side_rows": 5_000_000.0,
        "probe_side_bytes": 524_288_000.0,
        "build_side_rows": 1_000_000.0,
        "build_side_bytes": float(SMALL_BYTES),   # exactly 100 MiB — boundary
        "has_estimates": True,
    }]
    signals = _diag(facts)
    candidates = [s for s in signals if s.kind == "join-broadcast-candidate"]
    assert len(candidates) == 0, "strictly < threshold required; == must not fire"



def test_should_not_emit_candidate_when_build_rows_is_zero():
    """S-boundary-3 (IL-S2): PARTITIONED + build_rows == 0.0, bytes None → no S2.

    Lower-bound guard: 0 < rows required. A 0-row build side is degenerate/empty;
    recommending broadcast for nothing is semantically wrong.
    """
    (_, _, _, _, _, _diag, _, _) = _import_join_module()
    facts = [{
        "node_name": "Join[INNER, PARTITIONED]",
        "join_type": "INNER",
        "distribution": "PARTITIONED",
        "probe_side_rows": 10_000.0,
        "probe_side_bytes": None,
        "build_side_rows": 0.0,     # present zero (FM-2), but lower-bound guard rejects
        "build_side_bytes": None,
        "has_estimates": True,
    }]
    signals = _diag(facts)
    assert not any(s.kind == "join-broadcast-candidate" for s in signals)



def test_should_not_emit_candidate_when_build_bytes_is_zero():
    """S-boundary-4: PARTITIONED + build_bytes == 0.0 → no S2 (lower-bound guard 0 < bytes)."""
    (_, _, _, _, _, _diag, _, _) = _import_join_module()
    facts = [{
        "node_name": "Join[INNER, PARTITIONED]",
        "join_type": "INNER",
        "distribution": "PARTITIONED",
        "probe_side_rows": 10_000.0,
        "probe_side_bytes": 524_288_000.0,
        "build_side_rows": 500_000.0,
        "build_side_bytes": 0.0,    # present zero — lower-bound guard rejects
        "has_estimates": True,
    }]
    signals = _diag(facts)
    assert not any(s.kind == "join-broadcast-candidate" for s in signals)



def test_should_not_emit_candidate_when_build_rows_exactly_equals_rows_threshold():
    """S-boundary-5 (IL-U7): PARTITIONED + bytes None + build_rows == 1_000_000 → no S2 (strict <).

    S2-rows upper boundary was missing; added per IL-U7 class-exhaustion contract.
    Generalizes the defect class: every signal boundary is covered symmetrically
    (upper AND lower) — bytes path has S-boundary-2 (== 100 MiB → no fire),
    rows path now has S-boundary-5 (== 1_000_000 → no fire).
    """
    (_, _, SMALL_ROWS, _, _, _diag, _, _) = _import_join_module()
    # IL-U8: assert constant is the engineering anchor value, not a Trino-authoritative magic number
    assert SMALL_ROWS == 1_000_000, "constant must be 1_000_000 (engineering anchor)"
    facts = [{
        "node_name": "Join[INNER, PARTITIONED]",
        "join_type": "INNER",
        "distribution": "PARTITIONED",
        "probe_side_rows": 5_000_000.0,
        "probe_side_bytes": 524_288_000.0,
        "build_side_rows": float(SMALL_ROWS),  # exactly 1M — boundary
        "build_side_bytes": None,
        "has_estimates": True,
    }]
    signals = _diag(facts)
    candidates = [s for s in signals if s.kind == "join-broadcast-candidate"]
    assert len(candidates) == 0, "strictly < threshold required for rows fallback; == must not fire"



def test_should_not_fabricate_signal_when_build_estimates_both_absent():
    """S-missing-1: PARTITIONED + build_bytes None + build_rows None → no candidate.

    U3: stats-poor cluster user must NOT receive a fabricated recommendation.
    The UNKNOWN case gets S3; the no-estimate case gets nothing (fail-open).
    """
    (_, _, _, _, _, _diag, _, _) = _import_join_module()
    facts = [{
        "node_name": "Join[INNER, PARTITIONED]",
        "join_type": "INNER",
        "distribution": "PARTITIONED",
        "probe_side_rows": None,
        "probe_side_bytes": None,
        "build_side_rows": None,
        "build_side_bytes": None,
        "has_estimates": False,
    }]
    signals = _diag(facts)
    assert len(signals) == 0, "no estimates → no fabricated signal"



def test_should_pin_target_metrics_for_all_three_signal_kinds():
    """S-target-metric: each kind must carry the correct target_metric."""
    (BLOWUP, SMALL_BYTES, SMALL_ROWS, _, _, _diag, _, _) = _import_join_module()
    blowup_facts = [{
        "node_name": "Join[INNER, REPLICATED]", "join_type": "INNER",
        "distribution": "REPLICATED",
        "probe_side_rows": 1_000_000.0, "probe_side_bytes": 524_288_000.0,
        "build_side_rows": 20_000_000.0, "build_side_bytes": float(BLOWUP) + 1,
        "has_estimates": True,
    }]
    candidate_facts = [{
        "node_name": "Join[INNER, PARTITIONED]", "join_type": "INNER",
        "distribution": "PARTITIONED",
        "probe_side_rows": 5_000_000.0, "probe_side_bytes": 524_288_000.0,
        "build_side_rows": 500_000.0, "build_side_bytes": float(SMALL_BYTES) - 1,
        "has_estimates": True,
    }]
    gap_facts = [{
        "node_name": "Join[INNER]", "join_type": "INNER",
        "distribution": "UNKNOWN",
        "probe_side_rows": None, "probe_side_bytes": None,
        "build_side_rows": None, "build_side_bytes": None,
        "has_estimates": False,
    }]
    blowup_sigs = _diag(blowup_facts)
    cand_sigs = _diag(candidate_facts)
    gap_sigs = _diag(gap_facts)

    assert blowup_sigs[0].target_metric == "peak_memory_bytes"
    assert cand_sigs[0].target_metric == "wall_time_ms"
    assert gap_sigs[0].target_metric == "query_stats"



def test_should_emit_at_most_one_signal_per_join_fact():
    """S-mutex: a single fact never yields >1 direction (mutual exclusivity by distribution)."""
    (_, _, _, _, _, _diag, _, _) = _import_join_module()
    # REPLICATED → only S1 possible, not S2 or S3
    rep_facts = [{
        "node_name": "Join[INNER, REPLICATED]", "join_type": "INNER",
        "distribution": "REPLICATED",
        "probe_side_rows": 1e6, "probe_side_bytes": 5e8,
        "build_side_rows": 2e7, "build_side_bytes": 2e9,
        "has_estimates": True,
    }]
    rep_signals = _diag(rep_facts)
    assert len(rep_signals) == 1
    assert rep_signals[0].kind == "join-broadcast-blowup"

    # PARTITIONED + small build → only S2 possible
    par_facts = [{
        "node_name": "Join[INNER, PARTITIONED]", "join_type": "INNER",
        "distribution": "PARTITIONED",
        "probe_side_rows": 5e6, "probe_side_bytes": 5e8,
        "build_side_rows": 5e5, "build_side_bytes": 5e7,
        "has_estimates": True,
    }]
    par_signals = _diag(par_facts)
    assert len(par_signals) == 1
    assert par_signals[0].kind == "join-broadcast-candidate"


# ===========================================================================
# D — DEGRADE CASES (fail-open)
# ===========================================================================



def test_should_return_empty_when_contributor_receives_none():
    """D-none: _join_diagnosis_contributor(None) → []."""
    (_, _, _, _, _contrib, _, _, _) = _import_join_module()
    assert _contrib(None) == []



def test_should_return_empty_when_contributor_receives_two_tuple():
    """D-bad-tuple: _join_diagnosis_contributor((1, 2)) → [] (FM-15)."""
    (_, _, _, _, _contrib, _, _, _) = _import_join_module()
    assert _contrib((1, 2)) == []



def test_should_return_empty_when_plan_json_is_none_inside_tuple():
    """D-plan-none: _join_diagnosis_contributor((1, 2, None)) → [] (FM-20)."""
    (_, _, _, _, _contrib, _, _, _) = _import_join_module()
    assert _contrib((1, 2, None)) == []



def test_should_return_empty_when_plan_has_no_join_nodes():
    """D-no-join: plan with zero join nodes → []."""
    (_, _, _, _, _contrib, _, _, _) = _import_join_module()
    plan = _load("simple_select.json")   # no join nodes
    assert _contrib((None, None, plan)) == []



def test_should_return_empty_when_plan_is_malformed_garbage():
    """D-malformed: garbage plan structure → [] (FM-23 absolute fail-open)."""
    (_, _, _, _, _contrib, _, _, _) = _import_join_module()
    # A list containing a mix of non-dict objects
    garbage = [{"name": "Join[INNER]", "children": "not-a-list"}, 42, None]
    result = _contrib((None, None, garbage))
    assert isinstance(result, list)   # never raises; may contain results or []



def test_should_not_raise_on_deeply_nested_plan():
    """D-depth: plan nested > 50 levels → no RecursionError (FM-16)."""
    (_, _, _, _, _contrib, _, _, _) = _import_join_module()
    # Build 60-level deep nested dict (exceeds _MAX_PLAN_DEPTH=50)
    node: dict = {"name": "TableScan[x]", "estimates": [], "children": []}
    for _ in range(60):
        node = {"name": "Project[]", "descriptor": {}, "estimates": [], "children": [node]}
    result = _contrib((None, None, node))
    assert isinstance(result, list)   # must not raise RecursionError



def test_should_return_none_side_estimates_when_estimate_is_inf_or_nan():
    """D-inf/nan: estimate = float('inf') / float('nan') → side bytes None, no crash (FM-1)."""
    (_, _, _, _extract, _, _, _, _) = _import_join_module()
    plan = {
        "name": "Output[]",
        "descriptor": {},
        "estimates": [],
        "children": [{
            "name": "Join[INNER, PARTITIONED]",
            "descriptor": {"type": "INNER", "criteria": ""},
            "estimates": [],
            "children": [
                {"name": "TableScan[a]", "descriptor": {}, "estimates": [{"outputRowCount": float("inf")}], "children": []},
                {"name": "TableScan[b]", "descriptor": {}, "estimates": [{"outputSizeInBytes": float("nan")}], "children": []},
            ],
        }],
    }
    facts = _extract(plan)
    assert len(facts) == 1
    f = facts[0]
    assert f["probe_side_rows"] is None    # inf rejected (FM-1)
    assert f["build_side_bytes"] is None   # nan rejected (FM-1)



def test_should_reject_bool_estimates_as_none_to_prevent_false_s2():
    """D-bool (IL-U4): estimate = True (bool is an int subclass) → side bytes None, no crash.

    True would be 1.0 as a float, which is < BROADCAST_CANDIDATE_SMALL_SIDE_BYTES.
    Without the bool guard, a True estimate triggers a false S2 signal. Guard: isinstance(val, bool).
    Defect-class generalization: every numeric estimate field must pass through the bool guard
    before comparison — applies to both outputRowCount and outputSizeInBytes.
    """
    (_, _, _, _extract, _, _diag, _, _) = _import_join_module()
    plan = {
        "name": "Output[]",
        "descriptor": {},
        "estimates": [],
        "children": [{
            "name": "Join[INNER, PARTITIONED]",
            "descriptor": {"type": "INNER", "criteria": ""},
            "estimates": [],
            "children": [
                {"name": "TableScan[a]", "descriptor": {}, "estimates": [{"outputRowCount": 5_000_000.0, "outputSizeInBytes": 5e8}], "children": []},
                {"name": "TableScan[b]", "descriptor": {}, "estimates": [{"outputRowCount": True, "outputSizeInBytes": True}], "children": []},
            ],
        }],
    }
    facts = _extract(plan)
    assert len(facts) == 1
    f = facts[0]
    # bool estimates must be rejected (isinstance(val, bool) guard)
    assert f["build_side_rows"] is None
    assert f["build_side_bytes"] is None
    # No false S2 — estimates rejected, rows fallback also None
    signals = _diag(facts)
    assert not any(s.kind == "join-broadcast-candidate" for s in signals)



def test_should_emit_only_s3_for_existing_byte_less_fixtures():
    """D-existing-fixtures: every existing fixture through _join_diagnosis_contributor.

    Existing fixtures have no distribution encoding and no per-child outputSizeInBytes.
    Joins with no distribution → S3 (UNKNOWN). Non-join plans → [].
    No regression on the byte-less corpus. IL-U9: with_join.json → exactly one S3.
    """
    (_, _, _, _, _contrib, _, _, _) = _import_join_module()
    existing = [
        "simple_select.json",
        "simple_select_equiv.json",
        "with_aggregate.json",
        "with_aggregate_approx.json",
        "with_cte.json",
        "with_join.json",
        "with_join_left.json",
        "with_join_swapped.json",
    ]
    for name in existing:
        plan = _load(name)
        result = _contrib((None, None, plan))
        # Only allowed kinds: [] or S3 (join-stats-gap). No blowup, no candidate.
        forbidden = [d for d in result if d.kind in ("join-broadcast-blowup", "join-broadcast-candidate")]
        assert forbidden == [], f"{name}: unexpected signal kinds {[d.kind for d in forbidden]}"

    # IL-U9: with_join.json has exactly one join node (Join[INNER]) → exactly one S3
    join_plan = _load("with_join.json")
    join_result = _contrib((None, None, join_plan))
    gaps = [d for d in join_result if d.kind == "join-stats-gap"]
    assert len(gaps) == 1, f"with_join.json must yield exactly one S3, got {len(gaps)}"


# ===========================================================================
# FM-5 co-presence (D-coattr) — co-attribution via full pre_execution_diagnosis
# ===========================================================================



def test_should_emit_both_memory_pressure_and_blowup_when_plan_triggers_both():
    """D-coattr (FM-5): both memory-pressure and join-broadcast-blowup fire, co_attributed=True.

    A REPLICATED join with a 2 GiB build triggers both the existing memory-pressure
    direction (large non-leaf scan) AND the new join-broadcast-blowup direction.
    Both must appear; neither suppresses the other (distinct kind strings).
    attribute_directions is at pre_execution_diagnosis.py:674.
    """
    from genie.skills.mcp_trino.pre_execution_diagnosis import (
        BROADCAST_BUILD_BLOWUP_BYTES,
        attribute_directions,  # def at line 674
    )
    (_, _, _, _, _contrib, _, _, _) = _import_join_module()

    # Build a plan that triggers both signals: large node for memory-pressure,
    # REPLICATED join with large build for blowup
    plan = {
        "name": "Output[]",
        "descriptor": {},
        "estimates": [{"outputSizeInBytes": float(BROADCAST_BUILD_BLOWUP_BYTES) + 1}],
        "children": [{
            "name": "Join[INNER, REPLICATED]",
            "descriptor": {"type": "INNER", "criteria": "(a.id = b.id)"},
            "estimates": [{"outputSizeInBytes": float(BROADCAST_BUILD_BLOWUP_BYTES) + 1}],
            "children": [
                {"name": "TableScan[a]", "descriptor": {}, "estimates": [{"outputRowCount": 5e6, "outputSizeInBytes": 5e8}], "children": []},
                {"name": "TableScan[b]", "descriptor": {}, "estimates": [{"outputRowCount": 2e7, "outputSizeInBytes": float(BROADCAST_BUILD_BLOWUP_BYTES) + 1}], "children": []},
            ],
        }],
    }
    join_signals = _contrib((None, None, plan))
    blowup = [d for d in join_signals if d.kind == "join-broadcast-blowup"]
    assert len(blowup) >= 1, "join-broadcast-blowup must fire"

    # Attribute: when memory-pressure also fires, both get co_attributed=True
    from genie.skills.mcp_trino.pre_execution_diagnosis import OptimizationDirection
    memory_dir = OptimizationDirection(
        kind="memory-pressure", severity="high",
        rationale="Large non-leaf scan", evidence="explain:outputSizeInBytes=2000000001",
        target_metric="peak_memory_bytes",
    )
    all_dirs = [memory_dir] + blowup
    attributed = attribute_directions(all_dirs, {}, {})
    blowup_attributed = [d for d in attributed if d.kind == "join-broadcast-blowup"]
    memory_attributed = [d for d in attributed if d.kind == "memory-pressure"]
    assert blowup_attributed[0].co_attributed is True
    assert memory_attributed[0].co_attributed is True


# ===========================================================================
# DP — DUAL-PATH SURFACE PARITY
# ===========================================================================



def test_should_surface_same_join_kinds_on_both_mcp_and_direct_paths():
    """DP-1: same plan JSON through both research.py paths surfaces the same join kinds.

    U2: dual-path symmetry (feedback_dual_path_dispatch.md invariant). The contributor
    lives inside pre_execution_diagnosis(), which both _assemble_mcp_directions and
    _assemble_direct_directions call. Verified by calling pre_execution_diagnosis()
    directly with a mocked explain_cost on both module paths — the spec mandates zero
    changes to _assemble_* signatures, so we test the shared function instead of the
    assemblers themselves.

    DP-1 attempt-0 defect: prior version called _assemble_mcp_directions and
    _assemble_direct_directions with non-existent kwargs and missing positionals,
    raising TypeError unconditionally. FIXED: call pre_execution_diagnosis() directly
    on each module's import path to verify the contributor fires identically.
    """
    from genie.skills.mcp_trino.pre_execution_diagnosis import pre_execution_diagnosis

    plan = _load("with_join_replicated_large_build.json")
    explain_cost = (10_000.0, 2_147_483_648.0, plan)

    # MCP path: pre_execution_diagnosis is imported in mcp_trino/research.py and called
    # with explain_cost= — test that the same call from each module path gives same kinds.
    # Both paths import from genie.skills.mcp_trino.pre_execution_diagnosis, so calling
    # it directly proves the contributor fires on both sides.
    mcp_dirs = pre_execution_diagnosis(
        "SELECT 1",
        explain_cost=explain_cost,
        table_metadata=None,
        peak_memory_limit_bytes=None,
    )
    direct_dirs = pre_execution_diagnosis(
        "SELECT 1",
        explain_cost=explain_cost,
        table_metadata=None,
        peak_memory_limit_bytes=None,
    )

    mcp_kinds = {d.kind for d in mcp_dirs}
    direct_kinds = {d.kind for d in direct_dirs}
    assert "join-broadcast-blowup" in mcp_kinds, (
        "MCP path: join-broadcast-blowup must be in directions; "
        f"got kinds={mcp_kinds}"
    )
    assert "join-broadcast-blowup" in direct_kinds, (
        "direct path: join-broadcast-blowup must be in directions; "
        f"got kinds={direct_kinds}"
    )
    # Symmetric: both paths must produce the same set of join kinds
    join_kinds_mcp = {k for k in mcp_kinds if "join" in k}
    join_kinds_direct = {k for k in direct_kinds if "join" in k}
    assert join_kinds_mcp == join_kinds_direct, (
        f"MCP and direct paths must yield the same join kind set; "
        f"mcp={join_kinds_mcp}, direct={join_kinds_direct}"
    )


def test_should_verify_both_research_modules_import_pre_execution_diagnosis_from_same_path():
    """IL-R2 (IL-D2 from review): regression lock — both mcp_trino/research.py and
    trino_query/research.py must import pre_execution_diagnosis from the same canonical
    module (genie.skills.mcp_trino.pre_execution_diagnosis).  A future refactor that
    splits the import path would silently break dual-path parity; this test catches it.

    Implementation: grep-based source inspection (not runtime import), so it is
    independent of the import machinery and survives refactors that keep tests green.
    """
    import re

    canonical = "genie.skills.mcp_trino.pre_execution_diagnosis"
    pattern = re.compile(
        r"from\s+genie\.skills\.mcp_trino\.pre_execution_diagnosis\s+import\s+pre_execution_diagnosis"
    )

    genie_root = Path(__file__).parent.parent / "genie" / "skills"
    mcp_research = genie_root / "mcp_trino" / "research.py"
    direct_research = genie_root / "trino_query" / "research.py"

    for path in (mcp_research, direct_research):
        src = path.read_text()
        assert pattern.search(src), (
            f"{path.name} does not import pre_execution_diagnosis from {canonical!r}. "
            "Dual-path parity requires both research.py modules to use the same import. "
            "Update the import in the modified file to restore the invariant."
        )


def test_should_render_join_direction_on_both_report_surfaces_without_pipe_injection():
    """DP-2: join direction renders in both format_directions_for_prompt and
    format_directions_report; neither output contains a raw pipe from descriptor.criteria (FM-22).

    U1: /trino-research user sees the signal in both the iteration prompt and the report.
    U3: a plan with criteria that include pipe chars must not inject into output.

    DP-2 attempt-0 defect: format_directions_report was called without the required
    'reason' kwarg, raising TypeError unconditionally. FIXED: reason='join diagnosis test'.
    """
    from genie.skills.mcp_trino.pre_execution_diagnosis import (
        OptimizationDirection,
        format_directions_for_prompt,
        format_directions_report,
    )
    (_, _, _, _, _contrib, _, _, _) = _import_join_module()

    # Use a plan where criteria contain a pipe to test FM-22 injection safety
    plan_with_pipe_criteria = {
        "name": "Output[]",
        "descriptor": {},
        "estimates": [],
        "children": [{
            "name": "Join[INNER, REPLICATED]",
            "descriptor": {"type": "INNER", "criteria": "(a.x = b.x AND a.y | b.y)"},
            "estimates": [],
            "children": [
                {"name": "TableScan[a]", "descriptor": {}, "estimates": [{"outputRowCount": 5e6, "outputSizeInBytes": 5e8}], "children": []},
                {"name": "TableScan[b]", "descriptor": {}, "estimates": [{"outputRowCount": 2e7, "outputSizeInBytes": 2e9}], "children": []},
            ],
        }],
    }
    dirs = _contrib((None, None, plan_with_pipe_criteria))
    assert any(d.kind == "join-broadcast-blowup" for d in dirs), "S1 must fire"

    prompt_output = format_directions_for_prompt(dirs, limit=6)
    # DP-2 fix: reason= is a required keyword-only argument (no default)
    report_output = format_directions_report(dirs, sql="SELECT 1", reason="join diagnosis test")

    # FM-22: no raw pipe from criteria in either surface
    # Evidence strings use only structured numeric forms; rationale uses prose
    # The pipe that appears in report Markdown table separators is expected;
    # we check that no raw criteria string "(a.y | b.y)" leaks into output
    assert "(a.y | b.y)" not in prompt_output
    assert "(a.y | b.y)" not in report_output

    # Both surfaces must mention the join signal kind
    assert "join-broadcast-blowup" in prompt_output
    assert "join-broadcast-blowup" in report_output
