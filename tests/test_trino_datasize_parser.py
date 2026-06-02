"""Tests for _parse_trino_datasize in genie/skills/mcp_trino/research.py.

Pure parser — binary base (1 kB = 1024 B), case-insensitive units,
whitespace-tolerant, fractional values. Returns None for all error cases.
"""
from __future__ import annotations

import pytest

from genie.skills.mcp_trino.research import _parse_trino_datasize


# ---------------------------------------------------------------------------
# Valid inputs (A cases)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("s,expected", [
    # A01 — 1 GB
    ("1GB", 1 * 1024**3),
    # A02 — 5 GB
    ("5GB", 5 * 1024**3),
    # A03 — 512 MB
    ("512MB", 512 * 1024**2),
    # A04 — 1.5 kB (fractional, binary base → 1.5 × 1024 = 1536)
    ("1.5kB", 1536),
    # A05 — 2 TB
    ("2TB", 2 * 1024**4),
    # A06 — 1024 B
    ("1024B", 1024),
    # A07 — 2 GiB (binary suffix alias)
    ("2GiB", 2 * 1024**3),
    # A08 — 256 MiB
    ("256MiB", 256 * 1024**2),
    # A09 — 4 KiB
    ("4KiB", 4096),
    # A10 — 1 PB
    ("1PB", 1 * 1024**5),
    # A11 — lowercase unit
    ("1gb", 1 * 1024**3),
    # A12 — leading/trailing whitespace
    ("  512 MB  ", 512 * 1024**2),
])
def test_parse_trino_datasize_valid(s, expected):
    assert _parse_trino_datasize(s) == expected


# ---------------------------------------------------------------------------
# Invalid inputs → None (B cases)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("s", [
    # B01 — empty string
    "",
    # B02 — None
    None,
    # B03 — no numeric part
    "xyz",
    # B04 — bare number (no unit)
    "1024",
    # B05 — zero (0B → 0 bytes → None)
    "0B",
    # B06 — negative (regex requires leading digit, so no match)
    "-1GB",
    # B07 — non-string (int)
    42,
    # B08 — unknown unit
    "1XB",
    # B09 — overflow-scale number: float('1e400') == inf → must return None, not raise
    "1e400GB",
    # B10 — absurdly long decimal string that also overflows to inf
    ("1" * 400 + "GB"),
])
def test_parse_trino_datasize_invalid_returns_none(s):
    assert _parse_trino_datasize(s) is None


def test_parse_trino_datasize_overflow_does_not_raise():
    """Regression: absurd exponent overflows float to inf; must return None without raising OverflowError."""
    assert _parse_trino_datasize("1e400GB") is None
    assert _parse_trino_datasize("1" * 400 + "GB") is None
