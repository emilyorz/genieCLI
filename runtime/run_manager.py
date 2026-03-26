"""
runtime/run_manager.py — Orchestration layer for autoresearch iteration loops.

RunManager coordinates the baseline → hypothesis → verify → compare → keep/revert
cycle.  It delegates git operations to checkpoint.py, metric parsing to metric.py,
and record-keeping to journal.py.

Design: RunManager is the only class (needs mutable state across calls).
RunConfig and RunState are plain dataclasses.
"""
from __future__ import annotations

import shlex
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Optional

from .checkpoint import checkpoint_create, checkpoint_restore, git_is_repo
from .journal import JournalWriter
from .metric import compare_metrics, extract_metric


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class RunConfig:
    goal: str
    scope: list[str]                         # file globs defining the search space
    metric_direction: Literal["higher", "lower"]
    verify_command: str                      # command whose output contains the metric
    guard_command: Optional[str] = None      # optional safety check (must exit 0)
    max_iterations: Optional[int] = None     # None = unlimited


@dataclass
class IterationResult:
    iteration: int
    hypothesis: str
    metric: Optional[float]
    delta: Optional[float]
    guard_passed: bool
    status: str                              # improved | same | worse | guard_failed | error
    checkpoint: Optional[dict] = None
    error: Optional[str] = None


@dataclass
class RunState:
    config: RunConfig
    iteration: int = 0
    baseline_metric: Optional[float] = None
    current_best: Optional[float] = None
    status: Literal["running", "completed", "failed", "stopped"] = "running"
    history: list[IterationResult] = field(default_factory=list)
    journal_path: Optional[str] = None
    cwd: str = "."


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

