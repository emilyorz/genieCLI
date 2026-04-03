"""Tests for genie/chat.py — screenshot tool chain."""
from __future__ import annotations

import base64
import json
from unittest.mock import MagicMock

import pytest

from genie.chat import _send_with_tools
from genie.core.registry import BaseSkill, SkillRegistry
from genie.session.manager import new_session
from tests.conftest import FakeProvider, NullSink


# ── Test doubles ──────────────────────────────────────────────────────────────

class _FakeScreenshot(BaseSkill):
    name = "browser_screenshot"
    description = "Take screenshot"
    args = []

    def run(self, filename="screenshot.png"):
        fake_png = base64.b64encode(b"\x89PNG\r\n").decode()
        return f"__SCREENSHOT__:{filename}:{fake_png}"


class _FakeTracker(BaseSkill):
    name = "tracker"
    description = "Track execution"
    args = []

    def __init__(self):
        self.calls: list[str] = []

    def run(self, **kwargs):
        self.calls.append("executed")
        return "tracked"


class _TrackingOutput(NullSink):
    def __init__(self):
        super().__init__()
        self.progress_calls: list[str] = []

    def progress(self, msg: str) -> None:
        self.progress_calls.append(msg)


@pytest.fixture(autouse=True)
def isolate_registry():
    SkillRegistry.clear()
    yield
    SkillRegistry.clear()


# ── Screenshot tool chain ─────────────────────────────────────────────────────

def test_screenshot_chain_executes_second_tool():
    """After screenshot, AI's follow-up tool call must be executed and announced.

    Regression for: screenshot branch called _run_tool_call(tool_call2) but
    never called output.tool_call / output.progress, making the second tool
    call invisible in the output stream.
    """
    tracker = _FakeTracker()
    SkillRegistry.register(_FakeScreenshot())
    SkillRegistry.register(tracker)

    screenshot_json = json.dumps({"tool": "browser_screenshot", "args": {}, "memory": ""})
    followup_json = json.dumps({"tool": "tracker", "args": {}, "memory": ""})
    final_response = "Task complete."

    provider = FakeProvider([screenshot_json, followup_json, final_response])
    session = new_session("")
    output = _TrackingOutput()
    ctx = MagicMock()

    _send_with_tools(provider, session, "test-model", "disable", output, ctx)

    # Second tool must be executed
    assert tracker.calls == ["executed"], "Follow-up tool after screenshot was not executed"

    # Second tool must be announced via output.progress (non-HumanSink path)
    assert any("tracker" in p for p in output.progress_calls), (
        f"output.progress('[Tool] tracker') was never called. Got: {output.progress_calls}"
    )


def test_screenshot_chain_normal_tool_result_returns_final_reply():
    """After screenshot + follow-up tool, loop continues until AI returns plain text."""
    tracker = _FakeTracker()
    SkillRegistry.register(_FakeScreenshot())
    SkillRegistry.register(tracker)

    screenshot_json = json.dumps({"tool": "browser_screenshot", "args": {}, "memory": ""})
    followup_json = json.dumps({"tool": "tracker", "args": {}, "memory": ""})
    final_response = "Analysis complete."

    provider = FakeProvider([screenshot_json, followup_json, final_response])
    session = new_session("")
    output = _TrackingOutput()
    ctx = MagicMock()

    result = _send_with_tools(provider, session, "test-model", "disable", output, ctx)

    assert result == "Analysis complete."
    assert tracker.calls == ["executed"]
