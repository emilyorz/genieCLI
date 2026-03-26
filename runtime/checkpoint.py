"""
runtime/checkpoint.py — Git-based checkpoint management for the autoresearch loop.

Provides create/restore operations that snapshot working-tree state so the
run manager can revert failed iterations without manual intervention.

All functions return plain dicts ({"ok": True, ...} or {"error": "..."})
instead of raising exceptions, keeping error handling structural.
"""
from __future__ import annotations

import subprocess
import time
from pathlib import Path


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _run_git(args: list[str], cwd: str) -> tuple[int, str, str]:
    """Run a git command and return (returncode, stdout, stderr)."""
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
    )
    return result.returncode, result.stdout.strip(), result.stderr.strip()


def _get_current_branch(cwd: str) -> str | None:
    """Return the current branch name, or None on failure."""
    code, stdout, _ = _run_git(["rev-parse", "--abbrev-ref", "HEAD"], cwd)
    return stdout if code == 0 else None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def git_is_repo(cwd: str | Path) -> bool:
    """Return True if cwd is inside a git repository."""
    code, _, _ = _run_git(["rev-parse", "--is-inside-work-tree"], str(cwd))
    return code == 0


def git_is_clean(cwd: str | Path) -> bool:
    """Return True if the working tree has no uncommitted changes."""
    code, stdout, _ = _run_git(["status", "--porcelain"], str(cwd))
    return code == 0 and stdout == ""


def checkpoint_create(label: str, cwd: str | Path) -> dict:
    """
    Commit current changes to a checkpoint branch.

    Creates branch autoresearch-{label}-{timestamp}, stages all changes,
    commits, then returns to the original branch.

    Returns {"label", "branch", "original_branch", "timestamp", "cwd"}
    or {"error": "..."} on failure.
    """
    cwd = str(cwd)
    timestamp = int(time.time())
    branch_name = f"autoresearch-{label}-{timestamp}"

    original_branch = _get_current_branch(cwd)
    if original_branch is None:
        return {"error": "Could not determine current branch"}

    code, _, err = _run_git(["checkout", "-b", branch_name], cwd)
    if code != 0:
        return {"error": f"Failed to create branch '{branch_name}': {err}"}

    code, _, err = _run_git(["add", "-A"], cwd)
    if code != 0:
        # Try to get back before bailing
        _run_git(["checkout", original_branch], cwd)
        return {"error": f"Failed to stage changes: {err}"}

    code, _, err = _run_git(
        ["commit", "--allow-empty", "-m", f"checkpoint: {label}"], cwd
    )
    if code != 0:
        _run_git(["checkout", original_branch], cwd)
        return {"error": f"Failed to commit checkpoint: {err}"}

    code, _, err = _run_git(["checkout", original_branch], cwd)
    if code != 0:
        return {"error": f"Failed to return to original branch '{original_branch}': {err}"}

    return {
        "label": label,
        "branch": branch_name,
        "original_branch": original_branch,
        "timestamp": timestamp,
        "cwd": cwd,
    }


def checkpoint_restore(checkpoint_info: dict, cwd: str | Path) -> dict:
    """
    Restore the working tree to the state before a checkpoint was created.

    Checks out original_branch and resets --hard to HEAD, discarding any
    changes made after the checkpoint.

    Returns {"ok": True, "restored_branch": str} or {"error": "..."}.
    """
    cwd = str(cwd)
    original_branch = checkpoint_info.get("original_branch")
    if not original_branch:
        return {"error": "checkpoint_info missing 'original_branch'"}

    code, _, err = _run_git(["checkout", original_branch], cwd)
    if code != 0:
        return {"error": f"Failed to checkout '{original_branch}': {err}"}

    code, _, err = _run_git(["reset", "--hard", "HEAD"], cwd)
    if code != 0:
        return {"error": f"Failed to reset --hard: {err}"}

    return {"ok": True, "restored_branch": original_branch}
