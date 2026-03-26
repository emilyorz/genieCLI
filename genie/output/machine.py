"""MachineSink — JSON to stdout, errors to stderr, non-interactive."""
from __future__ import annotations

import json
import sys
from typing import Any


class MachineSink:
    """OutputSink implementation for machine-readable / piped output."""

    def progress(self, msg: str) -> None:
        # Swallowed — progress chatter is not useful in machine mode
        pass

    def result(self, data: Any) -> None:
        print(json.dumps(data, ensure_ascii=False, default=str))

    def stream(self, text: str) -> None:
        sys.stdout.write(text)
        sys.stdout.flush()

    def error(self, msg: str, code: int = 1) -> None:
        payload = json.dumps({"error": msg, "code": code}, ensure_ascii=False)
        print(payload, file=sys.stderr)

    def table(self, rows: list[dict], headers: list[str] | None = None) -> None:
        print(json.dumps(rows, ensure_ascii=False, default=str))

    def confirm(self, prompt: str) -> bool:
        # Non-interactive: always proceed
        return True

    def markdown(self, text: str) -> None:
        # Emit raw text; the consumer can render it however they like
        print(text)
