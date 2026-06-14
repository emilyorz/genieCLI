"""tests/test_seed_validation_integration.py — §3.1 NORMATIVE call-site coupling tests.

What this file tests
--------------------
``_seed_decompose_and_select`` is the production function that BOTH call sites
(MCP STANDARD loop and --direct STANDARD loop) use to set best_sql + best_measure
as a coupled tuple.  Every test here calls ``_seed_decompose_and_select`` directly
with a stubbed ``produce_fn`` / ``measure_fn`` — no live Trino, no real LLM.

The key property under test: the returned ``(winner_sql, winner_measure)`` pair
is ALWAYS from the same decision arm.  A test cannot pass when flag=0 and fail
when flag=1 because the FLAG-OFF test explicitly asserts the stubs are NEVER
called — if an autouse fixture had already disabled the path, the stub would
also not be called and the test would give a false green.  The
``test_flag_off_stubs_never_called`` test is the regression guard that makes
flag-ON coverage real: it proves the only way the path is skipped is when
``flag_enabled=False``, not silently by an env-var the tests don't control.

Tests pass identically with GENIE_V48_SEED_DECOMPOSE=0 set externally BECAUSE
these tests bypass the env-var entirely — they pass ``flag_enabled`` explicitly.
That is intentional: the env-var is the production guard; these tests exercise
the function body directly, which is the right seam.

Wiring test
-----------
``test_both_call_sites_reference_seed_decompose_and_select`` asserts (at source
level) that both ``mcp_trino/research.py`` and ``trino_query/research.py``
contain a call to ``_seed_decompose_and_select``, so a future refactor that
removes either call site will break a test.
"""
from __future__ import annotations

from unittest.mock import MagicMock, call

import pytest

from genie.skills.mcp_trino.research import (
    MeasureResult,
    RunMetrics,
    _seed_decompose_and_select,
)


# ── Helpers ──────────────────────────────────────────────────────────────────

def _make_metrics(**kw) -> RunMetrics:
    m = MagicMock(spec=RunMetrics)
    for k, v in kw.items():
        setattr(m, k, v)
    return m


def _mr(median: float, rows: list, metrics=None) -> MeasureResult:
    """Build a minimal MeasureResult."""
    return MeasureResult(
        median_metric=median,
        samples=[median],
        row_count=len(rows),
        rows=rows,
        columns=[],
        metrics=metrics or _make_metrics(),
    )


_ORIG = "SELECT * FROM t"
_SEED = "SELECT id FROM t"
_ROWS_AB  = [(1,), (2,), (3,)]   # same content as _ROWS_AB2
_ROWS_AB2 = [(1,), (2,), (3,)]   # equivalent
_ROWS_C   = [(1,), (99,)]        # different → row-mismatch


def _produce_returns(recomposed_sql: str):
    """Stub produce_fn that returns a specific recomposed SQL."""
    return lambda sql: (recomposed_sql, [], [], None)


def _produce_unchanged():
    """Stub produce_fn that returns the original unchanged (no-op decompose)."""
    return lambda sql: (sql, [], [], None)


# ── T-CRUX-REJECT-SEED ───────────────────────────────────────────────────────

class TestReject:
    """Rows differ → seed rejected; winner must be (original_sql, baseline_measure)."""

    def test_reject_returns_original_sql(self):
        baseline = _mr(80_000, _ROWS_AB)
        seed_mr   = _mr(50_000, _ROWS_C)   # divergent rows → must reject

        winner_sql, winner_mr, _ = _seed_decompose_and_select(
            _ORIG, baseline,
            produce_fn=_produce_returns(_SEED),
            measure_fn=lambda s: seed_mr,
            flag_enabled=True,
        )
        assert winner_sql == _ORIG, "rejected seed must yield original SQL"

    def test_reject_winner_measure_IS_baseline(self):
        """§3.1 anti-tautology: winner_measure must be the baseline object, not seed."""
        baseline = _mr(80_000, _ROWS_AB)
        seed_mr   = _mr(50_000, _ROWS_C)

        _, winner_mr, _ = _seed_decompose_and_select(
            _ORIG, baseline,
            produce_fn=_produce_returns(_SEED),
            measure_fn=lambda s: seed_mr,
            flag_enabled=True,
        )
        # Must be the baseline MeasureResult, not the seed MeasureResult.
        assert winner_mr is baseline, (
            "rejected seed: winner_measure must be baseline (not seed) — "
            "otherwise data_consistent would compare baseline.rows vs seed.rows "
            "which is a baseline-vs-baseline tautology when the loop never keeps anything"
        )

    def test_reject_winner_metric_equals_baseline_metric(self):
        baseline = _mr(80_000, _ROWS_AB)
        seed_mr   = _mr(50_000, _ROWS_C)

        _, winner_mr, _ = _seed_decompose_and_select(
            _ORIG, baseline,
            produce_fn=_produce_returns(_SEED),
            measure_fn=lambda s: seed_mr,
            flag_enabled=True,
        )
        assert winner_mr.median_metric == 80_000


