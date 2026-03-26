"""Config loading — supports legacy ~/ai-agent-config.json and new ~/.genie/config.toml.

Resolution order (highest → lowest priority):
    CLI flags (injected by caller)  >  env vars  >  config.toml  >  config.json  >  DEFAULTS
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

DEFAULTS: dict[str, Any] = {
    # ── TGenie backend ────────────────────────────────────────────────────────
    "endpoint":           "https://your-ai-gateway.internal.company.com",
    "frontendUrl":        "https://your-frontend.internal.company.com",
    "targetUrlKeyword":   "ai-app",
    "cookieDomain":       ".company.com",
    "authToken":          "",
    "customHeader":       "",
    "cookies":            [],
    # ── Interface ─────────────────────────────────────────────────────────────
    # "tgenie" | "openai" | "anthropic"
    "interface":          "tgenie",
    "openaiApiKey":       "",
    "openaiBaseUrl":      "https://api.openai.com/v1",
    "openaiContentArray": False,
    # ── Shared ────────────────────────────────────────────────────────────────
    "defaultModel":       "gemini-2.5-flash",
    "systemPrompt":       "You are a helpful AI assistant.",
}

# Legacy path (backward-compat)
_LEGACY_PATH = Path.home() / "ai-agent-config.json"
# New path
_TOML_PATH   = Path.home() / ".genie" / "config.toml"


def load(overrides: dict | None = None) -> dict:
    """Load config with full merge chain.

    ``overrides`` is typically populated from CLI flags — values that are not
    None replace the loaded config.
    """
    cfg = dict(DEFAULTS)

    # 1. JSON legacy
    _merge_json(cfg)

    # 2. TOML (takes priority over JSON)
    _merge_toml(cfg)

    # 3. Environment variables (prefix GENIE_)
    _merge_env(cfg)

    # 4. CLI overrides (None values are ignored)
    if overrides:
        for key, val in overrides.items():
            if val is not None:
                cfg[key] = val

    return cfg


def save(cfg: dict) -> None:
    """Persist to the legacy JSON path (backward-compat)."""
    _LEGACY_PATH.write_text(
        json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8"
    )


# ── Internal helpers ──────────────────────────────────────────────────────────

def _merge_json(cfg: dict) -> None:
    if not _LEGACY_PATH.exists():
        return
    try:
        data = json.loads(_LEGACY_PATH.read_text(encoding="utf-8"))
        cfg.update(data)
    except Exception:
        pass


def _merge_toml(cfg: dict) -> None:
    if not _TOML_PATH.exists():
        return
    try:
        import tomllib  # Python 3.11+
    except ImportError:
        try:
            import tomli as tomllib  # fallback for 3.10
        except ImportError:
            return
    try:
        data = tomllib.loads(_TOML_PATH.read_text(encoding="utf-8"))
        # Flatten top-level sections into a single dict
        for section_val in data.values():
            if isinstance(section_val, dict):
                cfg.update(section_val)
            # top-level scalar keys
        for k, v in data.items():
            if not isinstance(v, dict):
                cfg[k] = v
    except Exception:
        pass


def _merge_env(cfg: dict) -> None:
    """Map GENIE_<KEY> env vars (uppercased) → config keys (camelCase not supported here).

    Supports simple string overrides like GENIE_INTERFACE, GENIE_DEFAULT_MODEL, etc.
    """
    mapping = {
        "GENIE_INTERFACE":         "interface",
        "GENIE_OPENAI_API_KEY":    "openaiApiKey",
        "GENIE_OPENAI_BASE_URL":   "openaiBaseUrl",
        "GENIE_DEFAULT_MODEL":     "defaultModel",
        "GENIE_AUTH_TOKEN":        "authToken",
        "GENIE_ENDPOINT":          "endpoint",
    }
    for env_key, cfg_key in mapping.items():
        val = os.environ.get(env_key)
        if val is not None:
            cfg[cfg_key] = val
