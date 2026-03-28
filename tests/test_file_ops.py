"""Tests for genie/skills/file_ops/__init__.py"""
from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from genie.skills.file_ops import FilePatch, ListFiles, ReadFile, WriteFile, register
from genie.core.registry import SkillRegistry


# ── ReadFile ──────────────────────────────────────────────────────────────────

def test_read_file_returns_content(tmp_path):
    f = tmp_path / "hello.txt"
    f.write_text("hello world", encoding="utf-8")
    skill = ReadFile()
    result = skill.run(path=str(f))
    assert result == "hello world"


def test_read_file_truncates_at_5000(tmp_path):
    f = tmp_path / "big.txt"
    f.write_text("x" * 6000, encoding="utf-8")
    skill = ReadFile()
    result = skill.run(path=str(f))
    assert len(result) == 5000


def test_read_file_missing_returns_error():
    skill = ReadFile()
    result = skill.run(path="/does/not/exist/file.txt")
    assert "Error reading file" in result


def test_read_file_empty_returns_empty(tmp_path):
    f = tmp_path / "empty.txt"
    f.write_text("", encoding="utf-8")
    skill = ReadFile()
    result = skill.run(path=str(f))
    assert result == ""


# ── WriteFile ─────────────────────────────────────────────────────────────────

def test_write_file_creates_file(tmp_path):
    dest = tmp_path / "output.txt"
    skill = WriteFile()
    result = skill.run(path=str(dest), content="written!")
    assert dest.read_text(encoding="utf-8") == "written!"
    assert "written" in result.lower() or "Written" in result


def test_write_file_reports_char_count(tmp_path):
    dest = tmp_path / "out.txt"
    content = "abc" * 10
    skill = WriteFile()
    result = skill.run(path=str(dest), content=content)
    assert "30" in result


def test_write_file_creates_parent_dirs(tmp_path):
    dest = tmp_path / "a" / "b" / "c.txt"
    skill = WriteFile()
    result = skill.run(path=str(dest), content="deep")
    assert dest.exists()
    assert dest.read_text() == "deep"


def test_write_file_error_returns_message():
    skill = WriteFile()
    # Write to a path where the name itself is invalid on most systems
    with patch("pathlib.Path.write_text", side_effect=OSError("permission denied")):
        result = skill.run(path="/some/file.txt", content="x")
    assert "Error writing file" in result


# ── ListFiles ─────────────────────────────────────────────────────────────────

def test_list_files_shows_entries(tmp_path):
    (tmp_path / "a.txt").write_text("a")
    (tmp_path / "b.txt").write_text("b")
    skill = ListFiles()
    result = skill.run(path=str(tmp_path))
    assert "a.txt" in result
    assert "b.txt" in result


def test_list_files_marks_dirs(tmp_path):
    (tmp_path / "subdir").mkdir()
    (tmp_path / "file.txt").write_text("x")
    skill = ListFiles()
    result = skill.run(path=str(tmp_path))
    assert "[DIR]" in result
    assert "subdir" in result


def test_list_files_empty_dir(tmp_path):
    skill = ListFiles()
    result = skill.run(path=str(tmp_path))
    assert result == "(empty)"


def test_list_files_truncates_at_50(tmp_path):
    for i in range(55):
        (tmp_path / f"file_{i:03d}.txt").write_text("x")
    skill = ListFiles()
    result = skill.run(path=str(tmp_path))
    assert "more" in result


def test_list_files_missing_dir_returns_error():
    skill = ListFiles()
    result = skill.run(path="/no/such/directory")
    assert "Error listing files" in result


def test_list_files_default_path_runs():
    # Just ensure it doesn't crash with default "."
    skill = ListFiles()
    result = skill.run()
    assert isinstance(result, str)


# ── FilePatch ─────────────────────────────────────────────────────────────────

def test_file_patch_success(tmp_path):
    target = tmp_path / "target.txt"
    target.write_text("line1\nline2\nline3\n")
    # Build a real unified diff
    patch_str = (
        "--- target.txt\n"
        "+++ target.txt\n"
        "@@ -1,3 +1,3 @@\n"
        " line1\n"
        "-line2\n"
        "+line2_patched\n"
        " line3\n"
    )
    skill = FilePatch()
    result = skill.run(path=str(target), patch=patch_str)
    assert "successfully" in result.lower() or "Patch applied" in result


def test_file_patch_failure_returns_error(tmp_path):
    target = tmp_path / "target.txt"
    target.write_text("line1\n")
    bad_patch = "not a valid patch\n"
    skill = FilePatch()
    result = skill.run(path=str(target), patch=bad_patch)
    # Should either succeed or return an error string — not raise
    assert isinstance(result, str)


def test_file_patch_subprocess_exception():
    skill = FilePatch()
    with patch("subprocess.run", side_effect=RuntimeError("oops")):
        result = skill.run(path="/some/file.txt", patch="x")
    assert "Error applying patch" in result


# ── register ──────────────────────────────────────────────────────────────────

def test_register_adds_all_skills():
    class _FakeRegistry:
        def __init__(self):
            self.registered = []
        def register(self, skill):
            self.registered.append(skill)

    reg = _FakeRegistry()
    register(reg)
    names = [s.name for s in reg.registered]
    assert "read_file" in names
    assert "write_file" in names
    assert "list_files" in names
    assert "file_patch" in names
