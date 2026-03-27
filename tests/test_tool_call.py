"""Tests for tool call parsing."""
import pytest
from genie.core.tool_call import extract_memory, parse_tool_call


def test_parse_valid_tool_call():
    text = '{"memory": "doing stuff", "tool": "read_file", "args": {"path": "/tmp/foo.txt"}}'
    result = parse_tool_call(text)
    assert result is not None
    assert result["tool"] == "read_file"
    assert result["args"]["path"] == "/tmp/foo.txt"
    assert result["memory"] == "doing stuff"


def test_parse_strips_markdown_fence():
    text = '```json\n{"memory": "x", "tool": "git_status", "args": {}}\n```'
    result = parse_tool_call(text)
    assert result is not None
    assert result["tool"] == "git_status"


def test_parse_fixes_trailing_comma():
    text = '{"memory": "x", "tool": "list_files", "args": {"path": "."},}'
    result = parse_tool_call(text)
    assert result is not None
    assert result["tool"] == "list_files"


def test_parse_null_tool_returns_dict():
    text = '{"memory": "done", "tool": null, "args": {}}'
    result = parse_tool_call(text)
    assert result is not None
    assert result["tool"] is None


def test_parse_returns_none_on_empty():
    assert parse_tool_call("") is None
    assert parse_tool_call("just some text") is None
    assert parse_tool_call(None) is None


def test_parse_adds_missing_args():
    text = '{"tool": "git_status"}'
    result = parse_tool_call(text)
    assert result is not None
    assert result["args"] == {}
    assert result["memory"] == ""


def test_extract_memory():
    text = '{"memory": "made progress", "tool": "read_file", "args": {}}'
    assert extract_memory(text) == "made progress"


def test_extract_memory_returns_empty_on_plain_text():
    assert extract_memory("just talking") == ""
