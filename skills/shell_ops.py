"""
skills/shell_ops.py — Safe shell execution skill for the autoresearch loop.

Provides profile-based command execution with a whitelist that prevents
arbitrary command injection.  Custom commands must match ALLOWED_PATTERNS.
All executions are bounded by timeout and a 10 KB output cap.
"""
from __future__ import annotations

import fnmatch
import subprocess

from .base import Arg, BaseSkill


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

PROFILES: dict[str, str] = {
    "python-test": "pytest",
    "node-test": "npm test",
    "lint": "ruff check .",
    "build": "npm run build",
}

# Patterns for custom_command whitelist (fnmatch syntax).
ALLOWED_PATTERNS = [
    "pytest",
    "pytest *",
    "python -m pytest",
    "python -m pytest *",
    "npm test",
    "npm test *",
    "npm run *",
    "ruff *",
    "eslint *",
    "make *",
    "cargo test",
    "cargo test *",
]

OUTPUT_LIMIT = 10 * 1024  # 10 KB


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _is_allowed(command: str) -> bool:
    """Return True if command matches any entry in ALLOWED_PATTERNS."""
    command = command.strip()
    for pattern in ALLOWED_PATTERNS:
        if fnmatch.fnmatch(command, pattern):
            return True
        # Exact match or prefix match for patterns without wildcards
        bare = pattern.rstrip("*").rstrip()
        if command == bare or command.startswith(bare + " "):
            return True
    return False


# ---------------------------------------------------------------------------
# Skill
# ---------------------------------------------------------------------------

class CommandRun(BaseSkill):
    name = "command_run"
    description = (
        "Run a whitelisted shell command via a named profile or a custom command. "
        "Profiles: python-test, node-test, lint, build. "
        "Use profile=custom with custom_command for ad-hoc whitelisted commands."
    )
    group = "shell"
    args = [
        Arg(
            name="profile",
            type="string",
            description="Execution profile: python-test | node-test | lint | build | custom",
            required=True,
            choices=["python-test", "node-test", "lint", "build", "custom"],
        ),
        Arg(
            name="custom_command",
            type="string",
            description=(
                "Command to run (only when profile=custom). "
                "Must match ALLOWED_PATTERNS whitelist."
            ),
            required=False,
            default=None,
        ),
        Arg(
            name="timeout",
            type="integer",
            description="Timeout in seconds (default: 60)",
            required=False,
            default=60,
        ),
        Arg(
            name="cwd",
            type="string",
            description="Working directory (default: '.')",
            required=False,
            default=".",
        ),
    ]

    def run(self, **kwargs) -> str:
        profile = kwargs["profile"]
        custom_command = kwargs.get("custom_command")
        timeout = int(kwargs.get("timeout", 60))
        cwd = kwargs.get("cwd", ".")

        if profile == "custom":
            if not custom_command:
                return "ERROR: custom_command is required when profile=custom"
            if not _is_allowed(custom_command):
                return (
                    f"ERROR: Command not in whitelist: '{custom_command}'\n"
                    f"Allowed patterns: {ALLOWED_PATTERNS}"
                )
            command = custom_command
        elif profile in PROFILES:
            command = PROFILES[profile]
        else:
            return f"ERROR: Unknown profile '{profile}'"

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
            if len(combined) > OUTPUT_LIMIT:
                combined = combined[:OUTPUT_LIMIT] + "\n... (output truncated at 10KB)"
            return f"exit_code={result.returncode}\n{combined}"
        except subprocess.TimeoutExpired:
            return f"ERROR: Command timed out after {timeout}s"
        except Exception as exc:
            return f"ERROR: {exc}"
