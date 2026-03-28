"""Tests for genie/runtime/run_manager.py"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from genie.runtime.run_manager import (
    IterationResult,
    RunConfig,
    RunManager,
    RunState,
)


# ── Fixtures / helpers ────────────────────────────────────────────────────────

def _config(direction="higher", guard=None, max_iter=None):
    return RunConfig(
        goal="raise coverage",
        scope=["tests/"],
        metric_direction=direction,
        verify_command="echo 42",
        guard_command=guard,
        max_iterations=max_iter,
    )


def _good_metric(value=42.0):
    m = MagicMock()
    m.success = True
    m.value = value
    m.raw_output = str(value)
    m.error = None
    return m


def _bad_metric(error="cmd failed"):
    m = MagicMock()
    m.success = False
    m.value = None
    m.raw_output = ""
    m.error = error
    return m


def _good_checkpoint(original="oldshaXXX", commit="newshaXXX"):
    return {"label": "iter1", "original_head": original, "commit_sha": commit, "timestamp": 0, "cwd": "."}


def _bad_checkpoint(msg="index locked"):
    return {"error": msg}


def _good_restore(head="oldshaXXX"):
    return {"ok": True, "restored_head": head}


def _bad_restore(msg="reset failed"):
    return {"error": msg}


# ── RunManager.start ──────────────────────────────────────────────────────────

@patch("genie.runtime.run_manager.git_is_repo", return_value=False)
def test_start_fails_when_not_git_repo(mock_is_repo, tmp_path):
    mgr = RunManager()
    state = mgr.start(_config(), cwd=str(tmp_path))
    assert state.status == "failed"


@patch("genie.runtime.run_manager.git_is_repo", return_value=True)
@patch("genie.runtime.run_manager.extract_metric")
def test_start_fails_when_baseline_fails(mock_metric, mock_is_repo, tmp_path):
    mock_metric.return_value = _bad_metric("no output")
    mgr = RunManager()
    state = mgr.start(_config(), cwd=str(tmp_path))
    assert state.status == "failed"
    assert state.baseline_metric is None


@patch("genie.runtime.run_manager.git_is_repo", return_value=True)
@patch("genie.runtime.run_manager.extract_metric")
def test_start_success(mock_metric, mock_is_repo, tmp_path):
    mock_metric.return_value = _good_metric(8.0)
    mgr = RunManager()
    state = mgr.start(_config(), cwd=str(tmp_path))
    assert state.status == "running"
    assert state.baseline_metric == pytest.approx(8.0)
    assert state.current_best == pytest.approx(8.0)
    assert state.iteration == 0
    assert state.journal_path is not None


# ── RunManager.should_continue ────────────────────────────────────────────────

def test_should_continue_running_unlimited():
    mgr = RunManager()
    state = RunState(config=_config(), status="running")
    assert mgr.should_continue(state) is True


def test_should_continue_running_bounded_not_reached():
    mgr = RunManager()
    state = RunState(config=_config(max_iter=5), status="running", iteration=4)
    assert mgr.should_continue(state) is True


def test_should_continue_running_bounded_reached():
    mgr = RunManager()
    state = RunState(config=_config(max_iter=5), status="running", iteration=5)
    assert mgr.should_continue(state) is False


def test_should_continue_failed():
    mgr = RunManager()
    state = RunState(config=_config(), status="failed")
    assert mgr.should_continue(state) is False


def test_should_continue_stopped():
    mgr = RunManager()
    state = RunState(config=_config(), status="stopped")
    assert mgr.should_continue(state) is False


# ── RunManager.step — improved path ──────────────────────────────────────────

@patch("genie.runtime.run_manager.checkpoint_create")
@patch("genie.runtime.run_manager.extract_metric")
@patch("genie.runtime.run_manager.compare_metrics", return_value="improved")
def test_step_improved_keeps_commit(mock_compare, mock_metric, mock_checkpoint, tmp_path):
    mock_checkpoint.return_value = _good_checkpoint()
    mock_metric.return_value = _good_metric(12.0)

    mgr = RunManager()
    state = RunState(
        config=_config(),
        iteration=0,
        baseline_metric=8.0,
        current_best=8.0,
        status="running",
        history=[],
        journal_path=str(tmp_path / "j.tsv"),
        cwd=str(tmp_path),
    )
    state = mgr.step(state, hypothesis="add tests", changed_files=["tests/foo.py"])
    assert state.iteration == 1
    assert state.current_best == pytest.approx(12.0)
    result = state.history[-1]
    assert result.status == "improved"


# ── RunManager.step — same/worse path ────────────────────────────────────────

@patch("genie.runtime.run_manager.checkpoint_restore")
@patch("genie.runtime.run_manager.checkpoint_create")
@patch("genie.runtime.run_manager.extract_metric")
@patch("genie.runtime.run_manager.compare_metrics", return_value="same")
def test_step_same_reverts(mock_compare, mock_metric, mock_checkpoint, mock_restore, tmp_path):
    mock_checkpoint.return_value = _good_checkpoint()
    mock_metric.return_value = _good_metric(8.0)
    mock_restore.return_value = _good_restore()

    mgr = RunManager()
    state = RunState(
        config=_config(),
        iteration=0,
        baseline_metric=8.0,
        current_best=8.0,
        status="running",
        history=[],
        journal_path=str(tmp_path / "j.tsv"),
        cwd=str(tmp_path),
    )
    state = mgr.step(state, hypothesis="no change", changed_files=[])
    result = state.history[-1]
    assert result.status == "same"
    mock_restore.assert_called_once()


# ── RunManager.step — checkpoint failure ─────────────────────────────────────

@patch("genie.runtime.run_manager.checkpoint_create")
def test_step_checkpoint_failure(mock_checkpoint, tmp_path):
    mock_checkpoint.return_value = _bad_checkpoint("index locked")

    mgr = RunManager()
    state = RunState(
        config=_config(),
        iteration=0,
        baseline_metric=8.0,
        current_best=8.0,
        status="running",
        history=[],
        journal_path=str(tmp_path / "j.tsv"),
        cwd=str(tmp_path),
    )
    state = mgr.step(state, hypothesis="attempt", changed_files=[])
    result = state.history[-1]
    assert result.status == "error"
    assert "Checkpoint failed" in result.error


# ── RunManager.step — guard failure ──────────────────────────────────────────

@patch("genie.runtime.run_manager.checkpoint_restore")
@patch("genie.runtime.run_manager.checkpoint_create")
@patch("subprocess.run")
def test_step_guard_fails_reverts(mock_subproc, mock_checkpoint, mock_restore, tmp_path):
    mock_checkpoint.return_value = _good_checkpoint()
    mock_restore.return_value = _good_restore()
    # Guard fails
    guard_proc = MagicMock()
    guard_proc.returncode = 1
    mock_subproc.return_value = guard_proc

    mgr = RunManager()
    state = RunState(
        config=_config(guard="pytest tests/"),
        iteration=0,
        baseline_metric=8.0,
        current_best=8.0,
        status="running",
        history=[],
        journal_path=str(tmp_path / "j.tsv"),
        cwd=str(tmp_path),
    )
    state = mgr.step(state, hypothesis="breaks guard", changed_files=[])
    result = state.history[-1]
    assert result.status == "guard_failed"
    assert result.guard_passed is False
    mock_restore.assert_called_once()


# ── RunManager.step — restore failure after worse ────────────────────────────

@patch("genie.runtime.run_manager.checkpoint_restore")
@patch("genie.runtime.run_manager.checkpoint_create")
@patch("genie.runtime.run_manager.extract_metric")
@patch("genie.runtime.run_manager.compare_metrics", return_value="worse")
def test_step_restore_failure_sets_state_failed(mock_compare, mock_metric, mock_checkpoint, mock_restore, tmp_path):
    mock_checkpoint.return_value = _good_checkpoint()
    mock_metric.return_value = _good_metric(5.0)
    mock_restore.return_value = _bad_restore("conflict")

    mgr = RunManager()
    state = RunState(
        config=_config(),
        iteration=0,
        baseline_metric=8.0,
        current_best=8.0,
        status="running",
        history=[],
        journal_path=str(tmp_path / "j.tsv"),
        cwd=str(tmp_path),
    )
    state = mgr.step(state, hypothesis="worse change", changed_files=[])
    assert state.status == "failed"


# ── RunManager.summary ────────────────────────────────────────────────────────

def test_summary_contains_goal():
    mgr = RunManager()
    state = RunState(
        config=_config(),
        iteration=3,
        baseline_metric=8.0,
        current_best=25.0,
        status="running",
        history=[],
    )
    summary = mgr.summary(state)
    assert "raise coverage" in summary
    assert "8.0" in summary
    assert "25.0" in summary
    assert "3" in summary


def test_summary_includes_history():
    mgr = RunManager()
    result = IterationResult(
        iteration=1,
        hypothesis="add tests",
        metric=12.0,
        delta=4.0,
        guard_passed=True,
        status="improved",
    )
    state = RunState(
        config=_config(),
        iteration=1,
        baseline_metric=8.0,
        current_best=12.0,
        status="running",
        history=[result],
    )
    summary = mgr.summary(state)
    assert "add tests" in summary
    assert "improved" in summary
    assert "+4.0" in summary
