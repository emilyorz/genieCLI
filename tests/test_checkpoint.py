"""Tests for genie/runtime/checkpoint.py"""
from __future__ import annotations

from unittest.mock import call, patch

import pytest

from genie.runtime.checkpoint import (
    _run_git,
    _sanitize_label,
    checkpoint_create,
    checkpoint_restore,
    git_is_clean,
    git_is_repo,
)


# ── _sanitize_label ───────────────────────────────────────────────────────────

def test_sanitize_label_replaces_spaces():
    assert _sanitize_label("hello world") == "hello_world"


def test_sanitize_label_replaces_special_chars():
    assert _sanitize_label("iter:1/test!") == "iter_1_test_"


def test_sanitize_label_keeps_allowed():
    assert _sanitize_label("iter-1_test") == "iter-1_test"


def test_sanitize_label_alphanumeric_unchanged():
    assert _sanitize_label("abc123") == "abc123"


# ── _run_git ──────────────────────────────────────────────────────────────────

def test_run_git_returns_tuple():
    with patch("subprocess.run") as mock_run:
        mock_run.return_value.returncode = 0
        mock_run.return_value.stdout = "abc\n"
        mock_run.return_value.stderr = ""
        code, stdout, stderr = _run_git(["status"], cwd="/tmp")
    assert code == 0
    assert stdout == "abc"   # stripped
    assert stderr == ""


def test_run_git_strips_whitespace():
    with patch("subprocess.run") as mock_run:
        mock_run.return_value.returncode = 0
        mock_run.return_value.stdout = "  sha123  \n"
        mock_run.return_value.stderr = "  err  \n"
        _, stdout, stderr = _run_git(["rev-parse", "HEAD"], cwd=".")
    assert stdout == "sha123"
    assert stderr == "err"


# ── git_is_repo ───────────────────────────────────────────────────────────────

def test_git_is_repo_true():
    with patch("subprocess.run") as mock_run:
        mock_run.return_value.returncode = 0
        mock_run.return_value.stdout = "true"
        mock_run.return_value.stderr = ""
        assert git_is_repo("/some/repo") is True


def test_git_is_repo_false():
    with patch("subprocess.run") as mock_run:
        mock_run.return_value.returncode = 128
        mock_run.return_value.stdout = ""
        mock_run.return_value.stderr = "not a git repo"
        assert git_is_repo("/not/a/repo") is False


# ── git_is_clean ──────────────────────────────────────────────────────────────

def test_git_is_clean_true():
    with patch("subprocess.run") as mock_run:
        mock_run.return_value.returncode = 0
        mock_run.return_value.stdout = ""
        mock_run.return_value.stderr = ""
        assert git_is_clean("/repo") is True


def test_git_is_clean_false_has_changes():
    with patch("subprocess.run") as mock_run:
        mock_run.return_value.returncode = 0
        mock_run.return_value.stdout = " M file.py"
        mock_run.return_value.stderr = ""
        assert git_is_clean("/repo") is False


def test_git_is_clean_false_nonzero():
    with patch("subprocess.run") as mock_run:
        mock_run.return_value.returncode = 1
        mock_run.return_value.stdout = ""
        mock_run.return_value.stderr = ""
        assert git_is_clean("/repo") is False


# ── checkpoint_create ─────────────────────────────────────────────────────────

def _git_seq(*responses):
    """Build a side_effect list for subprocess.run calls."""
    mocks = []
    for (code, stdout, stderr) in responses:
        m = type("P", (), {})()
        m.returncode = code
        m.stdout = stdout
        m.stderr = stderr
        mocks.append(m)
    return mocks


def test_checkpoint_create_success():
    seq = _git_seq(
        (0, "oldsha\n", ""),   # rev-parse HEAD (original_head)
        (0, "", ""),            # add -A
        (0, "", ""),            # commit
        (0, "newsha\n", ""),   # rev-parse HEAD (commit_sha)
    )
    with patch("subprocess.run", side_effect=seq):
        result = checkpoint_create("my label", cwd="/repo")
    assert "error" not in result
    assert result["label"] == "my_label"
    assert result["original_head"] == "oldsha"
    assert result["commit_sha"] == "newsha"
    assert "timestamp" in result


def test_checkpoint_create_rev_parse_fails():
    seq = _git_seq((128, "", "fatal: not a repo"))
    with patch("subprocess.run", side_effect=seq):
        result = checkpoint_create("label", cwd="/repo")
    assert "error" in result
    assert "HEAD" in result["error"]


def test_checkpoint_create_add_fails():
    seq = _git_seq(
        (0, "oldsha\n", ""),
        (1, "", "index locked"),
    )
    with patch("subprocess.run", side_effect=seq):
        result = checkpoint_create("label", cwd="/repo")
    assert "error" in result
    assert "stage" in result["error"].lower()


def test_checkpoint_create_commit_fails():
    seq = _git_seq(
        (0, "oldsha\n", ""),
        (0, "", ""),
        (1, "", "nothing to commit"),
    )
    with patch("subprocess.run", side_effect=seq):
        result = checkpoint_create("label", cwd="/repo")
    assert "error" in result
    assert "commit" in result["error"].lower()


def test_checkpoint_create_post_commit_rev_parse_fails():
    seq = _git_seq(
        (0, "oldsha\n", ""),
        (0, "", ""),
        (0, "", ""),
        (1, "", "error"),
    )
    with patch("subprocess.run", side_effect=seq):
        result = checkpoint_create("label", cwd="/repo")
    assert "error" in result


# ── checkpoint_restore ────────────────────────────────────────────────────────

def test_checkpoint_restore_success():
    with patch("subprocess.run") as mock_run:
        mock_run.return_value.returncode = 0
        mock_run.return_value.stdout = ""
        mock_run.return_value.stderr = ""
        result = checkpoint_restore({"original_head": "abc123"}, cwd="/repo")
    assert result == {"ok": True, "restored_head": "abc123"}


def test_checkpoint_restore_missing_original_head():
    result = checkpoint_restore({}, cwd="/repo")
    assert "error" in result
    assert "original_head" in result["error"]


def test_checkpoint_restore_reset_fails():
    with patch("subprocess.run") as mock_run:
        mock_run.return_value.returncode = 1
        mock_run.return_value.stdout = ""
        mock_run.return_value.stderr = "conflict"
        result = checkpoint_restore({"original_head": "abc"}, cwd="/repo")
    assert "error" in result
    assert "reset" in result["error"].lower()
