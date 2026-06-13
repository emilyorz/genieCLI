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
