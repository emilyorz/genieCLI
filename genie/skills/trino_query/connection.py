"""Trino connection profiles — CLI-managed, interactive switching.

Profiles are stored in ~/.config/genie/trino.json.
Active profile is selected via CLI (/trino use <name>), not env vars.

Example trino.json:
{
  "active": "local",
  "profiles": {
    "local": {
      "host": "localhost",
      "port": 8085,
      "user": "trino",
      "scheme": "http",
      "catalog": "iceberg",
      "schema": "warehouse",
      "label": "Mac mini Docker"
    },
    "home-lab": {
      "host": "192.168.1.100",
      "port": 8080,
      "user": "trino",
      "scheme": "https",
      "catalog": "iceberg",
      "schema": "warehouse",
      "label": "Home Lab Trino"
    }
  }
}
"""
from __future__ import annotations

import json
from dataclasses import dataclass, asdict
from pathlib import Path

CONFIG_PATH = Path.home() / ".config" / "genie" / "trino.json"


@dataclass
class TrinoProfile:
    host: str = "localhost"
    port: int = 8085
    user: str = "trino"
    scheme: str = "http"
    catalog: str = "iceberg"
    schema: str = "warehouse"
    label: str = ""

    def connect(self, catalog: str | None = None, schema: str | None = None):
        """Create a trino.dbapi connection using this profile."""
        try:
            import trino.dbapi
        except ImportError:
            raise ImportError(
                "Trino Python driver not installed. Run: pip install trino"
            ) from None
        return trino.dbapi.connect(
            host=self.host,
            port=self.port,
            user=self.user,
            catalog=catalog or self.catalog,
            schema=schema or self.schema,
            http_scheme=self.scheme,
        )

    def display_name(self) -> str:
        label_part = f" ({self.label})" if self.label else ""
        return f"{self.scheme}://{self.host}:{self.port}{label_part}"


def _load_raw() -> dict:
    """Load raw JSON from config file."""
    if CONFIG_PATH.exists():
        try:
            with open(CONFIG_PATH) as f:
                return json.load(f)
        except Exception:
            pass
    return {"active": "local", "profiles": {}}


def _save_raw(data: dict) -> None:
    """Save raw JSON to config file."""
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_PATH, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def _ensure_default() -> dict:
    """Ensure at least the 'local' profile exists."""
    data = _load_raw()
    if "profiles" not in data:
        data["profiles"] = {}
    if "local" not in data["profiles"]:
        data["profiles"]["local"] = {
            "host": "localhost",
            "port": 8085,
            "user": "trino",
            "scheme": "http",
            "catalog": "iceberg",
            "schema": "warehouse",
            "label": "Mac mini Docker",
        }
    if "active" not in data or data["active"] not in data["profiles"]:
        data["active"] = "local"
    _save_raw(data)
    return data


# ── Public API ────────────────────────────────────────────────────────────────

def list_profiles() -> dict[str, TrinoProfile]:
    """Return all profiles as {name: TrinoProfile}."""
    data = _ensure_default()
    result = {}
    for name, cfg in data["profiles"].items():
        result[name] = TrinoProfile(**{k: cfg[k] for k in TrinoProfile.__dataclass_fields__ if k in cfg})
    return result


def get_active_name() -> str:
    """Return the name of the active profile."""
    data = _ensure_default()
    return data["active"]


def get_active_profile() -> TrinoProfile:
    """Return the active TrinoProfile."""
    data = _ensure_default()
    name = data["active"]
    cfg = data["profiles"].get(name, {})
    return TrinoProfile(**{k: cfg[k] for k in TrinoProfile.__dataclass_fields__ if k in cfg})


def set_active(name: str) -> bool:
    """Switch active profile. Returns True if profile exists."""
    data = _ensure_default()
    if name not in data["profiles"]:
        return False
    data["active"] = name
    _save_raw(data)
    return True


def add_profile(name: str, profile: TrinoProfile) -> None:
    """Add or update a profile."""
    data = _ensure_default()
    data["profiles"][name] = asdict(profile)
    _save_raw(data)


def remove_profile(name: str) -> bool:
    """Remove a profile. Cannot remove the active profile."""
    data = _ensure_default()
    if name == data["active"]:
        return False
    if name in data["profiles"]:
        del data["profiles"][name]
        _save_raw(data)
        return True
    return False


def status_line() -> str:
    """One-line status for CLI banner: 'Trino: local (http://localhost:8085)'"""
    name = get_active_name()
    profile = get_active_profile()
    return f"Trino : {name} → {profile.display_name()}"
