#!/usr/bin/env python3
"""Validate v57/v58 /trino-research safety/parity claims.

Default mode is offline/local and safe: it checks repository documentation and runs
representative pytest suites for v57/v58 surfaces. Live company Trino/Qwen
validation is intentionally opt-in and never faked.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
STATUS = ROOT / "project-iterations" / "genieCLI" / "STATUS.md"
ARCHIVE = ROOT / "project-iterations" / "genieCLI" / "archive"
TARGETED_TESTS = [
    "tests/test_decompose_then_iterate.py",
    "tests/test_mcp_research.py",
]
BROAD_SWEEP = [
    "tests/test_mcp_research.py",
    "tests/test_strategy_verify.py",
    "tests/test_decompose_then_iterate.py",
    "tests/test_step_trace.py",
    "tests/test_trino_optimize.py",
    "tests/test_critical_path.py",
]


class CheckResult:
    def __init__(self, name: str, ok: bool, detail: str, status: str | None = None) -> None:
        self.name = name
        self.ok = ok
        self.detail = detail
        self.status = status or ("PASS" if ok else "FAIL")


def run(cmd: list[str]) -> tuple[int, str]:
    proc = subprocess.run(
        cmd,
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    return proc.returncode, proc.stdout.strip()


def doc_checks() -> list[CheckResult]:
    text = STATUS.read_text(encoding="utf-8") if STATUS.exists() else ""
    required_terms = [
        "v59",
        "v58",
        "v57",
        "6cd9640",
        "GENIE_FRAGMENT_REWRITE",
        "GENIE_FRAGMENT_REWRITE_CAP",
        "live company Trino/Qwen validation",
    ]
    results: list[CheckResult] = []
    for term in required_terms:
        results.append(CheckResult(f"STATUS contains {term}", term in text, term))
    for name in ["v57.md", "v58.md", "v59.md"]:
        path = ARCHIVE / name
        results.append(CheckResult(f"archive/{name} exists", path.exists(), str(path.relative_to(ROOT))))
    return results


def pytest_check(name: str, tests: list[str]) -> CheckResult:
    code, out = run([sys.executable, "-m", "pytest", *tests, "-q"])
    tail = "\n".join(out.splitlines()[-8:])
    return CheckResult(name, code == 0, tail)


def live_check() -> CheckResult:
    if os.environ.get("GENIE_V59_LIVE_VALIDATION") != "1":
        return CheckResult(
            "live company Trino/Qwen validation",
            True,
            "PENDING: set GENIE_V59_LIVE_VALIDATION=1 in an authorized environment; no live Trino/Qwen contact attempted.",
            status="PENDING",
        )
    # This script intentionally does not know private company endpoints or queries.
    # Authorized operators should run the real /trino-research command separately and
    # attach/redact the output in the v59 report/archive.
    return CheckResult(
        "live company Trino/Qwen validation",
        False,
        "LIVE REQUESTED but no company endpoint/query runner is configured in this repo-local script; run the authorized external validation and record redacted evidence.",
    )


def emit_markdown(results: list[CheckResult]) -> int:
    print("# v57/v58 /trino-research validation")
    print()
    print("Mode: local representative validation unless a live item explicitly says otherwise.")
    print()
    failures = 0
    pending = 0
    for result in results:
        marker = result.status
        print(f"## {marker}: {result.name}")
        print()
        print("```text")
        print(result.detail or "(no output)")
        print("```")
        print()
        failures += 0 if result.ok else 1
        pending += 1 if result.status == "PENDING" else 0
    print(f"Summary: {len(results) - failures - pending} passed / {pending} pending / {failures} failed")
    return 1 if failures else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--offline", action="store_true", help="run local representative checks (default)")
    parser.add_argument("--live", action="store_true", help="also report live validation status; requires explicit authorized env")
    parser.add_argument("--broad", action="store_true", help="run broader v57/v58 pytest sweep")
    args = parser.parse_args(argv)

    results = doc_checks()
    results.append(pytest_check("targeted v57/v58 pytest", TARGETED_TESTS))
    if args.broad:
        results.append(pytest_check("broad v57/v58 pytest sweep", BROAD_SWEEP))
    if args.live:
        results.append(live_check())
    return emit_markdown(results)


if __name__ == "__main__":
    raise SystemExit(main())
