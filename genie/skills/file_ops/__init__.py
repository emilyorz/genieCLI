"""File operations skill package."""
from __future__ import annotations

from pathlib import Path

from genie.core.arg import Arg
from genie.core.registry import BaseSkill


class ReadFile(BaseSkill):
    name = "read_file"
    description = "Read content of a local file"
    group = "file"
    args = [
        Arg(name="path", type="str",
            description="Absolute or relative file path",
            required=True),
    ]

    def run(self, path="") -> str:
        try:
            content = Path(path).read_text(encoding="utf-8", errors="replace")
            return content[:5000]
        except Exception as exc:
            return f"Error reading file: {exc}"


class WriteFile(BaseSkill):
    name = "write_file"
    description = "Write text content to a local file"
    group = "file"
    args = [
        Arg(name="path", type="str",
            description="File path to write to",
            required=True),
        Arg(name="content", type="str",
            description="Text content to write",
            required=True),
    ]

    def run(self, path="", content="") -> str:
        try:
            p = Path(path)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content, encoding="utf-8")
            return f"Written {len(content)} chars to: {path}"
        except Exception as exc:
            return f"Error writing file: {exc}"


class ListFiles(BaseSkill):
    name = "list_files"
    description = "List files in a directory"
    group = "file"
    args = [
        Arg(name="path", type="str",
            description="Directory path",
            required=False, default="."),
    ]

    def run(self, path=".") -> str:
        try:
            entries = sorted(Path(path).iterdir())
            lines = []
            for e in entries[:50]:
                tag = "[DIR] " if e.is_dir() else "      "
                lines.append(f"{tag}{e.name}")
            if len(entries) > 50:
                lines.append(f"... and {len(entries)-50} more")
            return "\n".join(lines) if lines else "(empty)"
        except Exception as exc:
            return f"Error listing files: {exc}"


class FilePatch(BaseSkill):
    name = "file_patch"
    description = "Apply a unified diff patch to a file"
    group = "file"
    args = [
        Arg(name="path", type="str",
            description="Path to the file to patch",
            required=True),
        Arg(name="patch", type="str",
            description="Unified diff string (output of `diff -u`)",
            required=True),
    ]

    def run(self, path="", patch="") -> str:
        import subprocess
        import tempfile
        try:
            with tempfile.NamedTemporaryFile(mode="w", suffix=".patch",
                                             delete=False, encoding="utf-8") as f:
                f.write(patch)
                patch_file = f.name
            # Use GNU patch (gpatch) — BSD patch on macOS doesn't support <<<< format
            import shutil
            patch_cmd = shutil.which("gpatch") or "patch"
            result = subprocess.run(
                [patch_cmd, "-u", path, patch_file],
                capture_output=True,
                text=True,
            )
            Path(patch_file).unlink(missing_ok=True)
            if result.returncode == 0:
                return f"Patch applied successfully to {path}"
            return f"Patch failed (exit {result.returncode}): {result.stderr.strip()}"
        except Exception as exc:
            return f"Error applying patch: {exc}"


def register(registry) -> None:
    registry.register(ReadFile())
    registry.register(WriteFile())
    registry.register(ListFiles())
    registry.register(FilePatch())
