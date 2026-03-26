"""
skills/file_ops.py — File mutation primitive for the autoresearch loop.

Provides a safe search-and-replace patch that validates old_text exists
(exactly once) before modifying the file, preventing silent no-ops and
accidental multi-site replacements.

Path boundary enforcement prevents writes outside the working directory,
including via symlinks that resolve to external paths.
"""
from __future__ import annotations

from pathlib import Path

from .base import Arg, BaseSkill


class FilePatch(BaseSkill):
    name = "file_patch"
    description = (
        "Apply a search-and-replace patch to a file. "
        "Fails if old_text is not found, or if it appears more than once "
        "(use more context to make it unique in that case). "
        "The target path must resolve within the working directory."
    )
    group = "file"
    args = [
        Arg(
            name="path",
            type="string",
            description="Path to the file to modify",
            required=True,
        ),
        Arg(
            name="old_text",
            type="string",
            description="Exact text to search for (must exist exactly once in the file)",
            required=True,
        ),
        Arg(
            name="new_text",
            type="string",
            description="Replacement text",
            required=True,
        ),
        Arg(
            name="cwd",
            type="string",
            description="Working directory used as the path boundary (default: '.')",
            required=False,
            default=".",
        ),
    ]

    def run(self, **kwargs) -> str:
        path = Path(kwargs["path"])
        old_text: str = kwargs["old_text"]
        new_text: str = kwargs["new_text"]
        cwd_path = Path(kwargs.get("cwd", ".")).resolve()

        # Enforce path boundary: resolve follows symlinks, so external symlinks
        # are caught here too.
        try:
            resolved = path.resolve()
            resolved.relative_to(cwd_path)
        except ValueError:
            return (
                f"ERROR: Path '{path}' resolves to '{resolved}', "
                f"which is outside working directory '{cwd_path}'"
            )

        if not path.exists():
            return f"ERROR: File not found: {path}"
        if not path.is_file():
            return f"ERROR: Path is not a file: {path}"

        content = path.read_text(encoding="utf-8")
        count = content.count(old_text)

        if count == 0:
            return f"ERROR: old_text not found in {path}"
        if count > 1:
            return (
                f"ERROR: old_text found {count} times in {path}. "
                "Provide more surrounding context to make it unique."
            )

        path.write_text(content.replace(old_text, new_text, 1), encoding="utf-8")
        return f"OK: patched {path} (replaced 1 occurrence)"
