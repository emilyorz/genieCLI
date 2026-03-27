"""
runtime/journal.py — TSV-format iteration journal for the autoresearch loop.

Records every iteration's outcome in a tab-separated values file for later
analysis and resumability.  Column schema is compatible with the autoresearch
TSV convention:

  iteration | commit | metric | delta | guard | status | description
"""
from __future__ import annotations

import csv
from pathlib import Path


COLUMNS = ["iteration", "commit", "metric", "delta", "guard", "status", "description"]


class JournalWriter:
    """Appends iteration records to a TSV journal file.

    Creates the file with a header row on first use; subsequent calls append.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        if not self.path.exists():
            self._write_header()

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _write_header(self) -> None:
        with self.path.open("w", newline="", encoding="utf-8") as fh:
            csv.DictWriter(fh, fieldnames=COLUMNS, delimiter="\t").writeheader()

    def _append_row(self, row: dict) -> None:
        with self.path.open("a", newline="", encoding="utf-8") as fh:
            csv.DictWriter(fh, fieldnames=COLUMNS, delimiter="\t").writerow(row)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def write_baseline(
        self,
        metric: float | None,
        commit: str = "",
        description: str = "baseline",
    ) -> None:
        """Write the iteration-0 baseline row."""
        self._append_row({
            "iteration": 0,
            "commit": commit,
            "metric": metric if metric is not None else "N/A",
            "delta": 0.0,
            "guard": "pass",
            "status": "baseline",
            "description": description,
        })

    def write_iteration(
        self,
        iteration: int,
        commit: str,
        metric: float | None,
        delta: float | None,
        guard: str,
        status: str,
        description: str = "",
    ) -> None:
        """Write a single iteration result row."""
        self._append_row({
            "iteration": iteration,
            "commit": commit,
            "metric": metric if metric is not None else "N/A",
            "delta": delta if delta is not None else "N/A",
            "guard": guard,
            "status": status,
            "description": description,
        })

    def read_recent(self, n: int) -> list[dict]:
        """Return the last n data rows (excluding the header) from the journal."""
        if not self.path.exists():
            return []
        with self.path.open("r", newline="", encoding="utf-8") as fh:
            rows = list(csv.DictReader(fh, delimiter="\t"))
        return rows[-n:] if len(rows) > n else rows
