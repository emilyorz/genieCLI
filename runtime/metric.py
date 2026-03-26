"""
runtime/metric.py — Metric extraction and directional comparison.

Runs a shell command and parses a float metric from its combined output.
Comparison supports both "higher is better" and "lower is better" directions.

Design: pure functions only; no global state.
"""
from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from typing import Literal


@dataclass
class MetricResult:
    value: float | None
    raw_output: str
    success: bool
    error: str | None = None


def extract_metric(
    command: str,
    cwd: str,
    timeout: int = 60,
) -> MetricResult:
    """
    Run command and extract the last float value from combined stdout + stderr.

    The last float found in output is used as the metric (common for test
    pass-rates, benchmark scores, coverage percentages, etc.).
    Raw output is capped at 2 000 characters to keep downstream logs compact.
    """
    try:
        result = subprocess.run(
            command,
            shell=True,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        combined = result.stdout + result.stderr
        floats = re.findall(r"[-+]?\d+(?:\.\d+)?", combined)
        raw = combined[:2000]
        if not floats:
            return MetricResult(
                value=None,
                raw_output=raw,
                success=False,
                error="No numeric value found in command output",
            )
        return MetricResult(value=float(floats[-1]), raw_output=raw, success=True)
    except subprocess.TimeoutExpired:
        return MetricResult(
            value=None,
            raw_output="",
            success=False,
            error=f"Command timed out after {timeout}s",
        )
    except Exception as exc:
        return MetricResult(value=None, raw_output="", success=False, error=str(exc))


def compare_metrics(
    baseline: float,
    current: float,
    direction: Literal["higher", "lower"],
) -> Literal["improved", "same", "worse"]:
    """
    Compare current metric against baseline given the optimization direction.

    Returns "improved", "same", or "worse".
    """
    if current == baseline:
        return "same"
    if direction == "higher":
        return "improved" if current > baseline else "worse"
    # direction == "lower"
    return "improved" if current < baseline else "worse"