# ── T-CRUX-ACCEPT-SEED ───────────────────────────────────────────────────────

class TestAccept:
    """Rows equivalent AND faster → seed accepted; winner is (seed_sql, seed_measure)."""

    def test_accept_returns_seed_sql(self):
        baseline = _mr(80_000, _ROWS_AB)
        seed_mr   = _mr(50_000, _ROWS_AB2)  # equiv rows, faster

        winner_sql, _, _ = _seed_decompose_and_select(
            _ORIG, baseline,
            produce_fn=_produce_returns(_SEED),
            measure_fn=lambda s: seed_mr,
            flag_enabled=True,
        )
        assert winner_sql == _SEED

    def test_accept_winner_measure_IS_seed(self):
        """AC-5: equivalence checked on baseline.rows vs seed_measure.rows (not baseline.rows)."""
        baseline = _mr(80_000, _ROWS_AB)
        seed_mr   = _mr(50_000, _ROWS_AB2)

        _, winner_mr, _ = _seed_decompose_and_select(
            _ORIG, baseline,
            produce_fn=_produce_returns(_SEED),
            measure_fn=lambda s: seed_mr,
            flag_enabled=True,
        )
        assert winner_mr is seed_mr, (
            "accepted seed: winner_measure must be the seed MeasureResult, not baseline"
        )

    def test_accept_winner_metric_equals_seed_metric(self):
        baseline = _mr(80_000, _ROWS_AB)
        seed_mr   = _mr(50_000, _ROWS_AB2)

        _, winner_mr, _ = _seed_decompose_and_select(
            _ORIG, baseline,
            produce_fn=_produce_returns(_SEED),
            measure_fn=lambda s: seed_mr,
            flag_enabled=True,
        )
        assert winner_mr.median_metric == 50_000


# ── T-SEED-EQUIV-SLOWER (SCR-1) ──────────────────────────────────────────────

class TestEquivSlower:
    """Rows equivalent but seed SLOWER → rejected; winner is (original, baseline)."""

    def test_equiv_slower_returns_original_sql(self):
        baseline = _mr(80_000, _ROWS_AB)
        seed_mr   = _mr(90_000, _ROWS_AB2)  # equiv but slower

        winner_sql, _, _ = _seed_decompose_and_select(
            _ORIG, baseline,
            produce_fn=_produce_returns(_SEED),
            measure_fn=lambda s: seed_mr,
            flag_enabled=True,
        )
        assert winner_sql == _ORIG

    def test_equiv_slower_winner_measure_is_baseline(self):
        baseline = _mr(80_000, _ROWS_AB)
        seed_mr   = _mr(90_000, _ROWS_AB2)

        _, winner_mr, _ = _seed_decompose_and_select(
            _ORIG, baseline,
            produce_fn=_produce_returns(_SEED),
            measure_fn=lambda s: seed_mr,
            flag_enabled=True,
        )
        assert winner_mr is baseline

    def test_equiv_exactly_equal_metric_not_accepted(self):
        """Strict < required; equal metric must not be accepted (SCR-1)."""
        baseline = _mr(80_000, _ROWS_AB)
        seed_mr   = _mr(80_000, _ROWS_AB2)  # same speed

        winner_sql, winner_mr, _ = _seed_decompose_and_select(
            _ORIG, baseline,
            produce_fn=_produce_returns(_SEED),
            measure_fn=lambda s: seed_mr,
            flag_enabled=True,
        )
        assert winner_sql == _ORIG
        assert winner_mr is baseline


# ── T-SEED-NOKEEP-WINNER ─────────────────────────────────────────────────────

class TestNoKeep:
    """When produce returns unchanged SQL, seed is skipped; winner is (original, baseline)."""

    def test_unchanged_produce_returns_original(self):
        baseline = _mr(80_000, _ROWS_AB)

        winner_sql, winner_mr, _ = _seed_decompose_and_select(
            _ORIG, baseline,
            produce_fn=_produce_unchanged(),
            measure_fn=lambda s: (_ for _ in ()).throw(AssertionError("measure_fn must NOT be called when produce is unchanged")),
            flag_enabled=True,
        )
        assert winner_sql == _ORIG
        assert winner_mr is baseline


