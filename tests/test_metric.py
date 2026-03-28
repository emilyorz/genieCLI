"""Tests for genie/runtime/metric.py"""
from __future__ import annotations

import subprocess
from unittest.mock import MagicMock, patch

import pytest

from genie.runtime.metric import MetricResult, compare_metrics, extract_metric


# ── compare_metrics ───────────────────────────────────────────────────────────

def test_compare_metrics_higher_improved():
    assert compare_metrics(10.0, 15.0, "higher") == "improved"


def test_compare_metrics_higher_worse():
    assert compare_metrics(10.0, 5.0, "higher") == "worse"


def test_compare_metrics_higher_same():
    assert compare_metrics(10.0, 10.0, "higher") == "same"


def test_compare_metrics_lower_improved():
    assert compare_metrics(10.0, 5.0, "lower") == "improved"


def test_compare_metrics_lower_worse():
    assert compare_metrics(10.0, 15.0, "lower") == "worse"


def test_compare_metrics_lower_same():
    assert compare_metrics(10.0, 10.0, "lower") == "same"


# ── extract_metric helpers ────────────────────────────────────────────────────

def _make_proc(returncode=0, stdout="", stderr=""):
    mock = MagicMock()
    mock.returncode = returncode
    mock.stdout = stdout
    mock.stderr = stderr
    return mock


# ── extract_metric: success paths ─────────────────────────────────────────────

def test_extract_metric_last_float():
    with patch("subprocess.run", return_value=_make_proc(stdout="score: 87.5\n")):
        result = extract_metric("echo 87.5", cwd=".")
    assert result.success is True
    assert result.value == pytest.approx(87.5)
    assert result.error is None


def test_extract_metric_picks_last_float():
    with patch("subprocess.run", return_value=_make_proc(stdout="10 20 30")):
        result = extract_metric("cmd", cwd=".")
    assert result.value == pytest.approx(30.0)


def test_extract_metric_integer_is_parsed():
    with patch("subprocess.run", return_value=_make_proc(stdout="42")):
        result = extract_metric("cmd", cwd=".")
    assert result.success is True
    assert result.value == pytest.approx(42.0)


def test_extract_metric_raw_output_capped():
    big = "1 " * 2000  # > 2000 chars
    with patch("subprocess.run", return_value=_make_proc(stdout=big)):
        result = extract_metric("cmd", cwd=".")
    assert len(result.raw_output) <= 2000


# ── extract_metric: failure paths ─────────────────────────────────────────────

def test_extract_metric_nonzero_exit():
    with patch("subprocess.run", return_value=_make_proc(returncode=1, stdout="87")):
        result = extract_metric("cmd", cwd=".")
    assert result.success is False
    assert result.value is None
    assert "exit" in result.error.lower()


def test_extract_metric_no_float_in_output():
    with patch("subprocess.run", return_value=_make_proc(stdout="no numbers here")):
        result = extract_metric("cmd", cwd=".")
    assert result.success is False
    assert result.value is None
    assert "No numeric" in result.error


def test_extract_metric_timeout():
    with patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="cmd", timeout=5)):
        result = extract_metric("cmd", cwd=".", timeout=5)
    assert result.success is False
    assert "timed out" in result.error


def test_extract_metric_generic_exception():
    with patch("subprocess.run", side_effect=OSError("disk error")):
        result = extract_metric("cmd", cwd=".")
    assert result.success is False
    assert "disk error" in result.error


# ── extract_metric: metric_pattern ────────────────────────────────────────────

def test_extract_metric_with_pattern_success():
    with patch("subprocess.run", return_value=_make_proc(stdout="coverage: 72.3%")):
        result = extract_metric("cmd", cwd=".", metric_pattern=r"coverage: (\d+\.\d+)")
    assert result.success is True
    assert result.value == pytest.approx(72.3)


def test_extract_metric_with_pattern_not_found():
    with patch("subprocess.run", return_value=_make_proc(stdout="something else")):
        result = extract_metric("cmd", cwd=".", metric_pattern=r"coverage: (\d+\.\d+)")
    assert result.success is False
    assert "not found" in result.error


def test_extract_metric_with_pattern_bad_capture():
    # Pattern matches but capture group can't parse as float
    with patch("subprocess.run", return_value=_make_proc(stdout="val: abc")):
        result = extract_metric("cmd", cwd=".", metric_pattern=r"val: (\w+)")
    assert result.success is False


# ── MetricResult dataclass ────────────────────────────────────────────────────

def test_metric_result_default_error_is_none():
    r = MetricResult(value=1.0, raw_output="1", success=True)
    assert r.error is None


def test_metric_result_stores_error():
    r = MetricResult(value=None, raw_output="", success=False, error="oops")
    assert r.error == "oops"
