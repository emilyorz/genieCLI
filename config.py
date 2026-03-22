import json
from pathlib import Path

CONFIG_PATH = Path.home() / "ai-agent-config.json"

DEFAULTS = {
    "endpoint":     "https://tgenie3-main.mlp.tw.ent.tsmc.com",
    "frontendUrl":  "https://tgenie3.tgenie.mlp.tw.ent.tsmc.com",
    "authToken":    "",
    "customHeader": "",
    "defaultModel": "gemini-2.5-flash",
    "systemPrompt": "You are a helpful AI assistant.",
    "cookies":      [],
}

def load():
    if CONFIG_PATH.exists():
        try:
            return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    return dict(DEFAULTS)

def save(cfg: dict):
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")
