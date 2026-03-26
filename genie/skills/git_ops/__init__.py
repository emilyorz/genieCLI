"""Git operations skill package."""
from __future__ import annotations

import subprocess

from genie.core.arg import Arg
from genie.core.registry import BaseSkill
from genie.runtime.checkpoint import checkpoint_create, checkpoint_restore


class GitStatus(BaseSkill):
    name = "git_status"
    description = "Show working tree status (git status --porcelain)"
    group = "git"
    args: list = []

    def run(self, **kwargs) -> str:
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            return f"ERROR: {result.stderr.strip()}"
        output = result.stdout.strip()
        return output if output else "(clean — no changes)"


class GitDiff(BaseSkill):
    name = "git_diff"
    description = "Show git diff. target: 'worktree' (default), 'staged', or 'HEAD~1'"
    group = "git"
    args = [
        Arg(name="target", type="string",
            description="What to diff: worktree, staged, or HEAD~1",
            required=False, default="worktree",
            choices=["worktree", "staged", "HEAD~1"]),
    ]

    def run(self, **kwargs) -> str:
        target = kwargs.get("target", "worktree")
        if target == "staged":
            cmd = ["git", "diff", "--cached"]
        elif target == "HEAD~1":
            cmd = ["git", "diff", "HEAD~1"]
        else:
            cmd = ["git", "diff"]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            return f"ERROR: {result.stderr.strip()}"
        output = result.stdout.strip()
        if len(output) > 10240:
            output = output[:10240] + "\n... (truncated at 10KB)"
        return output if output else "(no diff)"


class GitLog(BaseSkill):
    name = "git_log"
    description = "Show recent git log, one line per commit"
    group = "git"
    args = [
        Arg(name="n", type="integer",
            description="Number of commits to show (default: 10)",
            required=False, default=10),
    ]

    def run(self, **kwargs) -> str:
        n = int(kwargs.get("n", 10))
        result = subprocess.run(
            ["git", "log", "--oneline", f"-{n}"],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            return f"ERROR: {result.stderr.strip()}"
        return result.stdout.strip() or "(no commits)"


class GitCheckpointCreate(BaseSkill):
    name = "git_checkpoint_create"
    description = "Create a git checkpoint capturing the current working-tree state."
    group = "git"
    args = [
        Arg(name="label", type="string",
            description="Short label for the checkpoint",
            required=True),
        Arg(name="cwd", type="string",
            description="Working directory path (default: '.')",
            required=False, default="."),
    ]

    def run(self, **kwargs) -> str:
        label = kwargs["label"]
        cwd = kwargs.get("cwd", ".")
        info = checkpoint_create(label, cwd)
        if "error" in info:
            return f"ERROR: {info['error']}"
        return (
            f"Checkpoint created: commit={info['commit_sha'][:8]}, "
            f"original_head={info['original_head'][:8]}, timestamp={info['timestamp']}"
        )


class GitCheckpointRestore(BaseSkill):
    name = "git_checkpoint_restore"
    description = "Restore the working tree to its state before a checkpoint was created."
    group = "git"
    args = [
        Arg(name="original_head", type="string",
            description="SHA to restore to (from checkpoint info)",
            required=True),
        Arg(name="cwd", type="string",
            description="Working directory path (default: '.')",
            required=False, default="."),
    ]

    def run(self, **kwargs) -> str:
        checkpoint_info = {"original_head": kwargs["original_head"]}
        cwd = kwargs.get("cwd", ".")
        result = checkpoint_restore(checkpoint_info, cwd)
        if "error" in result:
            return f"ERROR: {result['error']}"
        return f"Restored to: {result['restored_head']}"


def register(registry) -> None:
    registry.register(GitStatus())
    registry.register(GitDiff())
    registry.register(GitLog())
    registry.register(GitCheckpointCreate())
    registry.register(GitCheckpointRestore())
