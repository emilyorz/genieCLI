"""Tests for genie/skills/git_ops/__init__.py"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from genie.skills.git_ops import (
    GitCheckpointCreate,
    GitCheckpointRestore,
    GitDiff,
    GitLog,
    GitStatus,
    register,
)


def _proc(returncode=0, stdout="", stderr=""):
    m = MagicMock()
    m.returncode = returncode
    m.stdout = stdout
    m.stderr = stderr
    return m


# ── GitStatus ─────────────────────────────────────────────────────────────────

def test_git_status_clean():
    with patch("subprocess.run", return_value=_proc(stdout="")):
        result = GitStatus().run()
    assert "clean" in result or result == "(clean — no changes)"


def test_git_status_has_changes():
    with patch("subprocess.run", return_value=_proc(stdout=" M genie/cli.py\n")):
        result = GitStatus().run()
    assert "genie/cli.py" in result


def test_git_status_error():
    with patch("subprocess.run", return_value=_proc(returncode=128, stderr="fatal: not a repo")):
        result = GitStatus().run()
    assert "ERROR" in result


# ── GitDiff ───────────────────────────────────────────────────────────────────

def test_git_diff_worktree_default():
    with patch("subprocess.run", return_value=_proc(stdout="diff --git a/foo b/foo\n")) as mock_run:
        result = GitDiff().run()
    cmd = mock_run.call_args[0][0]
    assert cmd == ["git", "diff"]


def test_git_diff_staged():
    with patch("subprocess.run", return_value=_proc(stdout="staged diff")) as mock_run:
        GitDiff().run(target="staged")
    cmd = mock_run.call_args[0][0]
    assert "--cached" in cmd


def test_git_diff_head_minus_1():
    with patch("subprocess.run", return_value=_proc(stdout="HEAD~1 diff")) as mock_run:
        GitDiff().run(target="HEAD~1")
    cmd = mock_run.call_args[0][0]
    assert "HEAD~1" in cmd


def test_git_diff_no_diff():
    with patch("subprocess.run", return_value=_proc(stdout="")):
        result = GitDiff().run()
    assert result == "(no diff)"


def test_git_diff_truncates_at_10kb():
    big = "x" * 12000
    with patch("subprocess.run", return_value=_proc(stdout=big)):
        result = GitDiff().run()
    assert "truncated" in result
    assert len(result) <= 10240 + 30  # some slack for the truncation message


def test_git_diff_error():
    with patch("subprocess.run", return_value=_proc(returncode=1, stderr="not a repo")):
        result = GitDiff().run()
    assert "ERROR" in result


# ── GitLog ────────────────────────────────────────────────────────────────────

def test_git_log_default_n():
    with patch("subprocess.run", return_value=_proc(stdout="abc123 first commit\n")) as mock_run:
        result = GitLog().run()
    cmd = mock_run.call_args[0][0]
    assert "-10" in cmd


def test_git_log_custom_n():
    with patch("subprocess.run", return_value=_proc(stdout="abc first\nbcd second")) as mock_run:
        GitLog().run(n=5)
    cmd = mock_run.call_args[0][0]
    assert "-5" in cmd


def test_git_log_empty():
    with patch("subprocess.run", return_value=_proc(stdout="")):
        result = GitLog().run()
    assert result == "(no commits)"


def test_git_log_error():
    with patch("subprocess.run", return_value=_proc(returncode=128, stderr="not a repo")):
        result = GitLog().run()
    assert "ERROR" in result


# ── GitCheckpointCreate ───────────────────────────────────────────────────────

def test_git_checkpoint_create_success():
    fake_info = {
        "commit_sha": "deadbeef1234",
        "original_head": "abcd5678",
        "timestamp": 1234567890,
    }
    with patch("genie.skills.git_ops.checkpoint_create", return_value=fake_info):
        result = GitCheckpointCreate().run(label="test", cwd=".")
    assert "deadbeef" in result
    assert "abcd5678" in result


def test_git_checkpoint_create_error():
    fake_info = {"error": "not a git repo"}
    with patch("genie.skills.git_ops.checkpoint_create", return_value=fake_info):
        result = GitCheckpointCreate().run(label="test", cwd=".")
    assert "ERROR" in result
    assert "not a git repo" in result


# ── GitCheckpointRestore ──────────────────────────────────────────────────────

def test_git_checkpoint_restore_success():
    with patch("genie.skills.git_ops.checkpoint_restore", return_value={"restored_head": "abc123"}):
        result = GitCheckpointRestore().run(original_head="abc123", cwd=".")
    assert "abc123" in result


def test_git_checkpoint_restore_error():
    with patch("genie.skills.git_ops.checkpoint_restore", return_value={"error": "failed to reset"}):
        result = GitCheckpointRestore().run(original_head="abc123", cwd=".")
    assert "ERROR" in result
    assert "failed to reset" in result


# ── register ──────────────────────────────────────────────────────────────────

def test_register_adds_all_skills():
    class _FakeRegistry:
        def __init__(self):
            self.registered = []
        def register(self, skill):
            self.registered.append(skill)

    reg = _FakeRegistry()
    register(reg)
    names = [s.name for s in reg.registered]
    assert "git_status" in names
    assert "git_diff" in names
    assert "git_log" in names
    assert "git_checkpoint_create" in names
    assert "git_checkpoint_restore" in names
