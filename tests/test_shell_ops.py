"""Tests for genie/skills/shell_ops/__init__.py"""
from __future__ import annotations

import subprocess
from unittest.mock import MagicMock, patch

import pytest

from genie.skills.shell_ops import (
    ALLOWED_EXECUTABLES,
    PROFILES,
    CommandRun,
    _is_allowed,
    register,
)


# ── _is_allowed ───────────────────────────────────────────────────────────────

def test_is_allowed_pytest():
    assert _is_allowed("pytest") is True


def test_is_allowed_npm():
    assert _is_allowed("npm test") is True


def test_is_allowed_ruff():
    assert _is_allowed("ruff check .") is True


def test_is_allowed_python_m_pytest():
    assert _is_allowed("python -m pytest") is True


def test_is_allowed_python_without_m_pytest():
    # python without -m pytest is not allowed
    assert _is_allowed("python script.py") is False


def test_is_allowed_python3_m_pytest():
    assert _is_allowed("python3 -m pytest") is True


def test_is_allowed_shell_operator_pipe():
    assert _is_allowed("pytest | grep foo") is False


def test_is_allowed_shell_operator_and():
    assert _is_allowed("pytest && echo done") is False


def test_is_allowed_shell_operator_semicolon():
    assert _is_allowed("pytest; rm -rf /") is False


def test_is_allowed_shell_operator_redirect():
    assert _is_allowed("pytest > out.txt") is False


def test_is_allowed_unknown_executable():
    assert _is_allowed("bash -c 'evil'") is False


def test_is_allowed_empty_string():
    assert _is_allowed("") is False


def test_is_allowed_malformed_shlex():
    # Unclosed quote — shlex.split raises ValueError
    assert _is_allowed("pytest 'unclosed") is False


# ── CommandRun profiles ───────────────────────────────────────────────────────

def _fake_run(returncode=0, stdout="ok", stderr=""):
    mock = MagicMock()
    mock.returncode = returncode
    mock.stdout = stdout
    mock.stderr = stderr
    return mock


def test_command_run_python_test_profile(tmp_path):
    skill = CommandRun()
    with patch("subprocess.run", return_value=_fake_run(stdout="1 passed")) as mock_run:
        result = skill.run(profile="python-test", cwd=str(tmp_path))
    assert "exit_code=0" in result
    assert "1 passed" in result
    cmd = mock_run.call_args[0][0]
    assert cmd == ["pytest"]


def test_command_run_node_test_profile(tmp_path):
    skill = CommandRun()
    with patch("subprocess.run", return_value=_fake_run(stdout="npm output")) as mock_run:
        result = skill.run(profile="node-test", cwd=str(tmp_path))
    assert "exit_code=0" in result
    cmd = mock_run.call_args[0][0]
    assert "npm" in cmd


def test_command_run_lint_profile(tmp_path):
    skill = CommandRun()
    with patch("subprocess.run", return_value=_fake_run()) as mock_run:
        result = skill.run(profile="lint", cwd=str(tmp_path))
    assert "exit_code=0" in result


def test_command_run_build_profile(tmp_path):
    skill = CommandRun()
    with patch("subprocess.run", return_value=_fake_run(stdout="build success")) as mock_run:
        result = skill.run(profile="build", cwd=str(tmp_path))
    assert "exit_code=0" in result


def test_command_run_unknown_profile():
    skill = CommandRun()
    result = skill.run(profile="nonexistent_xyz")
    assert "Unknown profile" in result


# ── CommandRun custom profile ─────────────────────────────────────────────────

def test_command_run_custom_allowed(tmp_path):
    skill = CommandRun()
    with patch("subprocess.run", return_value=_fake_run(stdout="1 passed")):
        result = skill.run(profile="custom", custom_command="pytest", cwd=str(tmp_path))
    assert "exit_code=0" in result


def test_command_run_custom_no_command():
    skill = CommandRun()
    result = skill.run(profile="custom")
    assert "custom_command is required" in result


def test_command_run_custom_disallowed():
    skill = CommandRun()
    result = skill.run(profile="custom", custom_command="rm -rf /")
    assert "not in whitelist" in result


def test_command_run_custom_with_pipe():
    skill = CommandRun()
    result = skill.run(profile="custom", custom_command="pytest | grep foo")
    assert "not in whitelist" in result


# ── Output truncation ─────────────────────────────────────────────────────────

def test_command_run_output_truncated(tmp_path):
    big_output = "x" * 12000
    skill = CommandRun()
    with patch("subprocess.run", return_value=_fake_run(stdout=big_output)):
        result = skill.run(profile="python-test", cwd=str(tmp_path))
    assert "truncated" in result


# ── Error paths ───────────────────────────────────────────────────────────────

def test_command_run_timeout(tmp_path):
    skill = CommandRun()
    with patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="pytest", timeout=60)):
        result = skill.run(profile="python-test", timeout=60, cwd=str(tmp_path))
    assert "timed out" in result


def test_command_run_generic_exception(tmp_path):
    skill = CommandRun()
    with patch("subprocess.run", side_effect=RuntimeError("unexpected")):
        result = skill.run(profile="python-test", cwd=str(tmp_path))
    assert "ERROR" in result


def test_command_run_nonzero_exit(tmp_path):
    skill = CommandRun()
    with patch("subprocess.run", return_value=_fake_run(returncode=1, stderr="test failed")):
        result = skill.run(profile="python-test", cwd=str(tmp_path))
    assert "exit_code=1" in result


# ── register ──────────────────────────────────────────────────────────────────

def test_register_adds_command_run():
    class _FakeRegistry:
        def __init__(self):
            self.registered = []
        def register(self, skill):
            self.registered.append(skill)

    reg = _FakeRegistry()
    register(reg)
    names = [s.name for s in reg.registered]
    assert "command_run" in names
