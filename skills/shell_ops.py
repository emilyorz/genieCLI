"""
skills/shell_ops.py — Safe shell execution skill for the autoresearch loop.

Provides profile-based command execution with a whitelist that prevents
arbitrary command injection.  Custom commands are parsed with shlex and
executed with shell=False, so chained operators like && cannot bypass the
whitelist.
"""
from __future__ import annotations

import shlex
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

ALLOWED_EXECUTABLES = {"pytest", "python", "python3", "npm", "ruff", "eslint", "make", "cargo"}

# Tokens that indicate shell chaining — reject any command containing these
_SHELL_OPERATORS = {"&&", "||", ";", "|", ">", ">>", "<", "&"}

OUTPUT_LIMIT = 10 * 1024  # 10 KB


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _is_allowed(command: str) -> bool:
    """
    Return True if command is safe to execute.

    Parses with shlex and rejects any token that is a shell operator, so
    chained commands like `pytest && rm -rf /` are blocked even though shlex
    tokenises them without error.  Only whitelisted executables are permitted;
    python/python3 are further restricted to `python -m pytest` invocations.
    """
    try:
        parts = shlex.split(command)
    except ValueError:
        return False
    if not parts:
        return False
    # Reject any shell operator tokens regardless of position
    if _SHELL_OPERATORS.intersection(parts):
        return False
    exe = parts[0]
    if exe not in ALLOWED_EXECUTABLES:
        return False
    # python/python3 only allowed as: python -m pytest [args...]
    if exe in ("python", "python3"):
        if not (len(parts) >= 3 and parts[1] == "-m" and parts[2] == "pytest"):
            return False
    return True


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
                "Must match the executable whitelist."
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
                    f"Allowed executables: {sorted(ALLOWED_EXECUTABLES)}"
                )
            command = custom_command
        elif profile in PROFILES:
            command = PROFILES[profile]
        else:
            return f"ERROR: Unknown profile '{profile}'"

        try:
            result = subprocess.run(
                shlex.split(command),
                shell=False,
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
