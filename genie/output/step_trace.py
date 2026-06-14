"""step_trace.py — StepEvent data model + TUI/report renderers for genieCLI v48."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, List


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

class StepStatus(str, Enum):
    RAN      = "ran"
    SKIPPED  = "skipped"
    NA       = "na"
    DEGRADED = "degraded"


@dataclass
class StepEvent:
    step_id: str          # e.g. "preflight_route", "baseline", "iteration_1", "decompose"
    stage: str            # human label: "Preflight", "Iteration 2/3", etc.
    status: StepStatus
    applicable: bool      # False → N/A rendering; invariant: applicable=False ↔ status=NA
    tui_headline: str     # one compact line; non-empty for non-NA
    detail: dict[str, Any] = field(default_factory=dict)
    na_reason: str = ""


# Type alias for a trace (ordered list of events; emission order preserved)
StepTrace = List[StepEvent]


# ---------------------------------------------------------------------------
# Canonical copy strings
# ---------------------------------------------------------------------------

CANONICAL_COPY: dict[str, str] = {
    "NO_SAFE_REWRITE_BEAT_ORIGINAL": (
        "decompose + iterate ran; no safe rewrite beat the original — keeping original SQL"
    ),
    "RECOMPOSE_SCAN_INCONCLUSIVE": (
        "recompose: applied (cross-fragment scan inconclusive — advisory)"
    ),
    "SEED_REJECTED": (
        "recomposed candidate rejected — row-equivalence failed; using original SQL"
    ),
}


# ---------------------------------------------------------------------------
# Detail formatters (kept in sync with proto_step_trace.py)
# ---------------------------------------------------------------------------

def _fmt_detail_iteration(d: dict[str, Any]) -> str:
    """Format iteration step detail block."""
    parts = []
    if "hypothesis" in d:
        parts.append(f"**Hypothesis:** {d['hypothesis']}")
    if "direction_applied" in d:
        parts.append(f"**Direction:** `{d['direction_applied']}`")
    if "candidate_sql" in d:
        parts.append(f"**SQL (snippet):**\n```sql\n{d['candidate_sql']}\n```")
    if "diff" in d:
        parts.append(f"**Diff (snippet):**\n```diff\n{d['diff']}\n```")
    if "metric_before" in d and "metric_after" in d:
        before = d["metric_before"]
        after = d["metric_after"]
        delta = after - before
        sign = "+" if delta >= 0 else ""
        parts.append(f"**Metric:** {before:,.0f} ms → {after:,.0f} ms ({sign}{delta:,.0f} ms)")
    if "verdict" in d:
        verdict_icon = {"improved": "✅", "worse": "❌", "neutral": "➖"}.get(d["verdict"], "")
        parts.append(f"**Verdict:** {verdict_icon} `{d['verdict']}`")
    return "\n\n".join(parts)


def _fmt_detail_fragment(d: dict[str, Any]) -> str:
    """Format fragment step detail block."""
    parts = []
    if "fragment_id" in d:
        parts.append(f"**Fragment ID:** `{d['fragment_id']}`")
    if "role" in d:
        parts.append(f"**Role:** {d['role']}")
    if "strategy_hint" in d:
        parts.append(f"**Strategy hint:** `{d['strategy_hint']}`")
    if "rationale" in d:
        parts.append(f"**Rationale:** {d['rationale']}")
    gates = []
    if "col_gate_verdict" in d:
        gates.append(f"col-gate: `{d['col_gate_verdict']}`")
    if "sem_gate_verdict" in d:
        gates.append(f"sem-gate: `{d['sem_gate_verdict']}`")
    if gates:
        parts.append("**Gates:** " + "  |  ".join(gates))
    if "action" in d:
        action_icon = {"optimized": "✅", "reverted": "🔄", "unchanged": "➖"}.get(d["action"], "")
        parts.append(f"**Action:** {action_icon} `{d['action']}`")
    return "\n\n".join(parts)


def _fmt_detail_recompose(d: dict[str, Any]) -> str:
    """Format recompose step detail block."""
    parts = []
    if "status" in d:
        parts.append(f"**Status:** `{d['status']}`")
    if "reverted_fragments" in d:
        reverted = d["reverted_fragments"]
        if reverted:
            parts.append(f"**Reverted fragments:** {', '.join(f'`{f}`' for f in reverted)}")
        else:
            parts.append("**Reverted fragments:** none")
    if "recomposed_sql" in d and d["recomposed_sql"]:
        parts.append(f"**Recomposed SQL (snippet):**\n```sql\n{d['recomposed_sql']}\n```")
    if "verdict_copy" in d:
        parts.append(f"**Verdict:** {d['verdict_copy']}")
    return "\n\n".join(parts)


def _fmt_detail_verify(d: dict[str, Any]) -> str:
    """Format verify step detail block."""
    parts = []
    if "winner_iteration" in d:
        parts.append(f"**Winner:** iteration {d['winner_iteration']}")
    if "final_metric" in d:
        parts.append(f"**Final metric:** {d['final_metric']:,.0f} ms")
    if "improvement_pct" in d:
        parts.append(f"**Improvement:** {d['improvement_pct']:.1f}%")
    return "\n\n".join(parts)


def _fmt_detail_preflight(d: dict[str, Any]) -> str:
    """Format preflight step detail block."""
    parts = []
    if "route" in d:
        parts.append(f"**Route:** `{d['route']}`")
    if "gate_message" in d:
        parts.append(f"**Gate message:** {d['gate_message']}")
    return "\n\n".join(parts)


def _fmt_detail_baseline(d: dict[str, Any]) -> str:
    """Format baseline step detail block."""
    parts = []
    if "metric_key" in d:
        parts.append(f"**Metric:** `{d['metric_key']}`")
    if "baseline_value" in d:
        parts.append(f"**Baseline value:** {d['baseline_value']:,.0f} ms")
    if "rows" in d:
        parts.append(f"**Rows returned:** {d['rows']:,}")
    return "\n\n".join(parts)


def _fmt_detail_decompose(d: dict[str, Any]) -> str:
    """Format decompose step detail block."""
    parts = []
    if "fragment_count" in d:
        parts.append(f"**Fragments produced:** {d['fragment_count']}")
    if "fragment_ids" in d:
        ids = d["fragment_ids"]
        if ids:
            parts.append(f"**Fragment IDs:** {', '.join(f'`{fid}`' for fid in ids)}")
    if "monster_ids" in d:
        monsters = d["monster_ids"]
        if monsters:
            parts.append(f"**Monster fragments:** {', '.join(f'`{mid}`' for mid in monsters)}")
    if "seed_changed" in d:
        parts.append(f"**Seed changed:** {'yes' if d['seed_changed'] else 'no'}")
    return "\n\n".join(parts)


_DETAIL_FORMATTERS: dict[str, Any] = {
    "preflight_route":  _fmt_detail_preflight,
    "baseline":         _fmt_detail_baseline,
    "decompose":        _fmt_detail_decompose,
    "recompose":        _fmt_detail_recompose,
    "verify":           _fmt_detail_verify,
}


# ---------------------------------------------------------------------------
# TUI renderer (compact breadcrumb)
# ---------------------------------------------------------------------------

def render_tui(trace: StepTrace) -> str:
    """Return compact breadcrumb lines — one per StepEvent, with over-cap collapse.

    Over-cap collapse rules:
    - If iteration_<N> events count > 10: show first 10 then one collapse line.
    - If SKIPPED fragment events with detail.action == "over_cap" count > 0:
      show one summary line instead of individual SKIPPED lines for those.
    """
    lines: list[str] = []

    # --- Collect iteration events for collapse check ---
    iter_events = [ev for ev in trace if ev.step_id.startswith("iteration_")]
    iter_count = len(iter_events)
    iter_shown = 0
    iter_collapsed = False

    # --- Collect over-cap fragment event step_ids ---
    over_cap_ids: set[str] = set()
    for ev in trace:
        if (
            ev.step_id.startswith("fragment_")
            and ev.status == StepStatus.SKIPPED
            and ev.detail.get("action") == "over_cap"
        ):
            over_cap_ids.add(ev.step_id)
    over_cap_count = len(over_cap_ids)
    over_cap_emitted = False

    for ev in trace:
        # Handle over-cap fragment collapse
        if ev.step_id in over_cap_ids:
            if not over_cap_emitted:
                lines.append(
                    f"  … {over_cap_count} lower-rank fragment(s) not optimized "
                    f"(cap=5) — see report"
                )
                over_cap_emitted = True
            continue

        # Handle iteration over-cap collapse
        if ev.step_id.startswith("iteration_"):
            if iter_count > 10:
                if iter_shown < 10:
                    iter_shown += 1
                    # Fall through to render normally
                elif not iter_collapsed:
                    extra = iter_count - 10
                    lines.append(f"  … {extra} more iterations — see report")
                    iter_collapsed = True
                    continue
                else:
                    continue
            else:
                iter_shown += 1

        # Standard rendering
        if ev.status == StepStatus.RAN:
            lines.append(f"  ✓ {ev.stage}: {ev.tui_headline}")
        elif ev.status == StepStatus.SKIPPED:
            lines.append(f"  ~ {ev.stage}: skipped — {ev.tui_headline}")
        elif ev.status == StepStatus.NA:
            lines.append(f"  · {ev.stage}: N/A — {ev.na_reason}")
        elif ev.status == StepStatus.DEGRADED:
            lines.append(f"  ! {ev.stage}: DEGRADED — {ev.tui_headline}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Report renderer (full markdown; never collapses)
# ---------------------------------------------------------------------------

def render_report(trace: StepTrace) -> str:
    """Return a full markdown report for the trace. Never collapses any events."""
    sections = []

    # --- Step Summary table ---
    table_rows = ["| Step | Status | Headline |", "| --- | --- | --- |"]
    for ev in trace:
        if ev.status == StepStatus.RAN:
            status_cell = "✓ ran"
        elif ev.status == StepStatus.SKIPPED:
            status_cell = "~ skipped"
        elif ev.status == StepStatus.NA:
            status_cell = "· N/A"
        else:
            status_cell = "! DEGRADED"
        headline = ev.tui_headline if ev.status != StepStatus.NA else f"_{ev.na_reason}_"
        table_rows.append(f"| {ev.stage} | {status_cell} | {headline} |")

    sections.append("## Step Summary\n\n" + "\n".join(table_rows))

    # --- Per-step detail subsections (RAN + DEGRADED steps with detail) ---
    detail_sections = []
    for ev in trace:
        if ev.status not in {StepStatus.RAN, StepStatus.DEGRADED}:
            continue
        if not ev.detail:
            continue
        formatter = _DETAIL_FORMATTERS.get(ev.step_id)
        # Fallback: handle iteration_N and fragment_N patterns
        if formatter is None:
            if ev.step_id.startswith("iteration_"):
                formatter = _fmt_detail_iteration
            elif ev.step_id.startswith("fragment_"):
                formatter = _fmt_detail_fragment
        if formatter is None:
            continue
        body = formatter(ev.detail)
        if body.strip():
            detail_sections.append(f"### {ev.stage}\n\n{body}")

    if detail_sections:
        sections.append("\n\n".join(detail_sections))

    # --- Skipped / N/A section ---
    na_events = [ev for ev in trace if ev.status == StepStatus.NA]
    if na_events:
        na_lines = ["## Skipped / N/A\n"]
        for ev in na_events:
            na_lines.append(f"- **{ev.stage}**: {ev.na_reason}")
        sections.append("\n".join(na_lines))

    return "\n\n".join(sections)
