"""
runtime/checkpoint.py — Git-based checkpoint management for the autoresearch loop.

Provides create/restore operations that snapshot working-tree state so the
run manager can revert failed iterations without manual intervention.

All functions return plain dicts ({"ok": True, ...} or {"error": "..."})
instead of raising exceptions, keeping error handling structural.
"""
from __future__ import annotations

import re
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


def _sanitize_label(label: str) -> str:
    """Replace characters outside [a-zA-Z0-9_-] with underscores."""
    return re.sub(r"[^a-zA-Z0-9_-]", "_", label)


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
    Commit current changes on the current branch without switching branches.

    Records the HEAD SHA before committing (original_head) so that
    checkpoint_restore() can reset back to that exact state.

    Returns {"label", "original_head", "commit_sha", "timestamp", "cwd"}
    or {"error": "..."} on failure.
    """
    cwd = str(cwd)
    timestamp = int(time.time())
    label = _sanitize_label(label)

    # Record HEAD before staging so restore can return here
    code, original_head, err = _run_git(["rev-parse", "HEAD"], cwd)
    if code != 0:
        return {"error": f"Could not determine current HEAD: {err}"}

    code, _, err = _run_git(["add", "-A"], cwd)
    if code != 0:
        return {"error": f"Failed to stage changes: {err}"}

    code, _, err = _run_git(
        ["commit", "--allow-empty", "-m", f"checkpoint: {label}"], cwd
    )
    if code != 0:
        return {"error": f"Failed to commit checkpoint: {err}"}

    code, commit_sha, err = _run_git(["rev-parse", "HEAD"], cwd)
    if code != 0:
        return {"error": f"Could not read new HEAD SHA: {err}"}

    return {
        "label": label,
        "original_head": original_head,
        "commit_sha": commit_sha,
        "timestamp": timestamp,
        "cwd": cwd,
    }


def checkpoint_restore(checkpoint_info: dict, cwd: str | Path) -> dict:
    """
    Restore the working tree to the state before the checkpoint was created.

    Resets --hard to original_head, discarding the checkpoint commit and any
    subsequent changes.

    Returns {"ok": True, "restored_head": str} or {"error": "..."}.
    """
    cwd = str(cwd)
    original_head = checkpoint_info.get("original_head")
    if not original_head:
        return {"error": "checkpoint_info missing 'original_head'"}

    code, _, err = _run_git(["reset", "--hard", original_head], cwd)
    if code != 0:
        return {"error": f"Failed to reset --hard to {original_head}: {err}"}

    return {"ok": True, "restored_head": original_head}
