import json
import re
import uuid
import time
from pathlib import Path
from api import new_msg

SESSIONS_DIR = Path(__file__).parent / "sessions"


def ensure_dir():
    SESSIONS_DIR.mkdir(exist_ok=True)


def slug(text: str, max_len: int = 30) -> str:
    text = re.sub(r"[^\w\s-]", "", text.lower())
    text = re.sub(r"[\s_-]+", "-", text).strip("-")
    return text[:max_len]


def new_session(system_prompt: str = "") -> dict:
    sid = str(uuid.uuid4())[:8]
    ts  = time.strftime("%Y%m%d_%H%M%S")
    session = {
        "id":         sid,
        "created_at": ts,
        "title":      "New conversation",
        "filename":   None,
        "history":    [],
    }
    if system_prompt:
        session["history"].append(new_msg("system", system_prompt))
    return session


def save_session(session: dict):
    ensure_dir()
    if not session["filename"]:
        title_slug = slug(session["title"])
        session["filename"] = f"{session['created_at']}_{title_slug}.json"
    path = SESSIONS_DIR / session["filename"]
    path.write_text(json.dumps(session, indent=2, ensure_ascii=False), encoding="utf-8")


def load_session(filename: str) -> dict:
    path = SESSIONS_DIR / filename
    return json.loads(path.read_text(encoding="utf-8"))


def list_sessions() -> list:
    ensure_dir()
    files = sorted(SESSIONS_DIR.glob("*.json"), reverse=True)
    sessions = []
    for f in files:
        try:
            s = json.loads(f.read_text(encoding="utf-8"))
            sessions.append({
                "filename": f.name,
                "title":    s.get("title", f.name),
                "created":  s.get("created_at", ""),
                "turns":    sum(1 for m in s.get("history", []) if m["role"] == "user"),
            })
        except Exception:
            pass
    return sessions


def update_title(session: dict, first_user_msg: str):
    if session["title"] == "New conversation":
        session["title"] = first_user_msg[:40]
        title_slug = slug(session["title"])
        session["filename"] = f"{session['created_at']}_{title_slug}.json"