class RunManager:
    """
    Orchestrates autoresearch iteration loops.

    Typical lifecycle:
        state = manager.start(config, cwd)
        while manager.should_continue(state):
            state = manager.step(state, hypothesis, changed_files)
        print(manager.summary(state))
    """

    def start(self, config: RunConfig, cwd: str = ".") -> RunState:
        """
        Initialise a new run: verify the repo, measure baseline, set up journal.

        Returns a RunState ready for step() calls.  On hard failure the returned
        state has status="failed".
        """
        if not git_is_repo(cwd):
            return RunState(config=config, status="failed", cwd=cwd)

        baseline = extract_metric(config.verify_command, cwd)
        journal_path = str(Path(cwd) / "autoresearch_journal.tsv")
        journal = JournalWriter(journal_path)

        if not baseline.success:
            journal.write_baseline(
                metric=None,
                description=f"baseline error: {baseline.error}",
            )
            return RunState(
                config=config,
                iteration=0,
                baseline_metric=None,
                current_best=None,
                status="failed",
                history=[],
                journal_path=journal_path,
                cwd=cwd,
            )

        journal.write_baseline(
            metric=baseline.value,
            description=f"goal: {config.goal}",
        )
        return RunState(
            config=config,
            iteration=0,
            baseline_metric=baseline.value,
            current_best=baseline.value,
            status="running",
            history=[],
            journal_path=journal_path,
            cwd=cwd,
        )

    def step(
        self,
        state: RunState,
        hypothesis: str,
        changed_files: list[str],
    ) -> RunState:
        """
        Run one iteration: checkpoint → guard → verify → compare → keep/revert.

        Mutates and returns the updated RunState.
        The caller is responsible for having already applied file changes before
        calling step(); step() commits them, measures the result, and decides
        keep/revert.
        """
        state.iteration += 1
        iteration = state.iteration
        journal = JournalWriter(state.journal_path)

        # 1. Commit current (already-modified) state; record original_head for revert
        checkpoint = checkpoint_create(f"iter{iteration}", state.cwd)
        if "error" in checkpoint:
            result = IterationResult(
                iteration=iteration,
                hypothesis=hypothesis,
                metric=None,
                delta=None,
                guard_passed=False,
                status="error",
                error=f"Checkpoint failed: {checkpoint['error']}",
            )
            state.history.append(result)
            journal.write_iteration(
                iteration=iteration,
                commit="",
                metric=None,
                delta=None,
                guard="error",
                status="error",
                description=hypothesis,
            )
            return state

        # 2. Optional guard check (e.g. linter, type-checker) on candidate state
        guard_passed = True
        if state.config.guard_command:
            try:
                guard_proc = subprocess.run(
                    shlex.split(state.config.guard_command),
                    shell=False,
                    cwd=state.cwd,
                    capture_output=True,
                    text=True,
                    timeout=120,
                )
                guard_passed = guard_proc.returncode == 0
            except Exception:
                guard_passed = False

        if not guard_passed:
            restore_result = checkpoint_restore(checkpoint, state.cwd)
            if "error" in restore_result:
                state.status = "failed"
                result = IterationResult(
                    iteration=iteration,
                    hypothesis=hypothesis,
                    metric=None,
                    delta=None,
                    guard_passed=False,
                    status="error",
                    checkpoint=checkpoint,
                    error=f"Restore failed after guard failure: {restore_result['error']}",
                )
                state.history.append(result)
                journal.write_iteration(
                    iteration=iteration,
                    commit=checkpoint.get("commit_sha", ""),
                    metric=None,
                    delta=None,
                    guard="fail",
                    status="error",
                    description=hypothesis,
                )
                return state

            result = IterationResult(
                iteration=iteration,
                hypothesis=hypothesis,
                metric=None,
                delta=None,
                guard_passed=False,
                status="guard_failed",
                checkpoint=checkpoint,
            )
            state.history.append(result)
            journal.write_iteration(
                iteration=iteration,
                commit=checkpoint.get("commit_sha", ""),
                metric=None,
                delta=None,
                guard="fail",
                status="guard_failed",
                description=hypothesis,
            )
            return state

        # 3. Measure metric on the committed candidate state
        metric_result = extract_metric(state.config.verify_command, state.cwd)
        current_value = metric_result.value

        # 4. Compare and decide keep vs revert
        comparison: str = "error"
        delta: Optional[float] = None

        if (
            metric_result.success
            and state.baseline_metric is not None
            and current_value is not None
        ):
            comparison = compare_metrics(
                state.baseline_metric, current_value, state.config.metric_direction
            )
            delta = current_value - state.baseline_metric

        if comparison == "improved":
            state.current_best = current_value
        else:
            # Changes did not improve — revert to pre-step state
            restore_result = checkpoint_restore(checkpoint, state.cwd)
            if "error" in restore_result:
                state.status = "failed"
                result = IterationResult(
                    iteration=iteration,
                    hypothesis=hypothesis,
                    metric=current_value,
                    delta=delta,
                    guard_passed=guard_passed,
                    status="error",
                    checkpoint=checkpoint,
                    error=f"Restore failed: {restore_result['error']}",
                )
                state.history.append(result)
                journal.write_iteration(
                    iteration=iteration,
                    commit=checkpoint.get("commit_sha", ""),
                    metric=current_value,
                    delta=delta,
                    guard="pass",
                    status="error",
                    description=hypothesis,
                )
                return state

        result = IterationResult(
            iteration=iteration,
            hypothesis=hypothesis,
            metric=current_value,
            delta=delta,
            guard_passed=guard_passed,
            status=comparison,
            checkpoint=checkpoint,
        )
        state.history.append(result)
        journal.write_iteration(
            iteration=iteration,
            commit=checkpoint.get("commit_sha", ""),
            metric=current_value,
            delta=delta,
            guard="pass",
            status=comparison,
            description=hypothesis,
        )
        return state

    def should_continue(self, state: RunState) -> bool:
        """Return True if the run should proceed to another step()."""
        if state.status != "running":
            return False
        if state.config.max_iterations is not None:
            return state.iteration < state.config.max_iterations
        return True

    def summary(self, state: RunState) -> str:
        """Generate a human-readable summary of the run."""
        lines = [
            "# Autoresearch Run Summary",
            f"Goal       : {state.config.goal}",
            f"Iterations : {state.iteration}",
            f"Baseline   : {state.baseline_metric}",
            f"Best       : {state.current_best}",
            f"Status     : {state.status}",
            "",
            "## Iteration History",
        ]
        for r in state.history:
            delta_str = f"{r.delta:+.4f}" if r.delta is not None else "N/A"
            lines.append(
                f"  [{r.iteration:3d}] {r.status:<12s} "
                f"metric={r.metric}  delta={delta_str}  | {r.hypothesis}"
            )
        return "\n".join(lines)
