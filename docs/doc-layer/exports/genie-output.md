from __future__ import annotations
from contextlib import contextmanager
import: threading
import: time
from typing import Any
from rich.console import Console
from rich.markdown import Markdown
from rich.markup import escape
from rich.table import Table
class: HumanSink (human.py:42)
  method: def progress(self, msg) -> None (human.py:47)
  method: def result(self, data) -> None (human.py:50)
  method: def stream(self, text) -> None (human.py:57)
  method: def error(self, msg, code) -> None (human.py:60)
  method: def table(self, rows, headers) -> None (human.py:63)
  method: def confirm(self, prompt) -> bool (human.py:80)
  method: def markdown(self, text) -> None (human.py:87)
  method: def print(self, msg) -> None (human.py:92)
  method: def rule(self, label) -> None (human.py:95)
  method: def kv(self, key, value) -> None (human.py:104)
  method: def tool_call(self, name, args) -> None (human.py:110)
  method: def tool_result(self, result) -> None (human.py:123)
  method: def status(self, message) (human.py:128)
from __future__ import annotations
import: json
import: sys
from typing import Any
class: MachineSink (machine.py:9)
  method: def progress(self, msg) -> None (machine.py:12)
  method: def result(self, data) -> None (machine.py:16)
  method: def stream(self, text) -> None (machine.py:19)
  method: def error(self, msg, code) -> None (machine.py:23)
  method: def table(self, rows, headers) -> None (machine.py:27)
  method: def confirm(self, prompt) -> bool (machine.py:30)
  method: def markdown(self, text) -> None (machine.py:34)
  method: def print(self, msg) -> None (machine.py:38)
  method: def tool_call(self, name, args) -> None (machine.py:46)
  method: def tool_result(self, result) -> None (machine.py:50)
  method: def status(self, message) (machine.py:54)
