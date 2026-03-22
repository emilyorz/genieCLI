from pathlib import Path
from .base import Arg, BaseSkill


class ReadFile(BaseSkill):
    name        = "read_file"
    description = "Read content of a local file"
    group       = "file"
    args        = [
        Arg(name="path", type="str",
            description="Absolute or relative file path",
            required=True),
    ]

    def run(self, path=""):
        try:
            content = Path(path).read_text(encoding="utf-8", errors="replace")
            return content[:5000]
        except Exception as e:
            return f"Error reading file: {e}"


class WriteFile(BaseSkill):
    name        = "write_file"
    description = "Write text content to a local file"
    group       = "file"
    args        = [
        Arg(name="path", type="str",
            description="File path to write to",
            required=True),
        Arg(name="content", type="str",
            description="Text content to write",
            required=True),
    ]

    def run(self, path="", content=""):
        try:
            p = Path(path)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content, encoding="utf-8")
            return f"Written {len(content)} chars to: {path}"
        except Exception as e:
            return f"Error writing file: {e}"


class ListFiles(BaseSkill):
    name        = "list_files"
    description = "List files in a directory"
    group       = "file"
    args        = [
        Arg(name="path", type="str",
            description="Directory path",
            required=False, default="."),
    ]

    def run(self, path="."):
        try:
            entries = sorted(Path(path).iterdir())
            lines   = []
            for e in entries[:50]:
                tag = "[DIR] " if e.is_dir() else "      "
                lines.append(f"{tag}{e.name}")
            if len(entries) > 50:
                lines.append(f"... and {len(entries)-50} more")
            return "\n".join(lines) if lines else "(empty)"
        except Exception as e:
            return f"Error listing files: {e}"
