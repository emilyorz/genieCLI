"""Interactive setup wizards for GenieCLI configuration.

Usage:
    genie setup          # LLM backend setup
    genie setup trino    # Trino connection setup
    genie setup mcp      # MCP Trino server setup
"""
from __future__ import annotations

import json
from pathlib import Path

_TOML_PATH = Path.home() / ".genie" / "config.toml"
_TRINO_PATH = Path.home() / ".config" / "genie" / "trino.json"
_MCP_PATH = Path.home() / ".config" / "genie" / "mcp.json"


def _prompt(label: str, default: str = "") -> str:
    """Read a line with optional default shown in brackets."""
    suffix = f" [{default}]" if default else ""
    try:
        val = input(f"  {label}{suffix} > ").strip()
    except (EOFError, KeyboardInterrupt):
        print("\n  Cancelled.")
        raise SystemExit(0)
    return val or default


def _write_toml(data: dict[str, str]) -> None:
    """Write flat key=value pairs to config.toml."""
    _TOML_PATH.parent.mkdir(parents=True, exist_ok=True)

    # Read existing content to preserve comments and other keys
    existing: dict[str, str] = {}
    if _TOML_PATH.exists():
        for line in _TOML_PATH.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if "=" in line and not line.startswith("#"):
                key, _, val = line.partition("=")
                key = key.strip()
                val = val.strip().strip('"').strip("'")
                existing[key] = val

    existing.update(data)

    lines = []
    for key, val in existing.items():
        lines.append(f'{key} = "{val}"')

    _TOML_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"\n  Saved: {_TOML_PATH}")


def setup_llm() -> None:
    """Interactive LLM backend setup."""
    print("\n  === GenieCLI Setup: LLM Backend ===\n")

    print("  Choose a backend:")
    print("    1. Ollama (local LLM, free)")
    print("    2. OpenAI API")
    print("    3. Groq")
    print("    4. TGenie (internal gateway)")
    print("    5. Anthropic")
    print()

    choice = _prompt("Backend", "1")

    if choice == "1":
        model = _prompt("Model", "qwen3.5:4b")
        base_url = _prompt("Ollama URL", "http://localhost:11434/v1")
        _write_toml({
            "interface": "openai",
            "openaiApiKey": "ollama",
            "openaiBaseUrl": base_url,
            "defaultModel": model,
        })
    elif choice == "2":
        api_key = _prompt("OpenAI API key")
        model = _prompt("Model", "gpt-4o")
        _write_toml({
            "interface": "openai",
            "openaiApiKey": api_key,
            "openaiBaseUrl": "https://api.openai.com/v1",
            "defaultModel": model,
        })
    elif choice == "3":
        api_key = _prompt("Groq API key")
        model = _prompt("Model", "llama-3.3-70b-versatile")
        _write_toml({
            "interface": "openai",
            "openaiApiKey": api_key,
            "openaiBaseUrl": "https://api.groq.com/openai/v1",
            "defaultModel": model,
        })
    elif choice == "4":
        endpoint = _prompt("TGenie endpoint", "https://your-ai-gateway.internal.company.com")
        token = _prompt("Auth token")
        model = _prompt("Model", "gemini-2.5-flash")
        _write_toml({
            "interface": "tgenie",
            "endpoint": endpoint,
            "authToken": token,
            "defaultModel": model,
        })
    elif choice == "5":
        api_key = _prompt("Anthropic API key")
        model = _prompt("Model", "claude-sonnet-4-6")
        _write_toml({
            "interface": "anthropic",
            "openaiApiKey": api_key,
            "defaultModel": model,
        })
    else:
        print(f"  Unknown choice: {choice}")
        return

    print("  Done! Run `genie` to start chatting.\n")


def setup_trino() -> None:
    """Interactive Trino connection setup."""
    print("\n  === GenieCLI Setup: Trino Connection ===\n")

    # Load existing profiles
    profiles: dict = {}
    active = "default"
    if _TRINO_PATH.exists():
        try:
            data = json.loads(_TRINO_PATH.read_text(encoding="utf-8"))
            profiles = data.get("profiles", {})
            active = data.get("active", "default")
        except Exception:
            pass

    name = _prompt("Profile name", "default")
    host = _prompt("Host", "localhost")
    port = _prompt("Port", "8085")
    user = _prompt("User", "trino")
    scheme = _prompt("Scheme (http/https)", "http")
    catalog = _prompt("Catalog", "iceberg")
    schema = _prompt("Schema", "warehouse")
    label = _prompt("Label (optional)", "")

    profiles[name] = {
        "host": host,
        "port": int(port),
        "user": user,
        "scheme": scheme,
        "catalog": catalog,
        "schema": schema,
        "label": label,
    }

    _TRINO_PATH.parent.mkdir(parents=True, exist_ok=True)
    _TRINO_PATH.write_text(
        json.dumps({"active": name, "profiles": profiles}, indent=2),
        encoding="utf-8",
    )
    print(f"\n  Saved: {_TRINO_PATH}")
    print(f"  Active profile: {name} ({scheme}://{host}:{port})")
    print("  Test with: /trino test\n")


def setup_mcp() -> None:
    """Interactive MCP Trino server setup."""
    print("\n  === GenieCLI Setup: MCP Trino Server ===\n")

    url = _prompt("MCP Trino server URL", "http://localhost:8811")
    timeout = _prompt("Timeout (seconds)", "30")

    config = {
        "trino": {
            "url": url,
            "enabled": True,
            "timeout": int(timeout),
        }
    }

    _MCP_PATH.parent.mkdir(parents=True, exist_ok=True)
    _MCP_PATH.write_text(
        json.dumps(config, indent=2), encoding="utf-8",
    )
    print(f"\n  Saved: {_MCP_PATH}")
    print(f"  MCP Trino: {url} (timeout: {timeout}s)\n")
