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

    def print(self, msg: str) -> None:
        # In machine mode, print goes to stderr (not data output)
        import re
        # Strip Rich markup tags for machine output
        clean = re.sub(r"\[/?[^\]]*\]", "", msg).strip()
        if clean:
            print(clean, file=sys.stderr)

    def tool_call(self, name: str, args: dict) -> None:
        payload = json.dumps({"event": "tool_call", "tool": name, "args": args}, ensure_ascii=False)
        print(payload, file=sys.stderr)

    def tool_result(self, result: str) -> None:
        # Swallowed in machine mode — result is part of the tool loop, not user output
        pass
