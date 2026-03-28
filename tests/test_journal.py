"""Tests for genie/runtime/journal.py"""
from __future__ import annotations

import csv
from pathlib import Path

import pytest

from genie.runtime.journal import COLUMNS, JournalWriter


# ── JournalWriter: creation ───────────────────────────────────────────────────

def test_creates_file_with_header(tmp_path):
    path = tmp_path / "journal.tsv"
    JournalWriter(path)
    assert path.exists()
    rows = path.read_text(encoding="utf-8").splitlines()
    assert rows[0] == "\t".join(COLUMNS)


def test_does_not_overwrite_existing_file(tmp_path):
    path = tmp_path / "journal.tsv"
    # Pre-create with a data row
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=COLUMNS, delimiter="\t")
        w.writeheader()
        w.writerow({c: "existing" for c in COLUMNS})

    JournalWriter(path)  # Should not wipe the file
    rows = list(csv.DictReader(path.open(newline="", encoding="utf-8"), delimiter="\t"))
    assert len(rows) == 1
    assert rows[0]["iteration"] == "existing"


# ── write_baseline ────────────────────────────────────────────────────────────

def test_write_baseline_appends_row(tmp_path):
    path = tmp_path / "j.tsv"
    jw = JournalWriter(path)
    jw.write_baseline(metric=8.0, commit="abc123", description="test run")

    rows = _read_rows(path)
    assert len(rows) == 1
    assert rows[0]["iteration"] == "0"
    assert rows[0]["metric"] == "8.0"
    assert rows[0]["status"] == "baseline"
    assert rows[0]["description"] == "test run"


def test_write_baseline_none_metric(tmp_path):
    path = tmp_path / "j.tsv"
    jw = JournalWriter(path)
    jw.write_baseline(metric=None, commit="abc")

    rows = _read_rows(path)
    assert rows[0]["metric"] == "N/A"


def test_write_baseline_default_description(tmp_path):
    path = tmp_path / "j.tsv"
    jw = JournalWriter(path)
    jw.write_baseline(metric=5.0)

    rows = _read_rows(path)
    assert rows[0]["description"] == "baseline"


# ── write_iteration ───────────────────────────────────────────────────────────

def test_write_iteration_appends_row(tmp_path):
    path = tmp_path / "j.tsv"
    jw = JournalWriter(path)
    jw.write_baseline(metric=8.0)
    jw.write_iteration(
        iteration=1,
        commit="deadbeef",
        metric=12.0,
        delta=4.0,
        guard="pass",
        status="improved",
        description="added tests",
    )

    rows = _read_rows(path)
    assert len(rows) == 2
    assert rows[1]["iteration"] == "1"
    assert rows[1]["commit"] == "deadbeef"
    assert rows[1]["metric"] == "12.0"
    assert rows[1]["delta"] == "4.0"
    assert rows[1]["guard"] == "pass"
    assert rows[1]["status"] == "improved"
    assert rows[1]["description"] == "added tests"


def test_write_iteration_none_metric_and_delta(tmp_path):
    path = tmp_path / "j.tsv"
    jw = JournalWriter(path)
    jw.write_iteration(
        iteration=2, commit="", metric=None, delta=None,
        guard="error", status="error",
    )
    rows = _read_rows(path)
    assert rows[0]["metric"] == "N/A"
    assert rows[0]["delta"] == "N/A"


def test_write_multiple_iterations(tmp_path):
    path = tmp_path / "j.tsv"
    jw = JournalWriter(path)
    jw.write_baseline(metric=8.0)
    for i in range(1, 6):
        jw.write_iteration(
            iteration=i, commit=f"sha{i}", metric=float(8 + i),
            delta=float(i), guard="pass", status="improved",
        )
    rows = _read_rows(path)
    assert len(rows) == 6
    assert rows[-1]["iteration"] == "5"


# ── read_recent ───────────────────────────────────────────────────────────────

def test_read_recent_returns_last_n(tmp_path):
    path = tmp_path / "j.tsv"
    jw = JournalWriter(path)
    jw.write_baseline(metric=0.0)
    for i in range(1, 6):
        jw.write_iteration(
            iteration=i, commit=f"c{i}", metric=float(i),
            delta=float(i), guard="pass", status="improved",
        )
    recent = jw.read_recent(3)
    assert len(recent) == 3
    assert recent[-1]["iteration"] == "5"


def test_read_recent_all_when_fewer_than_n(tmp_path):
    path = tmp_path / "j.tsv"
    jw = JournalWriter(path)
    jw.write_baseline(metric=1.0)
    recent = jw.read_recent(10)
    assert len(recent) == 1


def test_read_recent_missing_file(tmp_path):
    path = tmp_path / "nonexistent.tsv"
    jw = JournalWriter.__new__(JournalWriter)
    jw.path = path
    assert jw.read_recent(5) == []


# ── Helpers ───────────────────────────────────────────────────────────────────

def _read_rows(path: Path) -> list[dict]:
    with path.open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh, delimiter="\t"))
