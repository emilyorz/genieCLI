from __future__ import annotations
import: base64
from typing import Iterator
import: requests
from genie.core.provider import CompletionRequest, Delta, ProviderCapabilities
from genie.providers.base import parse_sse
from genie.providers.openai import _history_to_openai
function: def set_debug(enabled) -> None (anthropic.py:16)
function: def _dbg(*args) -> None (anthropic.py:21)
class: AnthropicProvider (anthropic.py:26)
  method: def __init__(self, cfg) -> None (anthropic.py:29)
  property: def name(self) -> str (anthropic.py:33)
  method: def capabilities(self) -> ProviderCapabilities (anthropic.py:36)
  method: def complete(self, req) -> Iterator[Delta] (anthropic.py:39)
  method: def complete_text(self, req) -> str (anthropic.py:43)
  method: def _call(self, req) -> str (anthropic.py:46)
from __future__ import annotations
import: json
import: re
function: def parse_sse(raw) -> str (base.py:8)
from __future__ import annotations
import: base64
from typing import Iterator
import: requests
from genie.core.provider import CompletionRequest, Delta, ProviderCapabilities
from genie.providers.base import parse_sse
function: def set_debug(enabled) -> None (openai.py:15)
function: def _dbg(*args) -> None (openai.py:20)
function: def _history_to_openai(history, content_as_array) -> list[dict] (openai.py:25)
class: OpenAIProvider (openai.py:39)
  method: def __init__(self, cfg) -> None (openai.py:42)
  property: def name(self) -> str (openai.py:46)
  method: def capabilities(self) -> ProviderCapabilities (openai.py:49)
  method: def complete(self, req) -> Iterator[Delta] (openai.py:52)
  method: def complete_text(self, req) -> str (openai.py:56)
  method: def _is_ollama(self) -> bool (openai.py:59)
  method: def _call(self, req) -> str (openai.py:64)
  method: def _call_ollama_native(self, req) -> str (openai.py:70)
  method: def _call_openai(self, req) -> str (openai.py:116)
from __future__ import annotations
import: subprocess
import: sys
import: uuid
from typing import Iterator
import: requests
import: urllib3
from genie.core.provider import CompletionRequest, Delta, ProviderCapabilities
from genie.providers.base import parse_sse
function: def set_debug(enabled) -> None (tgenie.py:20)
function: def _dbg(*args) -> None (tgenie.py:25)
class: TGenieProvider (tgenie.py:30)
  method: def __init__(self, cfg) -> None (tgenie.py:33)
  property: def name(self) -> str (tgenie.py:37)
  method: def capabilities(self) -> ProviderCapabilities (tgenie.py:40)
  method: def complete(self, req) -> Iterator[Delta] (tgenie.py:43)
  method: def complete_text(self, req) -> str (tgenie.py:47)
  method: def _call(self, req) -> str (tgenie.py:50)
  method: def _refresh_token(self) -> bool (tgenie.py:150)
