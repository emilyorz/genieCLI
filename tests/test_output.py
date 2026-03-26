"""Tests for output sinks."""
import json
import sys
from io import StringIO

import pytest
from tests.conftest import NullSink
from genie.output.machine import MachineSink


def test_null_sink_captures_result():
    sink = NullSink()
    sink.result("hello")
    assert sink.captured == ["hello"]


def test_null_sink_captures_markdown():
    sink = NullSink()
    sink.markdown("# Title")
    assert "# Title" in sink.captured


def test_null_sink_captures_errors():
    sink = NullSink()
    sink.error("something broke")
    assert "something broke" in sink.errors


def test_null_sink_confirm_always_true():
    sink = NullSink()
    assert sink.confirm("are you sure?") is True


def test_machine_sink_result_stdout(capsys):
    sink = MachineSink()
    sink.result({"key": "value"})
    out, _ = capsys.readouterr()
    data = json.loads(out.strip())
    assert data["key"] == "value"


def test_machine_sink_error_stderr(capsys):
    sink = MachineSink()
    sink.error("bad thing happened", code=2)
    _, err = capsys.readouterr()
    data = json.loads(err.strip())
    assert data["error"] == "bad thing happened"
    assert data["code"] == 2


def test_machine_sink_progress_is_silent(capsys):
    sink = MachineSink()
    sink.progress("working...")
    out, err = capsys.readouterr()
    assert out == ""
    assert err == ""


def test_machine_sink_confirm_always_true():
    sink = MachineSink()
    assert sink.confirm("proceed?") is True
