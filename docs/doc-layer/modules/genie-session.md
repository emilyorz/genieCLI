---
covers:
  - "genie/session/*.py"
last_synced: "df1131522263a60bac2a7a0326499f43bc63c490"
---

## Purpose

Owns all conversation-history persistence for genieCLI. The module provides
a thin CRUD layer over JSON files stored in a `sessions/` directory at the
repo root. It is the single authoritative source for creating, loading,
saving, and listing chat sessions; nothing else writes to `sessions/*.json`.

## Exports

`manager.py` — all public symbols; `__init__.py` is empty (namespace only).

| Symbol | Signature | Description |
|---|---|---|
| `new_msg` | `(role: str, text: str) -> dict` | Build a single message dict with a UUID `id`, integer Unix epoch milliseconds `timestamp` (`int(time.time() * 1000)`), and a one-element `content` list. |
| `new_session` | `(system_prompt: str = "") -> dict` | Create an in-memory session dict (8-char UUID `id`, `created_at` timestamp, empty `history` / `redo_stack`). Appends a system message when `system_prompt` is non-empty. |
| `save_session` | `(session: dict) -> None` | Persist session to `sessions/<filename>.json`; derives `filename` from `created_at` + title slug on first save. |
| `load_session` | `(filename: str) -> dict` | Read and parse `sessions/<filename>`; back-fills `redo_stack: []` when missing (migration guard). |
| `list_sessions` | `() -> list[dict]` | Return summary dicts (`filename`, `title`, `created`, `turns`) for every `*.json` in `sessions/`, sorted newest-first; parse errors are silently skipped. |
| `update_title` | `(session: dict, first_user_msg: str) -> None` | Replaces the default `"New conversation"` title with the first 40 chars of the user message and renames `filename` to match. No-op if title was already changed. |
| `slug` | `(text: str, max_len: int = 30) -> str` | Lowercase, strip non-word chars, collapse whitespace/dash runs, truncate; used internally for `filename` derivation. |

## Invariants

- `SESSIONS_DIR` is resolved relative to `manager.py` at import time
  (`<repo_root>/sessions/`). Moving the package breaks the path.
- `save_session` mutates `session["filename"]` in place on the first call;
  callers must not assume the passed dict is unchanged.
- `load_session` does not validate the JSON schema beyond checking
  `redo_stack` type — callers own schema evolution.
- `list_sessions` silently drops any file it cannot parse; missing files do
  not raise.
- `update_title` is idempotent only when the title is still the sentinel
  `"New conversation"`. If called twice with different messages the second
  call is a no-op.
- No locking around file writes; concurrent `save_session` calls on the same
  session can corrupt the file on POSIX.

## Change log

- df1131522263a60bac2a7a0326499f43bc63c490: initial doc-layer card created
