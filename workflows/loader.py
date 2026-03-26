"""
workflows/loader.py — Markdown workflow discovery and loading.

Scans the workflows/ directory for .md files with YAML frontmatter,
providing metadata extraction and system-prompt injection.

Frontmatter schema:
    ---
    name: workflow-name
    description: One-line summary
    requires:
      - skill_name_1
      - skill_name_2
    ---

Parse failures fall back gracefully so one bad file never blocks
discovery of the rest.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

_WORKFLOWS_DIR = Path(__file__).parent


class WorkflowLoader:
    """
    Discovers and loads markdown workflow definitions.

    Typical usage:
        loader = WorkflowLoader()
        for meta in loader.discover():
            print(meta["name"], meta["description"])

        body = loader.inject_prompt("autoresearch")
        ok   = loader.check_requirements("autoresearch", available_skill_names)
    """

    def __init__(self, directory: Path | str | None = None) -> None:
        self._dir = Path(directory) if directory else _WORKFLOWS_DIR

    # ------------------------------------------------------------------
    # Frontmatter parsing
    # ------------------------------------------------------------------

    def _parse_frontmatter(self, text: str) -> tuple[dict[str, Any], str]:
        """
        Extract YAML frontmatter and body from markdown text.

        Returns (metadata_dict, body_text).  On any parse failure returns
        ({}, full_text) so callers always receive usable content.
        """
        if not text.startswith("---"):
            return {}, text

        lines = text.splitlines()
        end_idx: int | None = None
        for i, line in enumerate(lines[1:], start=1):
            if line.strip() == "---":
                end_idx = i
                break

        if end_idx is None:
            return {}, text

        yaml_block = "\n".join(lines[1:end_idx])
        body = "\n".join(lines[end_idx + 1:]).strip()

        try:
            meta = yaml.safe_load(yaml_block) or {}
            if not isinstance(meta, dict):
                meta = {}
        except yaml.YAMLError:
            meta = {}

        return meta, body

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def discover(self) -> list[dict[str, Any]]:
        """
        Scan the workflows directory and return metadata for each workflow.

        Each entry is the frontmatter dict augmented with:
          - 'name'        (falls back to the stem of the filename)
          - 'description' (falls back to empty string)
          - 'requires'    (falls back to empty list)
          - 'file'        (absolute path string of the .md file)

        Files that cannot be read are silently skipped.
        """
        results: list[dict[str, Any]] = []
        for md_file in sorted(self._dir.glob("*.md")):
            try:
                text = md_file.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            meta, _ = self._parse_frontmatter(text)
            meta.setdefault("name", md_file.stem)
            meta.setdefault("description", "")
            meta.setdefault("requires", [])
            meta["file"] = str(md_file)
            results.append(meta)
        return results

    def load(self, name: str) -> str | None:
        """
        Return the full markdown content (including frontmatter) for the
        named workflow, or None if the file does not exist.
        """
        md_file = self._dir / f"{name}.md"
        if not md_file.exists():
            return None
        return md_file.read_text(encoding="utf-8", errors="replace")

    def check_requirements(self, name: str, available_skills: list[str]) -> bool:
        """
        Return True if every skill listed in the workflow's 'requires'
        frontmatter field is present in available_skills.

        Returns False if the workflow is not found or frontmatter is missing.
        """
        content = self.load(name)
        if content is None:
            return False
        meta, _ = self._parse_frontmatter(content)
        required: list[str] = meta.get("requires") or []
        available_set = set(available_skills)
        return all(skill in available_set for skill in required)

    def inject_prompt(self, name: str) -> str | None:
        """
        Return the workflow body text (frontmatter stripped) ready for
        injection into a system prompt, or None if the workflow is not found.
        """
        content = self.load(name)
        if content is None:
            return None
        _, body = self._parse_frontmatter(content)
        return body
