---
covers:
  - "genie/session/*.py"
last_synced: "572f7ff30399bed1a1a3c230918ba037ae874272"
---

## Purpose

Owns conversation-history persistence for genieCLI. The module creates, saves, loads, and lists chat sessions as JSON files under the repo-relative `sessions/` directory. It also provides helpers for constructing individual message dicts and for deriving filesystem-safe title slugs. There is no network I/O; all state lives on disk.

## Exports

```
from __future__ import annotations
import: json
import: re
import: time
import: uuid
from pathlib import Path
function: def _ensure_dir() -> None (manager.py:13)
function: def new_msg(role, text) -> dict (manager.py:17)
function: def slug(text, max_len) -> str (manager.py:26)
function: def new_session(system_prompt) -> dict (manager.py:32)
function: def save_session(session) -> None (manager.py:48)
function: def load_session(filename) -> dict (manager.py:57)
function: def list_sessions() -> list[dict] (manager.py:65)
function: def update_title(session, first_user_msg) -> None (manager.py:83)
```

Annotations:
- `_ensure_dir` — creates `sessions/` if absent
- `new_msg` — builds a single message dict with uuid + millisecond timestamp
- `slug` — lowercases, strips non-word chars, collapses separators, truncates
- `new_session` — initialises session dict; optionally prepends a system message
- `save_session` — auto-derives filename from title slug on first save, writes JSON
- `load_session` — reads JSON, back-fills missing `redo_stack`
- `list_sessions` — returns metadata list sorted newest-first, skips corrupt files
- `update_title` — renames title+filename once first user turn is known

## Invariants

- `SESSIONS_DIR` is always `<repo-root>/sessions/` resolved at import time via `Path(__file__).parent.parent.parent / "sessions"` (manager.py:10). No config override exists.
- Every message dict produced by `new_msg` contains exactly the keys `id`, `role`, `content`, `timestamp`; `content` is a list with one element whose `reasonText` is `None` (manager.py:17-23).
- `save_session` mutates `session["filename"]` in-place when the field is falsy — callers must treat the dict as owned after the call (manager.py:50-52).
- `load_session` silently back-fills `redo_stack` to `[]` if the key is absent or non-list, providing forward-compatibility with older saved files (manager.py:60-61).
- `list_sessions` silently swallows any `Exception` raised while reading individual files, so a corrupt `.json` never prevents the rest of the listing from returning (manager.py:78-79).
- `update_title` only renames when the current title is still the literal string `"New conversation"`; subsequent calls are no-ops (manager.py:84).

## Change log

- 572f7ff30399bed1a1a3c230918ba037ae874272: initial card created
