"""v32 T1: per-iteration re-diagnosis.

The pre-execution diagnosis injected into the system prompt describes the
ORIGINAL query. Once an improvement changes ``best_sql`` those directions are
stale, so the loop must re-diagnose the current ``best_sql`` (zero query cost)
and feed fresh directions into that turn's context.

Strategy: drive the real ``--direct`` optimization loop for two iterations with
a mocked provider + ``_measure``. Iteration 1 returns a rewrite (``SELECT *``)
that the static analyzer flags (``select-star``) — which the ORIGINAL query did
not. The test asserts iteration 2's context carries the fresh ``select-star``
direction that only a re-diagnosis of the changed ``best_sql`` could produce.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from genie.skills.trino_query import QueryMetrics
from genie.skills.trino_query import research as direct_research

_ORIGINAL = "SELECT a FROM t"          # no static findings
_REWRITE = "SELECT * FROM t"           # triggers r2 select-star


def _measure_side_effect(sql, *args, **kwargs):
    """Baseline (original) is slow; the rewrite is faster + result-equivalent."""
    improved = sql.strip() != _ORIGINAL
    return {
        "median": 50.0 if improved else 100.0,
        "samples": [50.0 if improved else 100.0],
        "row_count": 1,
        "rows": [(1,)],
        "metrics": QueryMetrics(wall_time_ms=50 if improved else 100, peak_memory_bytes=0),
    }


class _SequencedProvider:
    """Returns the rewrite on iter 1, then a no-op; records each call's messages."""

    def __init__(self):
        self.calls: list[list[dict]] = []

    def complete_text(self, req):
        self.calls.append([dict(m) for m in req.messages])
        if len(self.calls) == 1:
            return f"Improve projection\n```sql\n{_REWRITE}\n```"
        return f"No further change\n```sql\n{_REWRITE}\n```"


def test_direct_loop_reinjects_fresh_directions_after_best_sql_changes():
    provider = _SequencedProvider()

    with patch.object(direct_research, "_measure", side_effect=_measure_side_effect):
        direct_research._run_optimization_loop(
            provider=provider,
            model="test-model",
            reasoning="disable",
            original_sql=_ORIGINAL,
            metric_key="wall_time_ms",
            max_iterations=2,
            verify_runs=1,
            output=MagicMock(),
            build_prompt=lambda *a, **k: "SKILL_PROMPT_TEXT",
            explain_runner=None,
        )

    assert len(provider.calls) >= 2, "loop did not reach a second iteration"

    def _text(content):
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            return " ".join(
                (p.get("text") or "") if isinstance(p, dict) else str(p) for p in content
            )
        return str(content)

    def _last_user(messages):
        users = [m for m in messages if m.get("role") == "user"]
        return _text(users[-1]["content"]) if users else ""

    iter1_ctx = _last_user(provider.calls[0])
    iter2_ctx = _last_user(provider.calls[1])

    # Iteration 1 ran against the original (no findings) → no select-star direction.
    assert "fix-select-star" not in iter1_ctx
    # Iteration 2 ran against the rewritten best_sql → a fresh re-diagnosis of
    # SELECT * surfaced the select-star direction in this turn's context.
    assert "fix-select-star" in iter2_ctx, (
        "iteration 2 context did not carry fresh directions for the changed best_sql"
    )
    assert _REWRITE in iter2_ctx
