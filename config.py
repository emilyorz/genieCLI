import json
from pathlib import Path

CONFIG_PATH = Path.home() / "ai-agent-config.json"

DEFAULTS = {
    "endpoint":         "https://your-ai-gateway.internal.company.com",
    "frontendUrl":      "https://your-frontend.internal.company.com",
    "targetUrlKeyword": "ai-app",       # keyword to find your app tab in Chrome CDP
    "cookieDomain":     ".company.com",  # domain for cookie capture
    "authToken":        "",
    "customHeader":     "",
    "defaultModel":     "gemini-2.5-flash",
    "systemPrompt":     "You are a helpful AI assistant.",
    "cookies":          [],
}

def load():
    if CONFIG_PATH.exists():
        try:
            data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            return {**DEFAULTS, **data}   # DEFAULTS fills in any missing keys
        except Exception:
            pass
    return dict(DEFAULTS)

def save(cfg: dict):
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")