# ── FLAG-OFF spy (regression guard) ──────────────────────────────────────────

class TestFlagOff:
    """flag_enabled=False → produce_fn and measure_fn are NEVER called.

    This is the test that makes flag-ON coverage real: it proves the only
    way the path is skipped is via ``flag_enabled=False``, not by the env-var.
    If the autouse conftest fixture had magically neutered the ON-path, the
    stub would also not be called and this test's spy assertion would still
    pass — making it appear the ON-path is covered when it isn't.

    Combined with the tests above (which call with flag_enabled=True and assert
    on the returned tuple), the set proves: flag-OFF skips the stubs, flag-ON
    reaches them and returns a specific coupled pair.
    """

    def test_flag_off_stubs_never_called(self):
        baseline = _mr(80_000, _ROWS_AB)
        produce_calls: list[str] = []
        measure_calls: list[str] = []

        def spy_produce(sql: str):
            produce_calls.append(sql)
            return (_SEED, [], [], None)

        def spy_measure(sql: str) -> MeasureResult:
            measure_calls.append(sql)
            return _mr(50_000, _ROWS_AB2)

        winner_sql, winner_mr, _ = _seed_decompose_and_select(
            _ORIG, baseline,
            produce_fn=spy_produce,
            measure_fn=spy_measure,
            flag_enabled=False,   # OFF
        )
        assert produce_calls == [], "produce_fn must NOT be called when flag_enabled=False"
        assert measure_calls == [], "measure_fn must NOT be called when flag_enabled=False"
        assert winner_sql == _ORIG
        assert winner_mr is baseline

    def test_flag_on_produce_IS_called(self):
        """Counterpart: flag_enabled=True → produce_fn IS called.

        Together with test_flag_off_stubs_never_called this proves that
        flag_enabled=True and flag_enabled=False produce different behaviour —
        not that both silently skip the same path.
        """
        baseline = _mr(80_000, _ROWS_AB)
        produce_calls: list[str] = []

        def spy_produce(sql: str):
            produce_calls.append(sql)
            return (sql, [], [], None)   # return unchanged → measure not called

        _seed_decompose_and_select(
            _ORIG, baseline,
            produce_fn=spy_produce,
            measure_fn=lambda s: _mr(50_000, _ROWS_AB2),
            flag_enabled=True,   # ON
        )
        assert produce_calls == [_ORIG], "produce_fn must be called when flag_enabled=True"


# ── Wiring test — call sites reference _seed_decompose_and_select ─────────────

class TestCallSiteWiring:
    """Assert at source level that both production modules call _seed_decompose_and_select.

    If a future refactor removes the call from either module, this test breaks,
    preventing silent decoupling of the §3.1 locus.
    """

    def test_mcp_research_calls_seed_decompose_and_select(self):
        import pathlib
        src = pathlib.Path(__file__).parent.parent / "genie/skills/mcp_trino/research.py"
        text = src.read_text()
        assert "_seed_decompose_and_select(" in text, (
            "mcp_trino/research.py must call _seed_decompose_and_select — "
            "the §3.1 locus was removed from the MCP call site"
        )

    def test_direct_research_calls_seed_decompose_and_select(self):
        import pathlib
        src = pathlib.Path(__file__).parent.parent / "genie/skills/trino_query/research.py"
        text = src.read_text()
        assert "_seed_decompose_and_select(" in text, (
            "trino_query/research.py must call _seed_decompose_and_select — "
            "the §3.1 locus was removed from the --direct call site"
        )

    def test_seed_decompose_and_select_is_exported_from_mcp_research(self):
        from genie.skills.mcp_trino.research import _seed_decompose_and_select
        assert callable(_seed_decompose_and_select)

    def test_seed_decompose_and_select_returns_three_tuple(self):
        baseline = _mr(80_000, _ROWS_AB)
        result = _seed_decompose_and_select(
            _ORIG, baseline,
            produce_fn=_produce_unchanged(),
            measure_fn=lambda s: _mr(50_000, _ROWS_AB2),
            flag_enabled=True,
        )
        assert len(result) == 3, "must return (winner_sql, winner_measure, events)"
        winner_sql, winner_mr, events = result
        assert isinstance(winner_sql, str)
        assert isinstance(winner_mr, MeasureResult)
        assert isinstance(events, list)
