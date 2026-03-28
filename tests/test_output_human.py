"""Tests for genie/output/human.py"""
from __future__ import annotations

from unittest.mock import MagicMock, call, patch

import pytest

from genie.output.human import HumanSink


# ── progress ──────────────────────────────────────────────────────────────────

def test_progress_calls_console_print():
    sink = HumanSink()
    with patch("genie.output.human._console") as mock_console:
        sink.progress("loading...")
    mock_console.print.assert_called_once()
    printed = mock_console.print.call_args[0][0]
    assert "loading..." in printed


# ── result ────────────────────────────────────────────────────────────────────

def test_result_string():
    sink = HumanSink()
    with patch("genie.output.human._console") as mock_console:
        sink.result("hello world")
    mock_console.print.assert_called_once_with("hello world")


def test_result_dict_serializes_to_json():
    sink = HumanSink()
    with patch("genie.output.human._console") as mock_console:
        sink.result({"key": "value"})
    args = mock_console.print.call_args[0][0]
    assert '"key"' in args
    assert '"value"' in args


def test_result_list_serializes_to_json():
    sink = HumanSink()
    with patch("genie.output.human._console") as mock_console:
        sink.result([1, 2, 3])
    args = mock_console.print.call_args[0][0]
    assert "1" in args


# ── stream ────────────────────────────────────────────────────────────────────

def test_stream_prints_without_newline():
    sink = HumanSink()
    with patch("genie.output.human._console") as mock_console:
        sink.stream("partial text")
    mock_console.print.assert_called_once_with("partial text", end="")


# ── error ─────────────────────────────────────────────────────────────────────

def test_error_includes_message():
    sink = HumanSink()
    with patch("genie.output.human._console") as mock_console:
        sink.error("something went wrong")
    printed = mock_console.print.call_args[0][0]
    assert "something went wrong" in printed


def test_error_includes_error_tag():
    sink = HumanSink()
    with patch("genie.output.human._console") as mock_console:
        sink.error("fail", code=2)
    printed = mock_console.print.call_args[0][0]
    assert "ERROR" in printed


# ── table ─────────────────────────────────────────────────────────────────────

def test_table_empty_rows():
    sink = HumanSink()
    with patch("genie.output.human._console") as mock_console:
        sink.table([])
    mock_console.print.assert_called_once()
    printed = mock_console.print.call_args[0][0]
    assert "empty" in printed


def test_table_with_rows():
    sink = HumanSink()
    with patch("genie.output.human._console") as mock_console:
        sink.table([{"name": "Alice", "age": "30"}, {"name": "Bob", "age": "25"}])
    mock_console.print.assert_called_once()
    # Should have printed a Table object
    args = mock_console.print.call_args[0][0]
    from rich.table import Table
    assert isinstance(args, Table)


def test_table_custom_headers():
    sink = HumanSink()
    with patch("genie.output.human._console") as mock_console:
        sink.table([{"x": "1"}], headers=["x"])
    mock_console.print.assert_called_once()


# ── confirm ───────────────────────────────────────────────────────────────────

def test_confirm_yes():
    sink = HumanSink()
    with patch("builtins.input", return_value="y"):
        assert sink.confirm("Are you sure?") is True


def test_confirm_yes_full():
    sink = HumanSink()
    with patch("builtins.input", return_value="yes"):
        assert sink.confirm("Continue?") is True


def test_confirm_no():
    sink = HumanSink()
    with patch("builtins.input", return_value="n"):
        assert sink.confirm("Delete?") is False


def test_confirm_eof_returns_false():
    sink = HumanSink()
    with patch("builtins.input", side_effect=EOFError):
        assert sink.confirm("prompt") is False


def test_confirm_keyboard_interrupt_returns_false():
    sink = HumanSink()
    with patch("builtins.input", side_effect=KeyboardInterrupt):
        assert sink.confirm("prompt") is False


# ── markdown ──────────────────────────────────────────────────────────────────

def test_markdown_prints_markdown_object():
    sink = HumanSink()
    with patch("genie.output.human._console") as mock_console:
        sink.markdown("# Hello\nworld")
    mock_console.print.assert_called_once()
    from rich.markdown import Markdown
    assert isinstance(mock_console.print.call_args[0][0], Markdown)


# ── print ─────────────────────────────────────────────────────────────────────

def test_print_calls_console_print():
    sink = HumanSink()
    with patch("genie.output.human._console") as mock_console:
        sink.print("hello!")
    mock_console.print.assert_called_once_with("hello!")


# ── tool_call ─────────────────────────────────────────────────────────────────

def test_tool_call_with_args():
    sink = HumanSink()
    with patch("genie.output.human._console") as mock_console:
        sink.tool_call("my_tool", {"key": "val"})
    printed = mock_console.print.call_args[0][0]
    assert "my_tool" in printed
    assert "key" in printed


def test_tool_call_no_args():
    sink = HumanSink()
    with patch("genie.output.human._console") as mock_console:
        sink.tool_call("my_tool", {})
    printed = mock_console.print.call_args[0][0]
    assert "my_tool" in printed


# ── tool_result ───────────────────────────────────────────────────────────────

def test_tool_result_short():
    sink = HumanSink()
    with patch("genie.output.human._console") as mock_console:
        sink.tool_result("ok response")
    printed = mock_console.print.call_args[0][0]
    assert "ok response" in printed


def test_tool_result_truncates_at_100():
    sink = HumanSink()
    with patch("genie.output.human._console") as mock_console:
        sink.tool_result("x" * 200)
    printed = mock_console.print.call_args[0][0]
    assert "..." in printed
