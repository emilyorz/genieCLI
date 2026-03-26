"""Shared test fixtures: FakeProvider and NullSink."""
from __future__ import annotations

from typing import Any, Iterator

import pytest

from genie.core.provider import CompletionRequest, Delta, ProviderCapabilities


class FakeProvider:
    """Mock provider with pre-configured responses."""

    def __init__(self, responses: list[str]) -> None:
        self.responses = list(responses)
        self.calls: list[CompletionRequest] = []

    @property
    def name(self) -> str:
        return "fake"

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities()

    def complete(self, req: CompletionRequest) -> Iterator[Delta]:
        self.calls.append(req)
        text = self.responses.pop(0) if self.responses else ""
        yield Delta(text=text, finish_reason="stop")

    def complete_text(self, req: CompletionRequest) -> str:
        return "".join(d.text for d in self.complete(req))


class NullSink:
    """OutputSink that captures results and discards everything else."""

    def __init__(self) -> None:
        self.captured: list[Any] = []
        self.errors: list[str] = []

    def progress(self, msg: str) -> None:
        pass

    def result(self, data: Any) -> None:
        self.captured.append(data)

    def stream(self, text: str) -> None:
        pass

    def error(self, msg: str, code: int = 1) -> None:
        self.errors.append(msg)

    def table(self, rows: list[dict], headers: list[str] | None = None) -> None:
        self.captured.append(rows)

    def confirm(self, prompt: str) -> bool:
        return True

    def markdown(self, text: str) -> None:
        self.captured.append(text)


@pytest.fixture
def fake_provider():
    return FakeProvider(["Hello, World!"])


@pytest.fixture
def null_sink():
    return NullSink()
