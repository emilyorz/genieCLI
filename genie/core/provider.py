"""Provider Protocol + data types for AI backend abstraction."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterator, Protocol, runtime_checkable


@dataclass
class Delta:
    text: str = ""
    finish_reason: str | None = None


@dataclass
class CompletionRequest:
    messages: list[dict]
    model: str
    tools: list[dict] | None = None
    files: list[dict] | None = None  # vision attachments: [{filename, content_type, data}]
    reasoning: str = "disable"       # tgenie-specific reasoning effort


@dataclass
class ProviderCapabilities:
    streaming: bool = True
    vision: bool = False
    tool_calls: bool = False


@runtime_checkable
class Provider(Protocol):
    @property
    def name(self) -> str: ...

    def complete(self, req: CompletionRequest) -> Iterator[Delta]: ...

    def complete_text(self, req: CompletionRequest) -> str:
        """Convenience wrapper: collect all Delta.text into a single string."""
        return "".join(d.text for d in self.complete(req))

    def capabilities(self) -> ProviderCapabilities: ...
