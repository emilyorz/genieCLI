"""Tests for provider SSE parsing."""
import pytest
from genie.providers.base import parse_sse


def test_parse_sse_basic_content():
    raw = (
        'data: {"choices": [{"delta": {"content": "Hello"}}]}\n'
        'data: {"choices": [{"delta": {"content": " world"}}]}\n'
        "data: [DONE]\n"
    )
    result = parse_sse(raw)
    assert result == "Hello world"


def test_parse_sse_done_terminator():
    raw = (
        'data: {"choices": [{"delta": {"content": "test"}}]}\n'
        'data: {"done": true}\n'
    )
    result = parse_sse(raw)
    assert result == "test"


def test_parse_sse_falls_back_to_reasoning():
    raw = (
        'data: {"choices": [{"delta": {"reasoning_content": "thinking..."}}]}\n'
        "data: [DONE]\n"
    )
    result = parse_sse(raw)
    assert result == "thinking..."


def test_parse_sse_empty_stream():
    result = parse_sse("data: [DONE]\n")
    assert result == ""


def test_parse_sse_skips_non_data_lines():
    raw = (
        "event: message\n"
        'data: {"choices": [{"delta": {"content": "ok"}}]}\n'
        "id: 123\n"
        "data: [DONE]\n"
    )
    result = parse_sse(raw)
    assert result == "ok"
